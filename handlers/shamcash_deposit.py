import logging
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
    CommandHandler, # Added CommandHandler for /cancel
)
import store
import config
from utils.notifications import notify_user, notify_admin # For notifications

logger = logging.getLogger(__name__)

# Conversation states
CURRENCY, AMOUNT, TXID, ADMIN_REJECT_REASON = range(4) # Added ADMIN_REJECT_REASON state

# =============================
# 💰 بدء عملية الإيداع
# =============================
async def start_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    text = "💵 اختر نوع العملة التي قمت بالتحويل بها:"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇺🇸 USD", callback_data="shamcash_usd"),
         InlineKeyboardButton("🇸🇾 NSP", callback_data="shamcash_nsp")],
        [InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]
    ])
    await q.edit_message_text(text, reply_markup=kb)
    return CURRENCY


# =============================
# 💲 إدخال المبلغ
# =============================
async def ask_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["currency"] = "USD" if "usd" in q.data else "NSP"
    cur = context.user_data["currency"]
    await q.edit_message_text(
        f"💰 الرجاء إدخال المبلغ الذي قمت بتحويله ({cur}):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]])
    )
    return AMOUNT


# =============================
# 🔢 إدخال رقم المعاملة
# =============================
async def ask_txid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صالح.")
        return AMOUNT

    cur = context.user_data["currency"]
    min_amount = config.SHAMCASH_MIN_USD if cur == "USD" else config.SHAMCASH_MIN_NSP
    if amount < min_amount:
        await update.message.reply_text(f"⚠️ الحد الأدنى للإيداع هو {min_amount} {cur}.")
        return AMOUNT

    context.user_data["amount"] = amount
    await update.message.reply_text(
        "🔢 الرجاء إدخال معرف عملية التحويل (TxID):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]])
    )
    return TXID


# =============================
# ✅ إنهاء الإيداع وإرسال الطلب
# =============================
async def finalize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txid = update.message.text.strip()
    data = context.user_data
    currency, amount = data["currency"], data["amount"]

    user_telegram_id = str(update.effective_user.id)
    user = store.get_user_by_telegram_id(user_telegram_id)
    if not user:
        await update.message.reply_text("⚠️ حسابك غير مسجل.")
        context.user_data.clear()
        return ConversationHandler.END

    # Check for duplicate TXID
    existing_tx = store._execute_query("SELECT id FROM shamcash_transactions WHERE txid = %s AND status != 'rejected'", (txid,), fetchone=True)
    if existing_tx:
        await update.message.reply_text("⚠️ لقد قمت بتقديم طلب إيداع بنفس معرف المعاملة هذا من قبل.")
        context.user_data.clear()
        return ConversationHandler.END

    tx_id = store._execute_query("""
        INSERT INTO shamcash_transactions (user_id, currency, amount, txid, status, created_at)
        VALUES (%s,%s,%s,%s,%s,%s)
    """, (user["id"], currency, amount, txid, "pending", datetime.now()), fetchone=False) # Returns lastrowid

    if tx_id:
        store.add_audit_log("shamcash_deposit", tx_id, "pending", actor=f"user_{user_telegram_id}", reason=f"User submitted deposit in {currency}")

        await update.message.reply_text("✅ تم تسجيل طلب الإيداع بانتظار مراجعة الإدارة.")
        context.user_data.clear()

        msg = (
            f"🔔 <b>طلب إيداع جديد عبر ShamCash</b>\n\n"
            f"👤 المستخدم: <a href='tg://user?id={user_telegram_id}'>@{update.effective_user.username or update.effective_user.full_name}</a>\n"
            f"💰 المبلغ: <code>{amount}</code> {currency}\n"
            f"🆔 TxID: <code>{txid}</code>\n"
            f"رقم العملية: <code>{tx_id}</code>"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ موافقة", callback_data=f"admin_approve_shamcash_dep:{tx_id}")],
            [InlineKeyboardButton("❌ رفض", callback_data=f"admin_reject_shamcash_dep:{tx_id}")]
        ])
        await notify_admin(msg, reply_markup=kb, parse_mode="HTML")
    else:
        await update.message.reply_text("❌ حدث خطأ في تسجيل الإيداع بقاعدة البيانات.")
        context.user_data.clear()

    return ConversationHandler.END


