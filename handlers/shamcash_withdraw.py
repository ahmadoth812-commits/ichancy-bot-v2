# handlers/shamcash_withdraw.py
import re
import logging
from datetime import datetime
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update
)
from telegram.ext import (
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters
)
import store
import config

logger = logging.getLogger(__name__)

# Conversation states
AMOUNT, WALLET, CONFIRM, REJECT_REASON = range(4)

# Config defaults
MIN_WITHDRAW_NSP = getattr(config, "SHAMCASH_MIN_WITHDRAW_NSP", 50000)
COMMISSION_RATE = getattr(config, "SHAMCASH_COMMISSION", 0.10)
ADMIN_IDS = getattr(config, "ADMIN_IDS", [])
WALLET_REGEX = re.compile(r"^[a-fA-F0-9]{24,64}$")

# Utilities
def _is_admin(tg_id): return int(tg_id) in [int(a) for a in ADMIN_IDS]
def _fmt(n): return f"{int(n):,} NSP"


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    text = (
        f"💸 سحب عبر ShamCash\n\n"
        f"الحد الأدنى للسحب: {_fmt(MIN_WITHDRAW_NSP)}\n"
        f"عمولة المنصة: {int(COMMISSION_RATE * 100)}%\n\n"
        "الرجاء إدخال المبلغ الذي ترغب بسحبه:"
    )
    await update.effective_chat.send_message(text)
    return AMOUNT


async def get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip().replace(",", "")
    try:
        amount = int(txt)
    except:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح.")
        return AMOUNT
    if amount < MIN_WITHDRAW_NSP:
        await update.message.reply_text(f"الحد الأدنى للسحب هو {_fmt(MIN_WITHDRAW_NSP)}.")
        return AMOUNT

    user = store.getUserByTelegramId(str(update.effective_user.id))
    if not user:
        await update.message.reply_text("⚠️ حسابك غير مسجل. استخدم /start أولاً.")
        return ConversationHandler.END

    balance = store.get_user_balance(user['id']) or 0
    if amount > balance:
        await update.message.reply_text(f"❌ رصيدك الحالي ({_fmt(balance)}) غير كافٍ.")
        return ConversationHandler.END

    context.user_data["amount"] = amount
    await update.message.reply_text("📨 أرسل عنوان محفظة ShamCash (Address):")
    return WALLET


