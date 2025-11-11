import logging
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, MessageHandler, ConversationHandler, ContextTypes, filters, CommandHandler
import store, config
from services.coinex_adapter import get_coinex_client # Use the global client function
from utils.notifications import notify_user, notify_admin

logger = logging.getLogger(__name__)

# Conversation states
AMOUNT, CHAIN, ADDRESS, CONFIRM, REJECT_REASON = range(5) # Added REJECT_REASON state

def _fmt_nsp(n):
    return f"{int(n):,} NSP"

# ========== USER FLOW ==========

async def start_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text( # Using edit_message_text
        f"💸 سحب عبر CoinEx\n"
        f"الحد الأدنى للسحب: {_fmt_nsp(config.COINEX_MIN_WITHDRAW_NSP)}\n"
        f"الرجاء إدخال المبلغ بالـ NSP:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]])
    )
    return AMOUNT


async def ask_chain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask user to choose withdrawal chain (BEP20 / TRC20)"""
    try:
        amount = int(update.message.text.strip().replace(",", ""))
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صالح.")
        return AMOUNT

    if amount < config.COINEX_MIN_WITHDRAW_NSP:
        await update.message.reply_text(f"⚠️ الحد الأدنى للسحب هو {_fmt_nsp(config.COINEX_MIN_WITHDRAW_NSP)}.")
        return AMOUNT

    user_telegram_id = str(update.effective_user.id)
    user = store.get_user_by_telegram_id(user_telegram_id)
    if not user:
        await update.message.reply_text("⚠️ حسابك غير مسجل.")
        context.user_data.clear()
        return ConversationHandler.END

    balance = store.get_user_balance(user["id"])
    if amount > balance:
        await update.message.reply_text(f"🚫 لا يوجد رصيد كافٍ. رصيدك الحالي: {_fmt_nsp(balance)}.")
        return ConversationHandler.END

    context.user_data["amount_nsp"] = amount
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 BEP20", callback_data="chain_bep20"),
         InlineKeyboardButton("🔵 TRC20", callback_data="chain_trc20")],
        [InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]
    ])
    await update.message.reply_text("🌐 اختر السلسلة المراد السحب عليها:", reply_markup=kb)
    return CHAIN


async def ask_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chain = "BEP20" if "bep20" in q.data else "TRC20"
    context.user_data["chain"] = chain
    await q.edit_message_text("📩 أدخل عنوان محفظة USDT المراد السحب إليها:",
                              reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]])
                             )
    return ADDRESS


async def confirm_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User enters withdrawal address and confirms"""
    address = update.message.text.strip()
    context.user_data["address"] = address
    amount_nsp = context.user_data["amount_nsp"]

    # ✅ تحقق من أن العنوان موجود في الـ whitelist
    # This function is assumed to be in store.py and checks a DB table.
    if not store.is_coinex_address_whitelisted(address):
        await update.message.reply_text(
            "⚠️ هذا العنوان غير مسجل في قائمة العناوين الموثوقة.\n"
            "يرجى التواصل مع الإدارة لإضافته قبل طلب السحب."
        )
        context.user_data.clear()
        return ConversationHandler.END

    # تحويل NSP → USDT
    rate = store.get_usd_to_nsp_rate()
    if not rate or rate <= 0:
        await update.message.reply_text("⚠️ سعر التحويل غير متوفر حالياً. يرجى المحاولة لاحقاً.")
        context.user_data.clear()
        return ConversationHandler.END

    usdt_amount = float("{:.6f}".format(amount_nsp / rate))
    chain = context.user_data["chain"]

    summary = (
        f"📋 **ملخص طلب السحب:**\n\n"
        f"💰 المبلغ (NSP): {_fmt_nsp(amount_nsp)}\n"
        f"💵 ما يعادله (USDT): {usdt_amount}\n"
        f"🔗 الشبكة: {chain}\n"
        f"🏦 العنوان: `{address}`\n\n"
        f"هل ترغب في إرسال الطلب للإدارة؟"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد", callback_data="withdraw_send")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="withdraw_cancel")]
    ])
    await update.message.reply_text(summary, reply_markup=kb, parse_mode="Markdown")
    return CONFIRM