# =============================
# 👮‍♂️ موافقة الأدمن
# =============================
async def admin_approve_dep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if int(q.from_user.id) not in config.ADMIN_IDS:
        return await q.answer("❌ غير مصرح.")

    tx_id = int(q.data.split(":")[1])
    tx = store.get_transaction("shamcash_transactions", tx_id)
    if not tx or tx["status"] != "pending":
        return await q.answer("⚠️ العملية غير موجودة أو تمت مراجعتها سابقًا.")

    user_id = tx["user_id"]
    value = tx["amount"]

    # تحويل USD إلى NSP إن لزم
    if tx["currency"] == "USD":
        rate = store.get_usd_to_nsp_rate()
        value = int(value * rate)
        
    store.add_balance(user_id, value)
    store.update_transaction_status("shamcash_transactions", tx_id, "approved", approved_at=datetime.now())
    store.add_audit_log("shamcash_deposit", tx_id, "approved", actor=f"admin_{q.from_user.id}", reason="Admin approved deposit")

    user_telegram_id = store.get_user_telegram_by_id(user_id)
    if user_telegram_id:
        await notify_user(
            user_telegram_id,
            f"✅ تمت الموافقة على إيداعك #{tx_id} بمبلغ <b>{value} NSP</b>.",
            parse_mode="HTML"
        )
    await q.edit_message_text(f"✅ تمت الموافقة على العملية #{tx_id}.")


# =============================
# ❌ رفض الأدمن مع سبب
# =============================
async def admin_reject_dep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if int(q.from_user.id) not in config.ADMIN_IDS:
        return await q.answer("❌ غير مصرح.")

    tx_id = int(q.data.split(":")[1])
    context.user_data["reject_tx_id"] = tx_id
    await q.message.reply_text("✏️ الرجاء إدخال سبب الرفض:")
    return ADMIN_REJECT_REASON # Enter the conversation state


async def receive_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip()
    tx_id = context.user_data.pop("reject_tx_id", None)

    if not tx_id:
        await update.message.reply_text("⚠️ حدث خطأ في معالجة الرفض. يرجى المحاولة مرة أخرى.")
        return ConversationHandler.END

    store.update_transaction_status("shamcash_transactions", tx_id, "rejected", reason=reason, rejected_at=datetime.now())
    store.add_audit_log("shamcash_deposit", tx_id, "rejected", actor=f"admin_{update.effective_user.id}", reason=reason)

    tx = store.get_transaction("shamcash_transactions", tx_id)
    if tx:
        user_telegram_id = store.get_user_telegram_by_id(tx["user_id"])
        if user_telegram_id:
            await notify_user(
                user_telegram_id,
                f"🚫 تم رفض عملية الإيداع #{tx_id}.\n📝 السبب: {reason}"
            )

    await update.message.reply_text(f"✅ تم تسجيل سبب الرفض للعملية #{tx_id}.")
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


# =============================
# 📦 تسجيل الهاندلرز
# =============================
def register_handlers(dp):
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_deposit, pattern="^shamcash_deposit$")],
        states={
            CURRENCY: [CallbackQueryHandler(ask_amount, pattern="^shamcash_(usd|nsp)$")],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_txid)],
            TXID: [MessageHandler(filters.TEXT & ~filters.COMMAND, finalize)],
            ADMIN_REJECT_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reject_reason)],
        },
        fallbacks=[CallbackQueryHandler(cancel_action, pattern="^cancel_action$"),
                   CommandHandler("cancel", cancel_action)],
    )

    dp.add_handler(conv)
    dp.add_handler(CallbackQueryHandler(admin_approve_dep, pattern="^admin_approve_shamcash_dep"))
    dp.add_handler(CallbackQueryHandler(admin_reject_dep, pattern="^admin_reject_shamcash_dep"))
    # The MessageHandler for receive_reject_reason is now part of the ConversationHandler
    # dp.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reject_reason)) # This is no longer needed globally
