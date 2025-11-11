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
AMOUNT, PHONE, CONFIRM, ADMIN_REJECT_REASON, ADMIN_SET_TXID = range(5) # Added admin states

# =============================
# 🟢 بدء عملية السحب
# =============================
async def start_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]
    ])
    await q.edit_message_text( # Using edit_message_text
        f"💸 الرجاء إدخال المبلغ الذي ترغب بسحبه (الحد الأدنى {config.SYRIATEL_MIN_WITHDRAW:,} - الحد الأقصى {config.SYRIATEL_MAX_WITHDRAW:,} ل.س):",
        reply_markup=kb,
    )
    return AMOUNT


# =============================
# 💰 إدخال المبلغ والتحقق من الرصيد
# =============================
async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صالح.")
        return AMOUNT

    if amount < config.SYRIATEL_MIN_WITHDRAW or amount > config.SYRIATEL_MAX_WITHDRAW:
        await update.message.reply_text(
            f"⚠️ المبلغ يجب أن يكون بين {config.SYRIATEL_MIN_WITHDRAW:,} و {config.SYRIATEL_MAX_WITHDRAW:,} ل.س."
        )
        return AMOUNT

    user_telegram_id = str(update.effective_user.id)
    user = store.get_user_by_telegram_id(user_telegram_id)
    if not user:
        await update.message.reply_text("⚠️ حسابك غير مسجل.")
        context.user_data.clear()
        return ConversationHandler.END

    balance = store.get_user_balance(user["id"])
    if balance < amount:
        await update.message.reply_text(
            f"🚫 لا يوجد رصيد كافٍ.\nرصيدك الحالي: {balance:,} ل.س"
        )
        return ConversationHandler.END

    context.user_data["amount"] = amount
    await update.message.reply_text("📞 الرجاء إدخال الرقم المراد إرسال المبلغ إليه:",
                                   reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]])
                                  )
    return PHONE


# =============================
# 📋 عرض ملخص العملية
# =============================
async def confirm_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    context.user_data["phone"] = phone
    amount = context.user_data["amount"]

    fee = int(amount * config.SYRIATEL_FEE_PERCENT / 100)
    net_amount = amount - fee

    summary = (
        f"📋 <b>ملخص عملية السحب</b>\n\n"
        f"💰 المبلغ المطلوب: <code>{amount:,}</code> ل.س\n"
        f"💸 عمولة الخدمة ({config.SYRIATEL_FEE_PERCENT}%): <code>{fee:,}</code> ل.س\n"
        f"📤 المبلغ الصافي الذي سيتم إرساله: <code>{net_amount:,}</code> ل.س\n"
        f"📞 الرقم: <code>{phone}</code>\n\n"
        f"هل ترغب في تأكيد الطلب؟"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد", callback_data="withdraw_confirm")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")] # Changed from withdraw_cancel
    ])
    await update.message.reply_text(summary, reply_markup=kb, parse_mode="HTML")
    return CONFIRM