async def submit_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User confirms and request is stored pending admin review"""
    q = update.callback_query
    await q.answer()

    if q.data == "withdraw_cancel":
        await q.edit_message_text("❎ تم إلغاء العملية.")
        context.user_data.clear()
        return ConversationHandler.END

    user_telegram_id = str(q.from_user.id)
    user = store.get_user_by_telegram_id(user_telegram_id)
    if not user:
        await q.edit_message_text("⚠️ حسابك غير مسجل.")
        context.user_data.clear()
        return ConversationHandler.END

    amount_nsp = context.user_data["amount_nsp"]
    chain = context.user_data["chain"]
    address = context.user_data["address"]

    # خصم الرصيد (تجميد مؤقت حتى المراجعة)
    store.deduct_balance(user["id"], amount_nsp)

    # تحويل القيمة إلى USDT
    rate = store.get_usd_to_nsp_rate()
    usdt_amount = float("{:.6f}".format(amount_nsp / rate))

    # حفظ الطلب في قاعدة البيانات
    wid = store._execute_query("""
        INSERT INTO coinex_withdrawals (user_id, nsp_amount, usdt_amount, chain, address, status, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (user["id"], amount_nsp, usdt_amount, chain, address, "pending", datetime.now()), fetchone=False) # returns lastrowid
    
    # Store general transaction if needed, but coinex_withdrawals is specific enough
    # store._execute_query("""
    #     INSERT INTO transactions (user_id, provider_id, provider_type, value, action_type)
    #     VALUES (%s,%s,%s,%s,%s)
    # """, (user["id"], wid, "coinex", amount_nsp, "withdraw"))

    if wid:
        store.add_audit_log("coinex_withdrawals", wid, "pending", actor=f"user_{user_telegram_id}", reason="User submitted withdrawal request")

        await q.edit_message_text("✅ تم تسجيل طلب السحب بنجاح، بانتظار موافقة الإدارة.")
        context.user_data.clear()

        # إشعار الأدمن
        msg = (
            f"🔔 **طلب سحب جديد عبر CoinEx**\n\n"
            f"👤 المستخدم: <a href='tg://user?id={user_telegram_id}'>@{q.from_user.username or q.from_user.full_name}</a>\n"
            f"💰 NSP: {_fmt_nsp(amount_nsp)} → USDT: {usdt_amount}\n"
            f"🔗 الشبكة: {chain}\n"
            f"🏦 العنوان: `{address}`\n"
            f"🆔 رقم العملية: {wid}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ موافقة وتنفيذ آلي", callback_data=f"admin_coinex_approve:{wid}")],
            [InlineKeyboardButton("❌ رفض", callback_data=f"admin_coinex_reject:{wid}")]
        ])
        await notify_admin(msg, reply_markup=kb, parse_mode="Markdown")
    else:
        await q.edit_message_text("❌ حدث خطأ في تسجيل طلب السحب بقاعدة البيانات.")
        context.user_data.clear()

    return ConversationHandler.END

# ========== ADMIN FLOW ==========

