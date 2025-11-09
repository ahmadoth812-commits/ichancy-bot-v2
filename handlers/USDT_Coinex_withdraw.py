# handlers/coinex_withdraw.py
import logging
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    ConversationHandler,
    CallbackContext,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
import store
import services.transaction_notification_service as tns
import utils.coinex_adapter as coinex_adapter
from decimal import Decimal, ROUND_DOWN

logger = logging.getLogger(__name__)

# Conversation states
CHOOSING_NETWORK, ENTER_ADDRESS, ENTER_AMOUNT, CONFIRMATION = range(4)

# Helper keyboards
def network_keyboard():
    kb = [
        [InlineKeyboardButton("BEP20", callback_data="coinex_net:BEP20"),
         InlineKeyboardButton("TRC20", callback_data="coinex_net:TRC20")],
        [InlineKeyboardButton("الغاء", callback_data="coinex_cancel")]
    ]
    return InlineKeyboardMarkup(kb)

def confirm_keyboard():
    kb = [
        [InlineKeyboardButton("تأكيد الطلب", callback_data="coinex_confirm")],
        [InlineKeyboardButton("إلغاء", callback_data="coinex_cancel")]
    ]
    return InlineKeyboardMarkup(kb)

# Entry point: either command or callback from menu
async def start_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start flow: ask network."""
    await update.effective_chat.send_message(
        "اختر الشبكة التي تريد السحب عبرها:", reply_markup=network_keyboard()
    )
    return CHOOSING_NETWORK

async def network_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    if data == "coinex_cancel":
        await q.edit_message_text("تم إلغاء العملية.", reply_markup=None)
        return ConversationHandler.END

    _, net = data.split(":")
    context.user_data["coinex_network"] = net
    await q.edit_message_text(f"تم اختيار الشبكة: {net}\n\nأرسل عنوان المحفظة (address) لاستلام USDT على {net}:", reply_markup=None)
    return ENTER_ADDRESS

async def address_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    addr = update.message.text.strip()
    context.user_data["withdraw_address"] = addr

    # Check whitelist locally
    telegram_id = str(user.id)
    addr_record = store.get_withdraw_address_by_user_and_addr(telegram_id, addr, context.user_data["coinex_network"])
    if not addr_record:
        # create pending address request
        req_id = store.insert_withdraw_address_request(telegram_id, addr, context.user_data["coinex_network"])
        # notify admin to approve address (via transaction_notification_service)
        await tns.notify_admin_new_withdraw_address_request(req_id, telegram_id, addr, context.user_data["coinex_network"])
        await update.message.reply_text(
            "🔐 هذا العنوان غير مسجل لدى النظام. تم إرسال طلب إلى الإدارة لإعتماده. "
            "حالما توافق الإدارة يمكنك المتابعة بطلب سحب جديد أو الانتظار."
        )
        # end flow for now
        return ConversationHandler.END

    await update.message.reply_text("أدخل المبلغ الذي تريد سحبه (بالـ NSP):", reply_markup=ReplyKeyboardRemove())
    return ENTER_AMOUNT

async def amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    telegram_id = str(user.id)
    txt = update.message.text.strip()
    try:
        nsp_amount = Decimal(txt)
    except Exception:
        await update.message.reply_text("الرجاء إدخال رقم صحيح للمبلغ (قيمة عددية). جرب مرة أخرى:")
        return ENTER_AMOUNT

    # basic minimum: 10 USD equivalent -> must use admin exchange rate
    exchange_rate = store.get_exchange_rate_usdt_to_nsp()  # admin-defined: 1 USDT = X NSP
    if not exchange_rate:
        await update.message.reply_text("⚠️ لم يتم إعداد سعر الصرف من قبل الإدارة بعد، تواصل مع الإدارة.")
        return ConversationHandler.END

    # convert NSP -> USDT (divide)
    usdt_amount = (nsp_amount / Decimal(exchange_rate)).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)

    # fee 10%
    fee = (usdt_amount * Decimal("0.10")).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    final_usdt = (usdt_amount - fee).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)

    # check user's NSP balance
    user_db = store.getUserByTelegramId(telegram_id)
    if not user_db:
        await update.message.reply_text("لم يتم العثور على حسابك في النظام. الرجاء التسجيل أولًا.")
        return ConversationHandler.END

    user_balance_nsp = Decimal(store.get_user_balance(user_db[0]))  # store.get_user_balance expects id
    # Note: store.get_user_balance returns value in DB; ensure it returns numeric
    if user_balance_nsp < nsp_amount:
        await update.message.reply_text("رصيدك غير كافٍ لإتمام السحب. الرجاء شحن حسابك أولاً.")
        return ConversationHandler.END

    # Save context
    context.user_data["nsp_amount"] = str(nsp_amount)
    context.user_data["usdt_amount"] = str(usdt_amount)
    context.user_data["fee"] = str(fee)
    context.user_data["final_usdt"] = str(final_usdt)

    summary = (
        f"مراجعة طلب السحب:\n\n"
        f"المبلغ بالـ NSP: {nsp_amount}\n"
        f"المعادل بالـ USDT: {usdt_amount}\n"
        f"العمولة (10%): {fee} USDT\n"
        f"المبلغ النهائي للإرسال: {final_usdt} USDT\n"
        f"الشبكة: {context.user_data.get('coinex_network')}\n"
        f"العنوان: {context.user_data.get('withdraw_address')}\n\n"
        "اضغط تأكيد لإرسال الطلب إلى الإدارة للمراجعة."
    )

    await update.message.reply_text(summary, reply_markup=confirm_keyboard())
    return CONFIRMATION

async def confirmation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "coinex_cancel":
        await q.edit_message_text("تم إلغاء الطلب.", reply_markup=None)
        return ConversationHandler.END

    if q.data != "coinex_confirm":
        await q.answer()
        return CONFIRMATION

    # persist withdrawal request
    telegram_id = str(q.from_user.id)
    user_db = store.getUserByTelegramId(telegram_id)
    user_id = user_db.get('id') if user_db else None

    if not user_id:
        await q.edit_message_text("حصل خطأ: لم يتم العثور على المستخدم في قاعدة البيانات.", reply_markup=None)
        return ConversationHandler.END

    # insert into coinex_withdrawals table
    provider_id = store.insert_coinex_withdrawal(
        user_id=user_id,
        address=context.user_data["withdraw_address"],
        network=context.user_data["coinex_network"],
        nsp_amount=context.user_data["nsp_amount"],
        usdt_amount=context.user_data["usdt_amount"],
        fee=context.user_data["fee"],
        final_usdt=context.user_data["final_usdt"],
        status="pending_admin_review"
    )

    # reduce user balance immediately (put on hold) or mark reserved - here we'll decrease balance
    new_balance = Decimal(store.get_user_balance(user_id)) - Decimal(context.user_data["nsp_amount"])
    store.insertNewBalance(q.from_user.id, int(new_balance))  # adapt as your DB expects ints

    # notify admin
    await tns.notify_admin_new_coinex_withdraw(provider_id, telegram_id)

    await q.edit_message_text(
        "✅ تم إرسال طلب السحب إلى الإدارة للمراجعة. ستحصل على إشعار عند القبول أو الرفض.",
        reply_markup=None
    )

    # cleanup
    context.user_data.pop("withdraw_address", None)
    context.user_data.pop("coinex_network", None)
    context.user_data.pop("nsp_amount", None)
    context.user_data.pop("usdt_amount", None)
    context.user_data.pop("fee", None)
    context.user_data.pop("final_usdt", None)

    return ConversationHandler.END

# Admin actions: approve / reject (callback data will include withdrawal id)
async def admin_approve_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data  # format: coinex_withdraw:approve:<withdrawal_id>
    try:
        _, action, wid = data.split(":")
    except Exception:
        await q.edit_message_text("Invalid action")
        return

    if action == "approve":
        # Load withdrawal
        withdraw = store.get_coinex_withdrawal_by_id(wid)
        if not withdraw:
            await q.edit_message_text("طلب السحب غير موجود.")
            return

        # Call coinex adapter to perform withdrawal
        chain = withdraw.get("network")
        address = withdraw.get("address")
        amount = withdraw.get("final_usdt")  # USDT final amount
        # adapter should return dict: {"ok": True, "txid": "..."} or {"ok": False, "error": "..."}
        result = coinex_adapter.withdraw(
            chain=chain,
            address=address,
            amount=Decimal(amount)
        )

        if result.get("ok"):
            txid = result.get("txid")
            store.update_coinex_withdrawal_status(wid, "completed", txid=txid)
            # notify user
            user_telegram_id = store.getTelegramIdByUserId(withdraw.get("user_id"))
            await context.bot.send_message(
                chat_id=int(user_telegram_id),
                text=(
                    f"✅ تم تنفيذ طلب السحب بنجاح\n"
                    f"المبلغ: {withdraw.get('final_usdt')} USDT\n"
                    f"المعرف: {txid}"
                )
            )
            await q.edit_message_text(f"تم تنفيذ السحب بنجاح. TXID: {txid}")
        else:
            error = result.get("error", "Unknown error")
            store.update_coinex_withdrawal_status(wid, "failed", note=error)
            await q.edit_message_text(f"فشل تنفيذ السحب: {error}")

    elif action == "reject":
        # format: coinex_withdraw:reject:<id>:<reason_base64_or_short>
        parts = data.split(":", 3)
        reason = parts[3] if len(parts) > 3 else None
        store.update_coinex_withdrawal_status(wid, "rejected", note=reason)
        user_telegram_id = store.getTelegramIdByUserId(withdraw.get("user_id"))
        await context.bot.send_message(chat_id=int(user_telegram_id), text="❌ تم رفض طلب سحبك من قبل الإدارة.")
        await q.edit_message_text("تم رفض الطلب.")

# Conversation handler factory
def conversation_handler():
    conv = ConversationHandler(
        entry_points=[CommandHandler("withdraw_coinex", start_withdraw)],
        states={
            CHOOSING_NETWORK: [CallbackQueryHandler(network_chosen, pattern=r"^coinex_net:")],
            ENTER_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, address_received)],
            ENTER_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, amount_received)],
            CONFIRMATION: [CallbackQueryHandler(confirmation_handler, pattern=r"^coinex_")],
        },
        fallbacks=[CallbackQueryHandler(lambda u,c: (c and c), pattern="^coinex_cancel$")],
        allow_reentry=True,
        persistent=True
    )
    return conv

# Register admin callbacks patterns for approve/reject
def admin_callbacks(dispatcher):
    dispatcher.add_handler(CallbackQueryHandler(admin_approve_withdraw, pattern=r"^coinex_withdraw:(approve|reject):\d+"))
