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

# ============================
# 🟢 بدء عملية الإيداع
# ============================
async def start_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    numbers = store.get_syriatel_numbers()
    text = (
        "📱 الرجاء التحويل إلى أحد الأرقام التالية يدويًا:\n"
        + "\n".join(f"• {n}" for n in numbers)
        + f"\n\n💵 أقل مبلغ للتحويل هو {MIN_AMOUNT:,} SYP"
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ تم التحويل", callback_data="syriatel_done")],
            [InlineKeyboardButton("🔙 عودة", callback_data="cancel_action")]
        ]
    )
    await update.effective_chat.send_message(text, reply_markup=kb)
    return AMOUNT


# ============================
# 💰 إدخال المبلغ
# ============================
async def ask_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.effective_chat.send_message("💰 الرجاء إدخال المبلغ الذي قمت بتحويله (بالليرة السورية):")
    return AMOUNT


# ============================
# 🧾 إدخال رقم العملية
# ============================
async def ask_txid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text.strip())
    except:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح للمبلغ.")
        return AMOUNT

    if amount < MIN_AMOUNT:
        await update.message.reply_text(f"⚠️ أقل مبلغ يمكن تحويله هو {MIN_AMOUNT:,} SYP.")
        return AMOUNT

    context.user_data["amount"] = amount
    await update.message.reply_text("🔢 الرجاء إدخال رقم عملية التحويل (Transaction ID):")
    return TXID


# ============================
# ✅ إنهاء وتسجيل العملية
# ============================
async def finalize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txid = update.message.text.strip()
    amount = context.user_data.get("amount")
    user = store.getUserByTelegramId(str(update.effective_user.id))

    if not user:
        await update.message.reply_text("⚠️ حسابك غير مسجل في النظام.")
        return ConversationHandler.END

    db = store.getDatabaseConnection()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO syriatel_transactions (user_id, amount, txid, status, created_at)
        VALUES (%s, %s, %s, %s, %s)
    """, (user["id"], amount, txid, "pending", datetime.now()))
    tx_id = cur.lastrowid
    db.commit()
    db.close()

    # Audit Log
    store.add_audit_log("syriatel", tx_id, "pending", actor="user", reason="User submitted deposit")

    await update.message.reply_text(
        "✅ تم تسجيل عملية الإيداع الخاصة بك.\n"
        "🕓 قيد المراجعة من قبل الإدارة.\n"
        "📩 سيتم إعلامك فور اتخاذ القرار."
    )
    context.user_data.clear()

    # إخطار الأدمن
    msg = (
        f"🔔 <b>طلب إيداع جديد عبر Syriatel Cash</b>\n\n"
        f"👤 المستخدم: @{update.effective_user.username or update.effective_user.full_name}\n"
        f"💰 المبلغ: <code>{amount:,} SYP</code>\n"
        f"🆔 معرف العملية: <code>{txid}</code>\n\n"
        f"يرجى المراجعة والموافقة أو الرفض."
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ موافقة", callback_data=f"admin_approve_syr:{tx_id}")],
        [InlineKeyboardButton("❌ رفض", callback_data=f"admin_reject_syr:{tx_id}")],
    ])

    for admin in ADMIN_IDS:
        try:
            await context.bot.send_message(admin, msg, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            logger.error(f"فشل إرسال إشعار للأدمن {admin}: {e}")

    return ConversationHandler.END


# ============================
# 🟢 موافقة الأدمن
# ============================
async def admin_approve_syr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if int(q.from_user.id) not in ADMIN_IDS:
        return await q.answer("❌ غير مصرح لك.")

    tx_id = int(q.data.split(":")[1])
    tx = store.get_transaction("syriatel_transactions", tx_id)
    if not tx or tx["status"] != "pending":
        return await q.answer("⚠️ العملية غير موجودة أو تمت مراجعتها مسبقًا.")

    user_id = tx["user_id"]

    store.add_balance(user_id, tx["amount"])
    store.update_transaction_status("syriatel_transactions", tx_id, "approved", approved_at=datetime.now())
    store.add_audit_log("syriatel", tx_id, "approved", actor="admin", reason="Deposit approved by admin")

    tg = store.get_user_telegram_by_id(user_id)
    if tg:
        await context.bot.send_message(
            tg,
            f"✅ تمّت الموافقة على إيداعك #{tx_id}\n"
            f"💰 المبلغ: {tx['amount']:,} SYP\n"
            f"🕓 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
    await q.edit_message_text(f"✅ تمت الموافقة على العملية #{tx_id} بنجاح.")


# ============================
# 🔴 رفض الأدمن مع سبب
# ============================
async def admin_reject_syr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    tx_id = int(q.data.split(":")[1])
    context.user_data["reject_tx_id"] = tx_id
    await q.message.reply_text("🚫 الرجاء كتابة سبب الرفض:")
    context.user_data["awaiting_reason"] = True


async def capture_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_reason"):
        return

    reason = update.message.text.strip()
    tx_id = context.user_data.get("reject_tx_id")

    store.update_transaction_status("syriatel_transactions", tx_id, "rejected", rejected_at=datetime.now())
    store.add_audit_log("syriatel", tx_id, "rejected", actor="admin", reason=reason)

    tx = store.get_transaction("syriatel_transactions", tx_id)
    tg = store.get_user_telegram_by_id(tx["user_id"])
    if tg:
        await context.bot.send_message(
            tg,
            f"🚫 تم رفض عملية الإيداع #{tx_id}\n"
            f"💰 المبلغ: {tx['amount']:,} SYP\n"
            f"📝 السبب: {reason}"
        )

    await update.message.reply_text(f"تم تسجيل رفض العملية #{tx_id} مع السبب.")
    context.user_data.clear()


# ============================
# 📦 تسجيل الهاندلرز
# ============================
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
        fallbacks=[
            CallbackQueryHandler(lambda u, c: u.callback_query.message.delete(), pattern="^cancel_action$")
        ],
    )

    dp.add_handler(conv)
    dp.add_handler(CallbackQueryHandler(admin_approve_syr, pattern="^admin_approve_syr"))
    dp.add_handler(CallbackQueryHandler(admin_reject_syr, pattern="^admin_reject_syr"))
    dp.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, capture_reject_reason))
