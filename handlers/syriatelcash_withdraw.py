# handlers/syriatelcash_withdraw.py
import asyncio
import logging
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
    CommandHandler,
)
import store
import config
from utils.notifications import notify_user, notify_admin

logger = logging.getLogger(__name__)

# Conversation states
AMOUNT, PHONE, CONFIRM, ADMIN_REJECT_REASON, ADMIN_SET_TXID = range(5)

# -------------------------
# Helper to run sync store funcs in executor
# -------------------------
async def run_db(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


# =============================
# Start withdraw flow (callback)
# =============================
async def start_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]])
    try:
        await q.edit_message_text(
            f"💸 الرجاء إدخال المبلغ الذي ترغب بسحبه "
            f"(الحد الأدنى {config.SYRIATEL_MIN_WITHDRAW:,} - الحد الأقصى {config.SYRIATEL_MAX_WITHDRAW:,} ل.س):",
            reply_markup=kb,
        )
    except Exception:
        await q.message.reply_text(
            f"💸 الرجاء إدخال المبلغ الذي ترغب بسحبه "
            f"(الحد الأدنى {config.SYRIATEL_MIN_WITHDRAW:,} - الحد الأقصى {config.SYRIATEL_MAX_WITHDRAW:,} ل.س):",
            reply_markup=kb,
        )
    return AMOUNT


# =============================
# Receive amount -> validate
# =============================
async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return AMOUNT

    txt = update.message.text.strip().replace(",", "")
    try:
        amount = int(txt)
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صالح للمبلغ (بدون نص).")
        return AMOUNT

    if amount < config.SYRIATEL_MIN_WITHDRAW or amount > config.SYRIATEL_MAX_WITHDRAW:
        await update.message.reply_text(
            f"⚠️ المبلغ يجب أن يكون بين {config.SYRIATEL_MIN_WITHDRAW:,} و {config.SYRIATEL_MAX_WITHDRAW:,} ل.س."
        )
        return AMOUNT

    # get user
    user_telegram_id = str(update.effective_user.id)
    try:
        user = await run_db(store.get_user_by_telegram_id, user_telegram_id)
    except Exception as e:
        logger.exception("DB error fetching user: %s", e)
        await update.message.reply_text("⚠️ حدث خطأ داخلي أثناء التحقق من حسابك.")
        return ConversationHandler.END

    if not user:
        await update.message.reply_text("⚠️ حسابك غير مسجل. استخدم /start أولاً.")
        context.user_data.clear()
        return ConversationHandler.END

    try:
        balance = await run_db(store.get_user_balance, user["id"])
    except Exception as e:
        logger.exception("DB error fetching balance: %s", e)
        await update.message.reply_text("⚠️ حدث خطأ داخلي أثناء جلب الرصيد.")
        return ConversationHandler.END

    if not isinstance(balance, int) and not isinstance(balance, float):
        balance = 0

    if amount > balance:
        await update.message.reply_text(f"🚫 رصيدك الحالي: {balance:,} — غير كافٍ.")
        return ConversationHandler.END

    context.user_data["amount"] = amount
    await update.message.reply_text(
        "📞 الرجاء إدخال الرقم المراد إرسال المبلغ إليه:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]])
    )
    return PHONE


# =============================
# Receive phone -> show summary (confirm)
# =============================
def _fmt(n):
    return f"{int(n):,} ل.س"


async def confirm_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return PHONE

    phone = update.message.text.strip()
    # minimal phone sanitation (could be improved based on regional format)
    if len(phone) < 6 or len(phone) > 32:
        await update.message.reply_text("❌ يرجى إدخال رقم صالح (طول غير مقبول).")
        return PHONE

    context.user_data["phone"] = phone
    amount = context.user_data["amount"]

    fee = int(amount * config.SYRIATEL_FEE_PERCENT / 100)
    net_amount = amount - fee

    summary = (
        f"📋 <b>ملخص عملية السحب</b>\n\n"
        f"💰 المبلغ المطلوب: <code>{_fmt(amount)}</code>\n"
        f"💸 عمولة الخدمة ({config.SYRIATEL_FEE_PERCENT}%): <code>{_fmt(fee)}</code>\n"
        f"📤 المبلغ الصافي الذي سيتم إرساله: <code>{_fmt(net_amount)}</code>\n"
        f"📞 الرقم: <code>{phone}</code>\n\n"
        f"هل ترغب في تأكيد الطلب؟"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد", callback_data="withdraw_confirm")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]
    ])
    await update.message.reply_text(summary, reply_markup=kb, parse_mode=ParseMode.HTML)
    return CONFIRM