async def admin_approve_coinex_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin approves and triggers automatic CoinEx withdrawal"""
    q = update.callback_query
    await q.answer("جاري معالجة السحب عبر CoinEx...")
    if int(q.from_user.id) not in config.ADMIN_IDS:
        return await q.answer("❌ غير مصرح.")

    wid = int(q.data.split(":")[1])
    tx = store.get_transaction("coinex_withdrawals", wid)
    if not tx or tx["status"] != "pending":
        return await q.answer("⚠️ العملية غير موجودة أو تمت معالجتها.")

    # Ensure status is not already updated by another admin action
    if tx["status"] == "approved":
        return await q.answer("✅ العملية تمت الموافقة عليها بالفعل.")
    if tx["status"] == "rejected":
        return await q.answer("🚫 العملية تم رفضها بالفعل.")

    client = get_coinex_client()
    try:
        res = await client.withdraw_coinex(
            coin="USDT",
            to_address=tx["address"],
            amount=float(tx["usdt_amount"]),
            chain=tx["chain"]
        )

        if res.get("code") == 0 and res.get("data"):
            # CoinEx API might return different keys for transaction ID
            coinex_txid = res["data"].get("id") or res["data"].get("withdraw_id") or res["data"].get("order_id")
            if coinex_txid:
                store.update_transaction_status("coinex_withdrawals", wid, "approved", txid_external=str(coinex_txid), approved_at=datetime.now())
                store.add_audit_log("coinex_withdrawals", wid, "approved", actor=f"admin_{q.from_user.id}", reason=f"Executed via API, CoinEx TxID: {coinex_txid}")

                user_telegram_id = store.get_user_telegram_by_id(tx["user_id"])
                if user_telegram_id:
                    await notify_user(user_telegram_id, f"✅ تمت معالجة سحبك #{wid}.\n🆔 معرف تحويل CoinEx: `{coinex_txid}`", parse_mode="Markdown")

                await q.edit_message_text(f"✅ تم تنفيذ السحب آليًا.\nمعرف تحويل CoinEx: `{coinex_txid}`", parse_mode="Markdown")
            else:
                store.update_transaction_status("coinex_withdrawals", wid, "error", reason=f"CoinEx API success, but no TxID: {res}", approved_at=datetime.now())
                store.add_audit_log("coinex_withdrawals", wid, "error", actor=f"admin_{q.from_user.id}", reason=f"API success, no TxID: {res}")
                await q.edit_message_text(f"❌ تم السحب بنجاح ولكن تعذر الحصول على معرف التحويل.\nالرجاء التحقق يدوياً. الاستجابة: {res}")
        else:
            error_msg = res.get("message") or res.get("error_desc") or str(res)
            store.update_transaction_status("coinex_withdrawals", wid, "failed", reason=f"CoinEx API error: {error_msg}")
            store.add_audit_log("coinex_withdrawals", wid, "failed", actor=f"admin_{q.from_user.id}", reason=f"API error: {error_msg}")
            await q.edit_message_text(f"❌ فشل تنفيذ السحب عبر CoinEx API.\nالخطأ: {error_msg}")
            # Revert user balance if withdrawal failed and was pre-deducted
            # store.add_balance(tx["user_id"], tx["nsp_amount"]) # Uncomment if balance was pre-deducted and needs to be returned
    except Exception as e:
        logger.error(f"Error executing CoinEx withdrawal via API for TX {wid}: {e}")
        store.update_transaction_status("coinex_withdrawals", wid, "error", reason=f"Internal error: {e}")
        store.add_audit_log("coinex_withdrawals", wid, "error", actor=f"admin_{q.from_user.id}", reason=f"Internal error: {e}")
        await q.edit_message_text(f"❌ حدث خطأ داخلي أثناء محاولة تنفيذ السحب لـ #{wid}.")


async def admin_reject_coinex_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if int(q.from_user.id) not in config.ADMIN_IDS:
        return await q.answer("❌ غير مصرح.")
    wid = int(q.data.split(":")[1])
    context.user_data["reject_wid"] = wid
    await q.message.reply_text("✏️ الرجاء إدخال سبب الرفض:")
    return REJECT_REASON


async def receive_reject_reason_coinex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip()
    wid = context.user_data.pop("reject_wid", None)
    if not wid:
        await update.message.reply_text("⚠️ لا يوجد طلب معلق.")
        return ConversationHandler.END

    store.update_transaction_status("coinex_withdrawals", wid, "rejected", reason=reason, rejected_at=datetime.now())
    store.add_audit_log("coinex_withdrawals", wid, "rejected", actor=f"admin_{update.effective_user.id}", reason=reason)

    tx = store.get_transaction("coinex_withdrawals", wid)
    if tx:
        user_telegram_id = store.get_user_telegram_by_id(tx["user_id"])
        if user_telegram_id:
            await notify_user(user_telegram_id, f"🚫 تم رفض عملية السحب #{wid}.\n📝 السبب: {reason}")
        # Return balance to user if withdrawal was rejected
        store.add_balance(tx["user_id"], tx["nsp_amount"])
        await notify_user(user_telegram_id, f"✅ تم إعادة رصيد {tx['nsp_amount']:,} NSP إلى حسابك.")

    await update.message.reply_text(f"✅ تم رفض الطلب #{wid}.")
    context.user_data.clear()
    return ConversationHandler.END

# Cancellation handler
async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❎ تم إلغاء العملية.")
    elif update.message:
        await update.message.reply_text("❎ تم إلغاء العملية.")
    context.user_data.clear()
    return ConversationHandler.END


# ========== REGISTER ==========

def register_handlers(dp):
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_withdraw, pattern="^coinex_withdraw$")],
        states={
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_chain)],
            CHAIN: [CallbackQueryHandler(ask_address, pattern="^chain_")],
            ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_request)],
            CONFIRM: [CallbackQueryHandler(submit_request, pattern="^withdraw_")],
            REJECT_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reject_reason_coinex)]
        },
        fallbacks=[CallbackQueryHandler(cancel_action, pattern="^cancel_action$"),
                   CommandHandler("cancel", cancel_action)],
    )
    dp.add_handler(conv)
    dp.add_handler(CallbackQueryHandler(admin_approve_coinex_withdraw, pattern="^admin_coinex_approve"))
    dp.add_handler(CallbackQueryHandler(admin_reject_coinex_withdraw, pattern="^admin_coinex_reject"))