async def get_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wallet = update.message.text.strip()
    if not WALLET_REGEX.match(wallet):
        await update.message.reply_text("❌ العنوان غير صالح. أعد المحاولة.")
        return WALLET

    context.user_data["wallet"] = wallet
    amount = context.user_data["amount"]
    commission = int(amount * COMMISSION_RATE)
    net = amount - commission

    summary = (
        f"💳 ملخص العملية:\n\n"
        f"المبلغ المطلوب: {_fmt(amount)}\n"
        f"العمولة: {_fmt(commission)}\n"
        f"الصافي المرسل: {_fmt(net)}\n"
        f"المحفظة: `{wallet}`"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد", callback_data="confirm")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]
    ])
    await update.message.reply_text(summary, reply_markup=kb, parse_mode="Markdown")
    return CONFIRM


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = context.user_data
    user = store.getUserByTelegramId(str(q.from_user.id))
    if not user:
        await q.edit_message_text("⚠️ حسابك غير مسجل.")
        return ConversationHandler.END

    amount, wallet = data["amount"], data["wallet"]
    commission, net = int(amount * COMMISSION_RATE), amount - int(amount * COMMISSION_RATE)
    db = store.getDatabaseConnection()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO shamcash_transactions
        (user_id, wallet_address, requested_amount, commission, net_amount, status, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (user["id"], wallet, amount, commission, net, "pending", datetime.now()))
    tx_id = cur.lastrowid
    cur.execute("""
        INSERT INTO transactions (user_id, provider_id, provider_type, value, action_type)
        VALUES (%s,%s,%s,%s,%s)
    """, (user["id"], tx_id, "shamcash", amount, "withdraw"))
    db.commit()
    db.close()

    # Audit log
    store.add_audit_log("shamcash", tx_id, "pending", "User submitted withdrawal")

    await q.edit_message_text("✅ تم تسجيل طلب السحب، سيتم مراجعته من قبل الأدمن.")
    context.user_data.clear()

    # Notify admins
    text = (
        f"🔔 طلب سحب جديد عبر ShamCash\n\n"
        f"👤 المستخدم: @{q.from_user.username or q.from_user.full_name}\n"
        f"💰 المبلغ: {_fmt(amount)}\n"
        f"💸 بعد العمولة: {_fmt(net)}\n"
        f"📥 المحفظة: `{wallet}`\n"
        f"🆔 رقم المعاملة: {tx_id}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ موافقة", callback_data=f"admin_approve:{tx_id}")],
        [InlineKeyboardButton("❌ رفض", callback_data=f"admin_reject:{tx_id}")]
    ])
    for admin in ADMIN_IDS:
        try:
            await context.bot.send_message(admin, text, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            pass
    return ConversationHandler.END


async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not _is_admin(q.from_user.id): return await q.answer("❌ غير مصرح.")
    tx_id = int(q.data.split(":")[1])
    store.update_transaction_status("shamcash_transactions", tx_id, "awaiting_txid")
    store.add_audit_log("shamcash", tx_id, "awaiting_txid", "Admin approved - waiting for txid")
    await q.edit_message_text(f"✅ تمت الموافقة المبدئية على #{tx_id}. أرسل الآن:\n`/set_shamcash_txid {tx_id} <txid>`", parse_mode="Markdown")


async def admin_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not _is_admin(q.from_user.id): return await q.answer("❌ غير مصرح.")
    context.user_data["reject_id"] = int(q.data.split(":")[1])
    await q.edit_message_text("✏️ أرسل سبب الرفض:")
    return REJECT_REASON


async def reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip()
    tx_id = context.user_data.pop("reject_id")
    store.update_transaction_status("shamcash_transactions", tx_id, "rejected", reason)
    store.add_audit_log("shamcash", tx_id, "rejected", f"Admin rejected: {reason}")
    user_tg = store.get_user_telegram_by_tx("shamcash", tx_id)
    if user_tg:
        await update.message.bot.send_message(user_tg, f"❌ تم رفض طلب السحب #{tx_id}.\nالسبب: {reason}")
    await update.message.reply_text(f"تم رفض الطلب #{tx_id}. ✅")
    return ConversationHandler.END


async def set_txid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return await update.message.reply_text("❌ غير مصرح.")
    if len(context.args) < 2:
        return await update.message.reply_text("الاستخدام:\n/set_shamcash_txid <id> <txid>")
    tx_id, txid = int(context.args[0]), context.args[1]
    tx = store.get_transaction("shamcash_transactions", tx_id)
    if not tx:
        return await update.message.reply_text("⚠️ غير موجود.")
    user_id = tx["user_id"]
    store.finalize_shamcash_withdraw(tx_id, txid)
    store.add_audit_log("shamcash", tx_id, "approved", f"Txid set: {txid}")
    tg_id = store.get_user_telegram_by_id(user_id)
    if tg_id:
        await context.bot.send_message(tg_id, f"✅ تمت الموافقة على سحب #{tx_id}\nTxid: {txid}")
    await update.message.reply_text("تم التحديث بنجاح ✅")


def register_handlers(dp):
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(entry, pattern="^shamcash_withdraw$")],
        states={
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_amount)],
            WALLET: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_wallet)],
            CONFIRM: [
                CallbackQueryHandler(confirm, pattern="^confirm$"),
                CallbackQueryHandler(lambda u, c: ConversationHandler.END, pattern="^cancel$")
            ],
            REJECT_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, reject_reason)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
    )
    dp.add_handler(conv)
    dp.add_handler(CallbackQueryHandler(admin_approve, pattern="^admin_approve"))
    dp.add_handler(CallbackQueryHandler(admin_reject, pattern="^admin_reject"))
    dp.add_handler(CommandHandler("set_shamcash_txid", set_txid))