# =============================
# ✅ تسجيل الطلب وإخطار الأدمن
# =============================
async def finalize_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    # Changed from q.data == "withdraw_cancel" to check for the actual confirm button
    if q.data != "withdraw_confirm":
        await q.edit_message_text("❎ تم إلغاء العملية.")
        context.user_data.clear()
        return ConversationHandler.END

    user_telegram_id = str(q.from_user.id)
    user = store.get_user_by_telegram_id(user_telegram_id)
    if not user:
        await q.edit_message_text("⚠️ حسابك غير مسجل.")
        context.user_data.clear()
        return ConversationHandler.END

    amount = context.user_data["amount"]
    phone = context.user_data["phone"]
    fee = int(amount * config.SYRIATEL_FEE_PERCENT / 100)
    net_amount = amount - fee

    # خصم الرصيد
    store.deduct_balance(user["id"], amount)

    tx_id = store._execute_query("""
        INSERT INTO syriatel_withdrawals
        (user_id, amount, fee, net_amount, phone, status, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (user["id"], amount, fee, net_amount, phone, "pending", datetime.now()), fetchone=False) # returns lastrowid

    if tx_id:
        store.add_audit_log("syriatel_withdrawal", tx_id, "pending", actor=f"user_{user_telegram_id}", reason="User requested withdrawal")

        await q.edit_message_text("✅ تم إرسال طلب السحب إلى الإدارة للمراجعة.")
        context.user_data.clear()

        # إشعار الأدمن
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
            [InlineKeyboardButton("✅ موافقة", callback_data=f"admin_approve_syriatel_wd:{tx_id}")], # Changed pattern
            [InlineKeyboardButton("❌ رفض", callback_data=f"admin_reject_syriatel_wd:{tx_id}")] # Changed pattern
        ])
        await notify_admin(msg, reply_markup=kb, parse_mode="HTML")
    else:
        await q.edit_message_text("❌ حدث خطأ في تسجيل طلب السحب بقاعدة البيانات.")
        context.user_data.clear()

    return ConversationHandler.END


# =============================
# 👮‍♂️ موافقة الأدمن على السحب
# =============================
async def admin_approve_syriatel_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if int(q.from_user.id) not in config.ADMIN_IDS:
        return await q.answer("❌ غير مصرح.")

    tx_id = int(q.data.split(":")[1])
    tx = store.get_transaction("syriatel_withdrawals", tx_id)
    if not tx or tx["status"] != "pending":
        return await q.answer("⚠️ العملية غير موجودة أو تمت مراجعتها مسبقًا.")

    await q.edit_message_text(
        f"💬 الرجاء إرسال معرف التحويل (Transaction ID) الخاص بعملية #{tx_id}:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]])
    )
    context.user_data["awaiting_txid_for"] = tx_id
    return ADMIN_SET_TXID # Enter state to await TXID from admin


async def receive_admin_syriatel_txid(update: Update, context: ContextTypes.DEFAULT_TYPE): # Renamed
    txid = update.message.text.strip()
    admin_id = update.effective_user.id
    if admin_id not in config.ADMIN_IDS:
        await update.message.reply_text("❌ غير مصرح.")
        return ConversationHandler.END

    tx_id = context.user_data.pop("awaiting_txid_for", None)
    if not tx_id:
        await update.message.reply_text("⚠️ لا يوجد طلب معلق لإضافة معرف.")
        return ConversationHandler.END # End conversation if no pending request

    store.update_transaction_status("syriatel_withdrawals", tx_id, "approved", txid_external=txid, approved_at=datetime.now()) # Use txid_external
    store.add_audit_log("syriatel_withdrawal", tx_id, "approved", actor=f"admin_{update.effective_user.id}", reason=f"Approved with TxID {txid}")

    tx = store.get_transaction("syriatel_withdrawals", tx_id)
    if tx:
        user_telegram_id = store.get_user_telegram_by_id(tx["user_id"])
        if user_telegram_id:
            await notify_user(
                user_telegram_id,
                f"✅ تمت الموافقة على طلب السحب #{tx_id}.\n"
                f"📤 المبلغ الصافي: {tx['net_amount']:,} ل.س\n"
                f"🆔 معرف التحويل: {txid}"
            )

    await update.message.reply_text(f"تم تسجيل معرف المعاملة #{tx_id} ✅")
    context.user_data.clear()
    return ConversationHandler.END


# =============================
# ❌ رفض الأدمن مع سبب
# =============================
async def admin_reject_syriatel_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if int(q.from_user.id) not in config.ADMIN_IDS:
        return await q.answer("❌ غير مصرح.")

    tx_id = int(q.data.split(":")[1])
    context.user_data["reject_tx_id"] = tx_id
    await q.message.reply_text("✏️ الرجاء إدخال سبب الرفض:",
                               reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]])
                              )
    return ADMIN_REJECT_REASON # Enter conversation state


async def receive_reject_reason_syriatel_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE): # Renamed
    reason = update.message.text.strip()
    tx_id = context.user_data.pop("reject_tx_id", None)

    if not tx_id:
        await update.message.reply_text("⚠️ حدث خطأ في معالجة الرفض. يرجى المحاولة مرة أخرى.")
        return ConversationHandler.END

    store.update_transaction_status("syriatel_withdrawals", tx_id, "rejected", reason=reason, rejected_at=datetime.now())
    store.add_audit_log("syriatel_withdrawal", tx_id, "rejected", actor=f"admin_{update.effective_user.id}", reason=reason)

    tx = store.get_transaction("syriatel_withdrawals", tx_id)
    if tx:
        user_telegram_id = store.get_user_telegram_by_id(tx["user_id"])
        if user_telegram_id:
            await notify_user(
                user_telegram_id,
                f"🚫 تم رفض عملية السحب #{tx_id}.\n"
                f"📝 السبب: {reason}"
            )
        # Return balance to user if withdrawal was rejected
        store.add_balance(tx["user_id"], tx["amount"]) # Return full requested amount
        await notify_user(user_telegram_id, f"✅ تم إعادة رصيد {tx['amount']:,} SYP إلى حسابك.")

    await update.message.reply_text(f"✅ تم تسجيل رفض العملية #{tx_id} مع السبب.")
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


# =============================
# 📦 تسجيل الهاندلرز
# =============================
def register_handlers(dp):
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_withdraw, pattern="^syriatel_withdraw$")],
        states={
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_withdraw)],
            CONFIRM: [CallbackQueryHandler(finalize_withdraw, pattern="^withdraw_confirm$")],
            ADMIN_SET_TXID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_admin_syriatel_txid)],
            ADMIN_REJECT_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reject_reason_syriatel_withdraw)],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_action, pattern="^cancel_action$"),
            CommandHandler("cancel", cancel_action) # Add command handler for /cancel
        ],
    )

    dp.add_handler(conv)
    dp.add_handler(CallbackQueryHandler(admin_approve_syriatel_withdraw, pattern="^admin_approve_syriatel_wd"))
    dp.add_handler(CallbackQueryHandler(admin_reject_syriatel_withdraw, pattern="^admin_reject_syriatel_wd"))
    # Message handlers for admin interactions are now part of the conversation handler
    # No longer needed as global handlers:
    # dp.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_admin_txid))
    # dp.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reject_reason))
