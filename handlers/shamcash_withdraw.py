import re
import logging
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import store
import config

logger = logging.getLogger(__name__)

# Conversation states
AMOUNT, WALLET, CONFIRM, REJECT_REASON = range(4)

# Config
MIN_WITHDRAW_NSP = getattr(config, "SHAMCASH_MIN_WITHDRAW_NSP", 50000)
COMMISSION_RATE = getattr(config, "SHAMCASH_COMMISSION", 0.10)
ADMIN_IDS = getattr(config, "ADMIN_IDS", [])
WALLET_REGEX = re.compile(r"^[a-fA-F0-9]{24,64}$")


def _is_admin(tg_id): 
    return int(tg_id) in [int(a) for a in ADMIN_IDS]

def _fmt(n): 
    return f"{int(n):,} NSP"


# =============================
# 💸 بدء عملية السحب
# =============================
async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    text = (
        f"💸 <b>سحب عبر ShamCash</b>\n\n"
        f"🔹 الحد الأدنى: <b>{_fmt(MIN_WITHDRAW_NSP)}</b>\n"
        f"🔹 عمولة المنصة: <b>{int(COMMISSION_RATE * 100)}%</b>\n\n"
        "💰 الرجاء إدخال المبلغ الذي ترغب بسحبه:"
    )
    await update.effective_chat.send_message(text, parse_mode="HTML")
    return AMOUNT


# =============================
# 💰 إدخال المبلغ
# =============================
async def get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip().replace(",", "")
    try:
        amount = int(txt)
    except:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح.")
        return AMOUNT

    if amount < MIN_WITHDRAW_NSP:
        await update.message.reply_text(f"⚠️ الحد الأدنى للسحب هو {_fmt(MIN_WITHDRAW_NSP)}.")
        return AMOUNT

    user = store.getUserByTelegramId(str(update.effective_user.id))
    if not user:
        await update.message.reply_text("⚠️ حسابك غير مسجل. استخدم /start أولاً.")
        return ConversationHandler.END

    balance = store.get_user_balance(user["id"]) or 0
    if amount > balance:
        await update.message.reply_text(f"🚫 رصيدك الحالي: {_fmt(balance)} — غير كافٍ.")
        return ConversationHandler.END

    context.user_data["amount"] = amount
    await update.message.reply_text("📨 أرسل الآن عنوان محفظة <b>ShamCash</b> (Address):", parse_mode="HTML")
    return WALLET


# =============================
# 🏦 إدخال عنوان المحفظة
# =============================
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
        f"💳 <b>ملخص العملية</b>\n\n"
        f"💰 المبلغ المطلوب: <code>{_fmt(amount)}</code>\n"
        f"💸 العمولة: <code>{_fmt(commission)}</code>\n"
        f"📤 الصافي المرسل: <code>{_fmt(net)}</code>\n"
        f"🏦 المحفظة: <code>{wallet}</code>\n\n"
        "هل ترغب بتأكيد العملية؟"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد", callback_data="confirm_withdraw")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_withdraw")]
    ])
    await update.message.reply_text(summary, reply_markup=kb, parse_mode="HTML")
    return CONFIRM


