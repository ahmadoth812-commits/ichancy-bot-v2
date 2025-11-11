import logging
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
AMOUNT, TXID, ADMIN_REJECT_REASON = range(3) # Added ADMIN_REJECT_REASON state

# ============================
# 🟢 بدء عملية الإيداع
# ============================
async def start_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    numbers = store.get_syriatel_numbers() # Assumes this returns a list of strings
    if not numbers:
        await q.edit_message_text("⚠️ لا توجد أرقام Syriatel متاحة للإيداع حالياً.")
        return ConversationHandler.END

    text = (
        "📱 الرجاء التحويل إلى أحد الأرقام التالية يدويًا:\n"
        + "\n".join(f"• <code>{n}</code>" for n in numbers)
        + f"\n\n💵 أقل مبلغ للإيداع هو {config.SYRIATEL_MIN_AMOUNT:,} SYP"
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ تم التحويل", callback_data="syriatel_done")],
            [InlineKeyboardButton("🔙 عودة", callback_data="cancel_action")]
        ]
    )
    await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML") # Using edit_message_text
    return AMOUNT


# ============================
# 💰 إدخال المبلغ
# ============================
async def ask_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("💰 الرجاء إدخال المبلغ الذي قمت بتحويله (بالليرة السورية):",
                              reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]])
                             )
    return AMOUNT


# ============================
# 🧾 إدخال رقم العملية
# ============================
async def ask_txid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح للمبلغ.")
        return AMOUNT

    if amount < config.SYRIATEL_MIN_AMOUNT:
        await update.message.reply_text(f"⚠️ أقل مبلغ يمكن تحويله هو {config.SYRIATEL_MIN_AMOUNT:,} SYP.")
        return AMOUNT

    context.user_data["amount"] = amount
    await update.message.reply_text("🔢 الرجاء إدخال رقم عملية التحويل (Transaction ID):",
                                   reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]])
                                  )
    return TXID


# ============================
# ✅ إنهاء وتسجيل العملية
# ============================
async def finalize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txid = update.message.text.strip()
    amount = context.user_data.get("amount")
    user_telegram_id = str(update.effective_user.id)
    user = store.get_user_by_telegram_id(user_telegram_id)

    if not user:
        await update.message.reply_text("⚠️ حسابك غير مسجل في النظام.")
        context.user_data.clear()
        return ConversationHandler.END
    
    # Check for duplicate TXID
    existing_tx = store._execute_query("SELECT id FROM syriatel_transactions WHERE txid = %s AND status != 'rejected'", (txid,), fetchone=True)
    if existing_tx:
        await update.message.reply_text("⚠️ لقد قمت بتقديم طلب إيداع بنفس معرف المعاملة هذا من قبل.")
        context.user_data.clear()
        return ConversationHandler.END


    tx_id = store._execute_query("""
        INSERT INTO syriatel_transactions (user_id, amount, txid, status, created_at)
        VALUES (%s, %s, %s, %s, %s)
    """, (user["id"], amount, txid, "pending", datetime.now()), fetchone=False) # returns lastrowid

    if tx_id:
        store.add_audit_log("syriatel_deposit", tx_id, "pending", actor=f"user_{user_telegram_id}", reason="User submitted deposit")

        await update.message.reply_text(
            "✅ تم تسجيل عملية الإيداع الخاصة بك.\n"
            "🕓 قيد المراجعة من قبل الإدارة.\n"
            "📩 سيتم إعلامك فور اتخاذ القرار."
        )
        context.user_data.clear()

        # إخطار الأدمن
        msg = (
            f"🔔 <b>طلب إيداع جديد عبر Syriatel Cash</b>\n\n"
            f"👤 المستخدم: <a href='tg://user?id={user_telegram_id}'>@{update.effective_user.username or update.effective_user.full_name}</a>\n"
            f"💰 المبلغ: <code>{amount:,} SYP</code>\n"
            f"🆔 معرف العملية: <code>{txid}</code>\n\n"
            f"يرجى المراجعة والموافقة أو الرفض."
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ موافقة", callback_data=f"admin_approve_syriatel_dep:{tx_id}")], # Changed pattern
            [InlineKeyboardButton("❌ رفض", callback_data=f"admin_reject_syriatel_dep:{tx_id}")], # Changed pattern
        ])
        await notify_admin(msg, reply_markup=kb, parse_mode="HTML")
    else:
        await update.message.reply_text("❌ حدث خطأ في تسجيل الإيداع بقاعدة البيانات.")
        context.user_data.clear()


    return ConversationHandler.END


