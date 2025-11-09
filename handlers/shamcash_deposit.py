# handlers/shamcash_deposit.py
import logging
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
import store
import config

logger = logging.getLogger(__name__)

# Conversation states
CURRENCY, AMOUNT, TXID = range(3)

ADMIN_IDS = getattr(config, "ADMIN_IDS", [])
MIN_AMOUNT_USD = getattr(config, "SHAMCASH_MIN_USD", 5)
MIN_AMOUNT_NSP = getattr(config, "SHAMCASH_MIN_NSP", 25000)


async def start_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    text = "💰 اختر نوع العملة التي قمت بالتحويل بها:"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇺🇸 USD", callback_data="shamcash_usd"),
         InlineKeyboardButton("🇸🇾 NSP", callback_data="shamcash_nsp")]
    ])
    await update.effective_chat.send_message(text, reply_markup=kb)
    return CURRENCY


async def ask_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["currency"] = "USD" if "usd" in q.data else "NSP"
    await q.edit_message_text(f"💵 الرجاء إدخال المبلغ الذي قمت بتحويله ({context.user_data['currency']}):")
    return AMOUNT


async def ask_txid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
    except:
        await update.message.reply_text("❌ الرجاء إدخال رقم صالح.")
        return AMOUNT

    cur = context.user_data["currency"]
    min_amount = MIN_AMOUNT_USD if cur == "USD" else MIN_AMOUNT_NSP
    if amount < min_amount:
        await update.message.reply_text(f"⚠️ الحد الأدنى للإيداع هو {min_amount} {cur}.")
        return AMOUNT

    context.user_data["amount"] = amount
    await update.message.reply_text("🔢 الرجاء إدخال معرف عملية التحويل (TxID):")
    return TXID


async def finalize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txid = update.message.text.strip()
    data = context.user_data
    currency, amount = data["currency"], data["amount"]

    user = store.getUserByTelegramId(str(update.effective_user.id))
    if not user:
        await update.message.reply_text("⚠️ حسابك غير مسجل. استخدم /start أولاً.")
        return ConversationHandler.END

    db = store.getDatabaseConnection()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO shamcash_transactions (user_id, currency, amount, txid, status, created_at)
        VALUES (%s,%s,%s,%s,%s,%s)
    """, (user["id"], currency, amount, txid, "pending", datetime.now()))
    db.commit()
    tx_id = cur.lastrowid
    db.close()

    # Audit log
    store.add_audit_log("shamcash", tx_id, "pending", f"User submitted deposit {currency}")

    await update.message.reply_text("✅ تم تسجيل طلب الإيداع بانتظار مراجعة الأدمن.")
    context.user_data.clear()

    # Notify admins
    msg = (
        f"🔔 طلب إيداع جديد عبر ShamCash\n"
        f"👤 المستخدم: @{update.effective_user.username or update.effective_user.full_name}\n"
        f"💰 المبلغ: {amount} {currency}\n"
        f"🆔 TxID: `{txid}`\n"
        f"رقم العملية الداخلية: {tx_id}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ موافقة", callback_data=f"admin_approve_dep:{tx_id}")],
        [InlineKeyboardButton("❌ رفض", callback_data=f"admin_reject_dep:{tx_id}")]
    ])
    for admin in ADMIN_IDS:
        try:
            await context.bot.send_message(admin, msg, reply_markup=kb, parse_mode="Markdown")
        except:
            pass
    return ConversationHandler.END


async def admin_approve_dep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if int(q.from_user.id) not in ADMIN_IDS:
        return await q.answer("❌ غير مصرح.")
    tx_id = int(q.data.split(":")[1])
    tx = store.get_transaction("shamcash_transactions", tx_id)
    if not tx:
        return await q.answer("⚠️ المعاملة غير موجودة.")
    user_id = tx["user_id"]

    # تحويل USD إلى NSP إن لزم
    value = tx["amount"]
    if tx["currency"] == "USD":
        rate = store.get_usd_to_nsp_rate()
        value = int(value * rate)

    store.add_balance(user_id, value)
    store.update_transaction_status("shamcash_transactions", tx_id, "approved")
    store.add_audit_log("shamcash", tx_id, "approved", "Admin approved deposit")

    tg = store.get_user_telegram_by_id(user_id)
    if tg:
        await context.bot.send_message(tg, f"✅ تمت الموافقة على إيداعك #{tx_id} بمبلغ {value} NSP.")
    await q.edit_message_text(f"تمت الموافقة على العملية #{tx_id}. ✅")


async def admin_reject_dep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if int(q.from_user.id) not in ADMIN_IDS:
        return await q.answer("❌ غير مصرح.")
    tx_id = int(q.data.split(":")[1])
    store.update_transaction_status("shamcash_transactions", tx_id, "rejected")
    store.add_audit_log("shamcash", tx_id, "rejected", "Admin rejected deposit")
    await q.edit_message_text(f"🚫 تم رفض العملية #{tx_id}.")


def register_handlers(dp):
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_deposit, pattern="^shamcash_deposit$")],
        states={
            CURRENCY: [CallbackQueryHandler(ask_amount, pattern="^shamcash_(usd|nsp)$")],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_txid)],
            TXID: [MessageHandler(filters.TEXT & ~filters.COMMAND, finalize)],
        },
        fallbacks=[],
    )
    dp.add_handler(conv)
    dp.add_handler(CallbackQueryHandler(admin_approve_dep, pattern="^admin_approve_dep"))
    dp.add_handler(CallbackQueryHandler(admin_reject_dep, pattern="^admin_reject_dep"))