# =============================
# Finalize: store request, notify admin
# =============================
async def finalize_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data != "withdraw_confirm":
        await q.edit_message_text("❎ تم إلغاء العملية.")
        context.user_data.clear()
        return ConversationHandler.END

    user_telegram_id = str(q.from_user.id)
    try:
        user = await run_db(store.get_user_by_telegram_id, user_telegram_id)
    except Exception as e:
        logger.exception("DB error fetching user: %s", e)
        await q.edit_message_text("⚠️ حدث خطأ داخلي.")
        return ConversationHandler.END

    if not user:
        await q.edit_message_text("⚠️ حسابك غير مسجل.")
        context.user_data.clear()
        return ConversationHandler.END

    amount = context.user_data["amount"]
    phone = context.user_data["phone"]
    fee = int(amount * config.SYRIATEL_FEE_PERCENT / 100)
    net_amount = amount - fee

    # deduct balance
    try:
        await run_db(store.deduct_balance, user["id"], amount)
    except Exception as e:
        logger.exception("DB error deduct balance: %s", e)
        await q.edit_message_text("❌ حدث خطأ في خصم الرصيد. العملية ملغاة.")
        context.user_data.clear()
        return ConversationHandler.END

    # insert withdrawal pending
    try:
        tx_id = await run_db(
            store._execute_query,
            """
            INSERT INTO syriatel_withdrawals
            (user_id, amount, fee, net_amount, phone, status, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (user["id"], amount, fee, net_amount, phone, "pending", datetime.now())
        )
    except Exception as e:
        logger.exception("DB error inserting withdrawal: %s", e)
        # attempt to refund in case of DB error
        try:
            await run_db(store.add_balance, user["id"], amount)
        except Exception:
            logger.exception("DB error refunding after failed insert")
        await q.edit_message_text("❌ حدث خطأ أثناء إنشاء الطلب. تم إرجاع المبلغ إن أمكن.")
        context.user_data.clear()
        return ConversationHandler.END

    if not tx_id:
        # fallback: refund and inform
        try:
            await run_db(store.add_balance, user["id"], amount)
        except Exception:
            logger.exception("DB error refunding after missing tx_id")
        await q.edit_message_text("❌ حدث خطأ أثناء إنشاء الطلب. تم إرجاع المبلغ إن أمكن.")
        context.user_data.clear()
        return ConversationHandler.END

    # add audit log
    try:
        await run_db(store.add_audit_log, "syriatel_withdrawal", tx_id, "pending", f"user_{user_telegram_id}", "User requested withdrawal")
    except Exception:
        logger.exception("Failed to write audit log")

    # notify user + admin
    try:
        await q.edit_message_text("✅ تم إرسال طلب السحب إلى الإدارة للمراجعة.")
    except Exception:
        pass

    msg = (
        f"🔔 <b>طلب سحب جديد عبر Syriatel Cash</b>\n\n"
        f"👤 المستخدم: <a href='tg://user?id={user_telegram_id}'>@{q.from_user.username or q.from_user.full_name}</a>\n"
        f"💰 المبلغ: <code>{amount:,}</code> ل.س\n"
        f"💸 المبلغ الصافي: <code>{net_amount:,}</code> ل.س\n"
        f"📞 الرقم: <code>{phone}</code>\n"
        f"🆔 رقم العملية: <code>{tx_id}</code>\n\n"
        f"يرجى المراجعة والموافقة أو الرفض."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ موافقة", callback_data=f"admin_approve_syriatel_wd:{tx_id}")],
        [InlineKeyboardButton("❌ رفض", callback_data=f"admin_reject_syriatel_wd:{tx_id}")]
    ])
    try:
        await notify_admin(msg, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        logger.exception("Failed to notify admin")

    context.user_data.clear()
    return ConversationHandler.END


# =============================
# Admin approves -> ask for TxID
# =============================
async def admin_approve_syriatel_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    admin_id = int(q.from_user.id)
    if admin_id not in config.ADMIN_IDS:
        return await q.answer("❌ غير مصرح.", show_alert=True)

    try:
        tx_id = int(q.data.split(":")[1])
    except Exception:
        return await q.answer("⚠️ معرف العملية غير صالح.")

    try:
        tx = await run_db(store.get_transaction, "syriatel_withdrawals", tx_id)
    except Exception:
        tx = None

    if not tx or tx.get("status") != "pending":
        return await q.answer("⚠️ العملية غير موجودة أو تمت مراجعتها مسبقًا.")

    # mark awaiting txid and ask admin to send it
    try:
        await run_db(store.update_transaction_status, "syriatel_withdrawals", tx_id, "approved_awaiting_txid", None, None, datetime.now(), None)
    except Exception:
        logger.exception("Failed to update status to approved_awaiting_txid")

    context.user_data["awaiting_txid_for"] = tx_id
    await q.edit_message_text(
        f"✅ تمت الموافقة المبدئية على السحب #{tx_id}.\n"
        f"📤 الآن أرسل معرف التحويل (TxID) عبر رسالة هنا لإكمال العملية.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]])
    )
    return ADMIN_SET_TXID


# =============================
# Admin provides TxID -> finalize withdrawal
# =============================
async def receive_admin_syriatel_txid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END

    admin_id = int(update.effective_user.id)
    if admin_id not in config.ADMIN_IDS:
        await update.message.reply_text("❌ غير مصرح.")
        return ConversationHandler.END

    txid = update.message.text.strip()
    tx_id = context.user_data.pop("awaiting_txid_for", None)
    if not tx_id:
        await update.message.reply_text("⚠️ لا يوجد طلب معلق لإضافة معرف.")
        return ConversationHandler.END

    # update status to approved, add txid
    try:
        await run_db(store.update_transaction_status, "syriatel_withdrawals", tx_id, "approved", None, txid, datetime.now(), None)
    except Exception:
        logger.exception("Failed to update withdrawal with txid")
        await update.message.reply_text("❌ حدث خطأ داخلي أثناء حفظ معرف التحويل.")
        return ConversationHandler.END

    try:
        await run_db(store.add_audit_log, "syriatel_withdrawal", tx_id, "approved", f"admin_{admin_id}", f"TxID: {txid}")
    except Exception:
        logger.exception("Failed to write audit log for txid")

    # notify user
    try:
        tx = await run_db(store.get_transaction, "syriatel_withdrawals", tx_id)
        user_telegram = await run_db(store.get_user_telegram_by_id, tx["user_id"]) if tx else None
        if user_telegram:
            await notify_user(
                user_telegram,
                f"✅ تمت الموافقة على طلب السحب #{tx_id}.\n📤 المبلغ الصافي: {tx['net_amount']:,} ل.س\n🆔 معرف التحويل: {txid}"
            )
    except Exception:
        logger.exception("Failed to notify user after setting txid")

    await update.message.reply_text(f"✅ تم تسجيل معرف التحويل #{tx_id} بنجاح.")
    context.user_data.clear()
    return ConversationHandler.END


# =============================
# Admin rejects -> get reason
# =============================
async def admin_reject_syriatel_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    admin_id = int(q.from_user.id)
    if admin_id not in config.ADMIN_IDS:
        return await q.answer("❌ غير مصرح.", show_alert=True)

    try:
        tx_id = int(q.data.split(":")[1])
    except Exception:
        return await q.answer("⚠️ معرف العملية غير صالح.")

    context.user_data["reject_tx_id"] = tx_id
    try:
        await q.message.reply_text("✏️ الرجاء إدخال سبب الرفض:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]]))
    except Exception:
        await q.answer("✏️ الرجاء إدخال سبب الرفض:")
    return ADMIN_REJECT_REASON


async def receive_reject_reason_syriatel_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END

    reason = update.message.text.strip()
    tx_id = context.user_data.pop("reject_tx_id", None)
    if not tx_id:
        await update.message.reply_text("⚠️ حدث خطأ في معالجة الرفض. يرجى المحاولة مرة أخرى.")
        return ConversationHandler.END

    # update status to rejected and refund
    try:
        await run_db(store.update_transaction_status, "syriatel_withdrawals", tx_id, "rejected", reason, None, None, datetime.now())
    except Exception:
        logger.exception("Failed to set withdrawal as rejected")

    try:
        await run_db(store.add_audit_log, "syriatel_withdrawal", tx_id, "rejected", f"admin_{update.effective_user.id}", reason)
    except Exception:
        logger.exception("Failed to write audit log for rejection")

    # refund user's balance
    try:
        tx = await run_db(store.get_transaction, "syriatel_withdrawals", tx_id)
        if tx:
            await run_db(store.add_balance, tx["user_id"], tx["amount"])
            user_telegram = await run_db(store.get_user_telegram_by_id, tx["user_id"])
            if user_telegram:
                await notify_user(user_telegram, f"🚫 تم رفض طلب السحب #{tx_id}.\n📝 السبب: {reason}\n✅ تم إعادة رصيد {tx['amount']:,} ل.س إلى حسابك.")
    except Exception:
        logger.exception("Failed to refund or notify user after rejection")

    await update.message.reply_text(f"✅ تم تسجيل رفض العملية #{tx_id} مع السبب.")
    context.user_data.clear()
    return ConversationHandler.END


# =============================
# Cancel handler
# =============================
async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text("❎ تم إلغاء العملية.")
        except Exception:
            pass
    elif update.message:
        await update.message.reply_text("❎ تم إلغاء العملية.")
    context.user_data.clear()
    return ConversationHandler.END


# =============================
# Register handlers
# =============================
def register_handlers(app):
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_withdraw, pattern="^syriatel_withdraw$")],
        states={
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_withdraw)],
            CONFIRM: [CallbackQueryHandler(finalize_withdraw, pattern="^withdraw_confirm$")],
            ADMIN_SET_TXID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_admin_syriatel_txid)],
            ADMIN_REJECT_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reject_reason_syriatel_withdraw)],
        },
        fallbacks=[CallbackQueryHandler(cancel_action, pattern="^cancel_action$"), CommandHandler("cancel", cancel_action)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(admin_approve_syriatel_withdraw, pattern="^admin_approve_syriatel_wd"))
    app.add_handler(CallbackQueryHandler(admin_reject_syriatel_withdraw, pattern="^admin_reject_syriatel_wd"))