# ============================
# 🟢 موافقة الأدمن
# ============================
async def admin_approve_syriatel_dep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if int(q.from_user.id) not in config.ADMIN_IDS:
        return await q.answer("❌ غير مصرح لك.")

    tx_id = int(q.data.split(":")[1])
    tx = store.get_transaction("syriatel_transactions", tx_id)
    if not tx or tx["status"] != "pending":
        return await q.answer("⚠️ العملية غير موجودة أو تمت مراجعتها مسبقًا.")

    user_id = tx["user_id"]

    store.add_balance(user_id, tx["amount"])
    store.update_transaction_status("syriatel_transactions", tx_id, "approved", approved_at=datetime.now())
    store.add_audit_log("syriatel_deposit", tx_id, "approved", actor=f"admin_{q.from_user.id}", reason="Deposit approved by admin")

    user_telegram_id = store.get_user_telegram_by_id(user_id)
    if user_telegram_id:
        await notify_user(
            user_telegram_id,
            f"✅ تمّت الموافقة على إيداعك #{tx_id}\n"
            f"💰 المبلغ: {tx['amount']:,} SYP\n"
            f"🕓 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
    await q.edit_message_text(f"✅ تمت الموافقة على العملية #{tx_id} بنجاح.")


# ============================
# 🔴 رفض الأدمن مع سبب
# ============================
async def admin_reject_syriatel_dep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if int(q.from_user.id) not in config.ADMIN_IDS: # Add admin check here
        return await q.answer("❌ غير مصرح لك.")

    tx_id = int(q.data.split(":")[1])
    context.user_data["reject_tx_id"] = tx_id
    await q.message.reply_text("🚫 الرجاء كتابة سبب الرفض:")
    return ADMIN_REJECT_REASON # Enter conversation state


async def receive_reject_reason_syriatel(update: Update, context: ContextTypes.DEFAULT_TYPE): # Renamed
    reason = update.message.text.strip()
    tx_id = context.user_data.pop("reject_tx_id", None)

    if not tx_id:
        await update.message.reply_text("⚠️ حدث خطأ في معالجة الرفض. يرجى المحاولة مرة أخرى.")
        return ConversationHandler.END

    store.update_transaction_status("syriatel_transactions", tx_id, "rejected", reason=reason, rejected_at=datetime.now())
    store.add_audit_log("syriatel_deposit", tx_id, "rejected", actor=f"admin_{update.effective_user.id}", reason=reason)

    tx = store.get_transaction("syriatel_transactions", tx_id)
    if tx:
        user_telegram_id = store.get_user_telegram_by_id(tx["user_id"])
        if user_telegram_id:
            await notify_user(
                user_telegram_id,
                f"🚫 تم رفض عملية الإيداع #{tx_id}\n"
                f"💰 المبلغ: {tx['amount']:,} SYP\n"
                f"📝 السبب: {reason}"
            )

    await update.message.reply_text(f"تم تسجيل رفض العملية #{tx_id} مع السبب.")
    context.user_data.clear()
    return ConversationHandler.END


# Cancellation handler (defined once for all handlers)
async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❎ تم إلغاء العملية.")
    elif update.message:
        await update.message.reply_text("❎ تم إلغاء العملية.")
    context.user_data.clear()
    return ConversationHandler.END


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
            ADMIN_REJECT_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reject_reason_syriatel)],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_action, pattern="^cancel_action$"),
            CommandHandler("cancel", cancel_action) # Add command handler for /cancel
        ],
    )

    dp.add_handler(conv)
    dp.add_handler(CallbackQueryHandler(admin_approve_syriatel_dep, pattern="^admin_approve_syriatel_dep"))
    dp.add_handler(CallbackQueryHandler(admin_reject_syriatel_dep, pattern="^admin_reject_syriatel_dep"))
    # The MessageHandler for receive_reject_reason_syriatel is now part of the ConversationHandler
    # dp.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, capture_reject_reason)) # This is no longer needed globally