# =============================
# ✅ تأكيد الطلب وحفظه
# =============================
async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = store.getUserByTelegramId(str(q.from_user.id))
    if not user:
        await q.edit_message_text("⚠️ حسابك غير مسجل.")
        return ConversationHandler.END

    amount = context.user_data["amount"]
    wallet = context.user_data["wallet"]
    commission = int(amount * COMMISSION_RATE)
    net = amount - commission

    # خصم الرصيد
    store.deduct_balance(user["id"], amount)

    db = store.getDatabaseConnection()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO shamcash_withdrawals
        (user_id, wallet_address, requested_amount, commission, net_amount, status, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (user["id"], wallet, amount, commission, net, "pending", datetime.now()))
    tx_id = cur.lastrowid
    db.commit()
    db.close()

    store.add_audit_log("shamcash", tx_id, "pending", actor="user", reason="User requested withdrawal")

    await q.edit_message_text("✅ تم إرسال طلب السحب، بانتظار موافقة الإدارة.")
    context.user_data.clear()

    msg = (
        f"🔔 <b>طلب سحب جديد عبر ShamCash</b>\n\n"
        f"👤 المستخدم: @{q.from_user.username or q.from_user.full_name}\n"
        f"💰 المبلغ: <code>{_fmt(amount)}</code>\n"
        f"💸 بعد العمولة: <code>{_fmt(net)}</code>\n"
        f"🏦 المحفظة: <code>{wallet}</code>\n"
        f"🆔 رقم العملية: <code>{tx_id}</code>"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ موافقة", callback_data=f"admin_approve:{tx_id}")],
        [InlineKeyboardButton("❌ رفض", callback_data=f"admin_reject:{tx_id}")]
    ])
    for admin in ADMIN_IDS:
        try:
            await context.bot.send_message(admin, msg, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass
    return ConversationHandler.END


# =============================
# 👮‍♂️ الأدمن - الموافقة
# =============================
async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not _is_admin(q.from_user.id):
        return await q.answer("❌ غير مصرح.")

    tx_id = int(q.data.split(":")[1])
    store.update_transaction_status("shamcash_withdrawals", tx_id, "awaiting_txid")
    store.add_audit_log("shamcash", tx_id, "awaiting_txid", actor="admin", reason="Admin approved awaiting txid")

    await q.edit_message_text(
        f"✅ تمت الموافقة المبدئية على العملية #{tx_id}.\n"
        f"📤 أرسل الآن رقم المعاملة عبر الأمر:\n"
        f"<code>/set_shamcash_txid {tx_id} &lt;txid&gt;</code>",
        parse_mode="HTML"
    )


# =============================
# ❌ الأدمن - الرفض مع السبب
# =============================
async def admin_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not _is_admin(q.from_user.id):
        return await q.answer("❌ غير مصرح.")
    context.user_data["reject_id"] = int(q.data.split(":")[1])
    await q.edit_message_text("✏️ الرجاء إدخال سبب الرفض:")
    return REJECT_REASON


async def reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip()
    tx_id = context.user_data.pop("reject_id")
    store.update_transaction_status("shamcash_withdrawals", tx_id, "rejected", rejected_at=datetime.now())
    store.add_audit_log("shamcash", tx_id, "rejected", actor="admin", reason=reason)

    user_tg = store.get_user_telegram_by_tx("shamcash_withdrawals", tx_id)
    if user_tg:
        await update.message.bot.send_message(
            user_tg, f"🚫 تم رفض طلب السحب #{tx_id}.\n📝 السبب: {reason}"
        )
    await update.message.reply_text(f"تم تسجيل سبب الرفض للعملية #{tx_id}. ✅")
    return ConversationHandler.END


# =============================
# 🆔 الأدمن - إدخال TxID
# =============================
async def set_txid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return await update.message.reply_text("❌ غير مصرح.")
    if len(context.args) < 2:
        return await update.message.reply_text("الاستخدام:\n/set_shamcash_txid <id> <txid>")

    tx_id, txid = int(context.args[0]), context.args[1]
    tx = store.get_transaction("shamcash_withdrawals", tx_id)
    if not tx:
        return await update.message.reply_text("⚠️ العملية غير موجودة.")

    store.finalize_shamcash_withdraw(tx_id, txid)
    store.add_audit_log("shamcash", tx_id, "approved", actor="admin", reason=f"TxID set: {txid}")

    tg_id = store.get_user_telegram_by_id(tx["user_id"])
    if tg_id:
        await context.bot.send_message(
            tg_id,
            f"✅ تمت الموافقة على سحبك #{tx_id}.\n"
            f"🆔 معرف التحويل: <code>{txid}</code>",
            parse_mode="HTML"
        )
    await update.message.reply_text("تم تسجيل المعاملة بنجاح ✅")


# =============================
# 📦 تسجيل الهاندلرز
# =============================
def register_handlers(dp):
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(entry, pattern="^shamcash_withdraw$")],
        states={
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_amount)],
            WALLET: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_wallet)],
            CONFIRM: [
                CallbackQueryHandler(confirm, pattern="^confirm_withdraw$"),
                CallbackQueryHandler(lambda u, c: ConversationHandler.END, pattern="^cancel_withdraw$")
            ],
            REJECT_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, reject_reason)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
    )

    dp.add_handler(conv)
    dp.add_handler(CallbackQueryHandler(admin_approve, pattern="^admin_approve"))
    dp.add_handler(CallbackQueryHandler(admin_reject, pattern="^admin_reject"))
    dp.add_handler(CommandHandler("set_shamcash_txid", set_txid))
