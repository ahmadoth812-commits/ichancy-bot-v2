# handlers/syriatelcash_deposit.py
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
AMOUNT, TXID = range(2)

ADMIN_IDS = getattr(config, "ADMIN_IDS", [])
MIN_AMOUNT = getattr(config, "SYRIATEL_MIN_AMOUNT", 25000)

async def start_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    numbers = store.get_syriatel_numbers()
    text = (
        "📱 الرجاء التحويل إلى أحد الأرقام التالية يدويًا:\n"
        + "\n".join(f"• {n}" for n in numbers)
        + f"\n\n💵 أقل مبلغ هو {MIN_AMOUNT} SYP"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ تم التحويل", callback_data="syriatel_done")]])
    await update.effective_chat.send_message(text, reply_markup=kb)
    return AMOUNT


async def ask_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.effective_chat.send_message("💰 الرجاء إدخال المبلغ الذي قمت بتحويله:")
    return AMOUNT


async def ask_txid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text.strip())
    except:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح.")
        return AMOUNT
    if amount < MIN_AMOUNT:
        await update.message.reply_text(f"⚠️ أقل مبلغ هو {MIN_AMOUNT} SYP.")
        return AMOUNT
    context.user_data["amount"] = amount
    await update.message.reply_text("🔢 الرجاء إدخال رقم عملية التحويل (Transaction ID):")
    return TXID


async def finalize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txid = update.message.text.strip()
    amount = context.user_data["amount"]
    user = store.getUserByTelegramId(str(update.effective_user.id))
    if not user:
        await update.message.reply_text("⚠️ حسابك غير مسجل.")
        return ConversationHandler.END

    db = store.getDatabaseConnection()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO syriatel_transactions (user_id, amount, txid, status, created_at)
        VALUES (%s,%s,%s,%s,%s)
    """, (user["id"], amount, txid, "pending", datetime.now()))
    tx_id = cur.lastrowid
    db.commit()
    db.close()

    store.add_audit_log("syriatel", tx_id, "pending", "User submitted deposit")

    await update.message.reply_text("✅ تم تسجيل عملية الإيداع قيد المراجعة.")
    context.user_data.clear()

    msg = (
        f"🔔 طلب إيداع جديد عبر Syriatel Cash\n"
        f"👤 المستخدم: @{update.effective_user.username or update.effective_user.full_name}\n"
        f"💰 المبلغ: {amount} SYP\n"
        f"🆔 معرف العملية: `{txid}`"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ موافقة", callback_data=f"admin_approve_syr:{tx_id}")],
        [InlineKeyboardButton("❌ رفض", callback_data=f"admin_reject_syr:{tx_id}")]
    ])
    for admin in ADMIN_IDS:
        try:
            await context.bot.send_message(admin, msg, reply_markup=kb, parse_mode="Markdown")
        except:
            pass
    return ConversationHandler.END


async def admin_approve_syr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if int(q.from_user.id) not in ADMIN_IDS:
        return await q.answer("❌ غير مصرح.")
    tx_id = int(q.data.split(":")[1])
    tx = store.get_transaction("syriatel_transactions", tx_id)
    user_id = tx["user_id"]

    store.add_balance(user_id, tx["amount"])
    store.update_transaction_status("syriatel_transactions", tx_id, "approved")
    store.add_audit_log("syriatel", tx_id, "approved", "Admin approved deposit")

    tg = store.get_user_telegram_by_id(user_id)
    if tg:
        await context.bot.send_message(tg, f"✅ تمت الموافقة على إيداعك #{tx_id}.")
    await q.edit_message_text(f"تمت الموافقة على العملية #{tx_id}.")


async def admin_reject_syr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tx_id = int(q.data.split(":")[1])
    store.update_transaction_status("syriatel_transactions", tx_id, "rejected")
    store.add_audit_log("syriatel", tx_id, "rejected", "Admin rejected deposit")
    await q.edit_message_text(f"🚫 تم رفض العملية #{tx_id}.")


def register_handlers(dp):
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_deposit, pattern="^syriatel_deposit$")],
        states={
            AMOUNT: [
                CallbackQueryHandler(ask_amount, pattern="^syriatel_done$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_txid)
            ],
            TXID: [MessageHandler(filters.TEXT & ~filters.COMMAND, finalize)],
        },
        fallbacks=[],
    )
    dp.add_handler(conv)
    dp.add_handler(CallbackQueryHandler(admin_approve_syr, pattern="^admin_approve_syr"))
    dp.add_handler(CallbackQueryHandler(admin_reject_syr, pattern="^admin_reject_syr"))
