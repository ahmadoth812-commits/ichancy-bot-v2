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
AMOUNT, PHONE, CONFIRM = range(3)

ADMIN_IDS = getattr(config, "ADMIN_IDS", [])
MIN_WITHDRAW = getattr(config, "SYRIATEL_MIN_WITHDRAW", 50000)
MAX_WITHDRAW = getattr(config, "SYRIATEL_MAX_WITHDRAW", 500000)
FEE_PERCENT = getattr(config, "SYRIATEL_FEE_PERCENT", 10)


# =============================
# 🟢 بدء عملية السحب
# =============================
async def start_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]
    ])
    await update.effective_chat.send_message(
        f"💸 الرجاء إدخال المبلغ الذي ترغب بسحبه (الحد الأدنى {MIN_WITHDRAW:,} - الحد الأقصى {MAX_WITHDRAW:,} ل.س):",
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

    if amount < MIN_WITHDRAW or amount > MAX_WITHDRAW:
        await update.message.reply_text(
            f"⚠️ المبلغ يجب أن يكون بين {MIN_WITHDRAW:,} و {MAX_WITHDRAW:,} ل.س."
        )
        return AMOUNT

    user = store.getUserByTelegramId(str(update.effective_user.id))
    if not user:
        await update.message.reply_text("⚠️ حسابك غير مسجل.")
        return ConversationHandler.END

    balance = store.get_user_balance(user["id"])
    if balance < amount:
        await update.message.reply_text(
            f"🚫 لا يوجد رصيد كافٍ.\nرصيدك الحالي: {balance:,} ل.س"
        )
        return ConversationHandler.END

    context.user_data["amount"] = amount
    await update.message.reply_text("📞 الرجاء إدخال الرقم المراد إرسال المبلغ إليه:")
    return PHONE


# =============================
# 📋 عرض ملخص العملية
# =============================
async def confirm_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    context.user_data["phone"] = phone
    amount = context.user_data["amount"]

    fee = int(amount * FEE_PERCENT / 100)
    net_amount = amount - fee

    summary = (
        f"📋 <b>ملخص عملية السحب</b>\n\n"
        f"💰 المبلغ المطلوب: <code>{amount:,}</code> ل.س\n"
        f"💸 عمولة الخدمة ({FEE_PERCENT}%): <code>{fee:,}</code> ل.س\n"
        f"📤 المبلغ الصافي الذي سيتم إرساله: <code>{net_amount:,}</code> ل.س\n"
        f"📞 الرقم: <code>{phone}</code>\n\n"
        f"هل ترغب في تأكيد الطلب؟"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد", callback_data="withdraw_confirm")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="withdraw_cancel")]
    ])
    await update.message.reply_text(summary, reply_markup=kb, parse_mode="HTML")
    return CONFIRM


# =============================
# ✅ تسجيل الطلب وإخطار الأدمن
# =============================
async def finalize_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "withdraw_cancel":
        await q.edit_message_text("❎ تم إلغاء العملية.")
        return ConversationHandler.END

    user = store.getUserByTelegramId(str(update.effective_user.id))
    amount = context.user_data["amount"]
    phone = context.user_data["phone"]
    fee = int(amount * FEE_PERCENT / 100)
    net_amount = amount - fee

    # خصم الرصيد
    store.deduct_balance(user["id"], amount)

    db = store.getDatabaseConnection()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO syriatel_withdrawals 
        (user_id, amount, fee, net_amount, phone, status, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (user["id"], amount, fee, net_amount, phone, "pending", datetime.now()))
    tx_id = cur.lastrowid
    db.commit()
    db.close()

    store.add_audit_log("syriatel_withdrawals", tx_id, "pending", actor="user", reason="User requested withdrawal")

    await q.edit_message_text("✅ تم إرسال طلب السحب إلى الإدارة للمراجعة.")
    context.user_data.clear()

    # إشعار الأدمن
    msg = (
        f"🔔 <b>طلب سحب جديد عبر Syriatel Cash</b>\n\n"
        f"👤 المستخدم: @{q.from_user.username or q.from_user.full_name}\n"
        f"💰 المبلغ: <code>{amount:,}</code> ل.س\n"
        f"💸 المبلغ الصافي: <code>{net_amount:,}</code> ل.س\n"
        f"📞 الرقم: <code>{phone}</code>\n"
        f"🆔 رقم العملية: <code>{tx_id}</code>\n\n"
        f"يرجى المراجعة والموافقة أو الرفض."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ موافقة", callback_data=f"admin_approve_wd:{tx_id}")],
        [InlineKeyboardButton("❌ رفض", callback_data=f"admin_reject_wd:{tx_id}")]
    ])
    for admin in ADMIN_IDS:
        try:
            await context.bot.send_message(admin, msg, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            logger.error(f"فشل إرسال إشعار للأدمن {admin}: {e}")

    return ConversationHandler.END


# =============================
# 👮‍♂️ موافقة الأدمن على السحب
# =============================
async def admin_approve_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if int(q.from_user.id) not in ADMIN_IDS:
        return await q.answer("❌ غير مصرح.")

    tx_id = int(q.data.split(":")[1])
    tx = store.get_transaction("syriatel_withdrawals", tx_id)
    if not tx or tx["status"] != "pending":
        return await q.answer("⚠️ العملية غير موجودة أو تمت مراجعتها مسبقًا.")

    await q.edit_message_text(
        f"💬 الرجاء إرسال معرف التحويل (Transaction ID) الخاص بعملية #{tx_id}:"
    )
    context.user_data["awaiting_txid_for"] = tx_id


# =============================
# 🆔 إدخال معرف التحويل من الأدمن
# =============================
async def receive_admin_txid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txid = update.message.text.strip()
    admin_id = update.effective_user.id
    if admin_id not in ADMIN_IDS:
        return

    tx_id = context.user_data.pop("awaiting_txid_for", None)
    if not tx_id:
        return await update.message.reply_text("⚠️ لا يوجد طلب معلق لإضافة معرف.")

    store.update_transaction_status("syriatel_withdrawals", tx_id, "approved", txid=txid, approved_at=datetime.now())
    store.add_audit_log("syriatel_withdrawals", tx_id, "approved", actor="admin", reason=f"Approved with TxID {txid}")

    tx = store.get_transaction("syriatel_withdrawals", tx_id)
    tg_id = store.get_user_telegram_by_id(tx["user_id"])
    if tg_id:
        await context.bot.send_message(
            tg_id,
            f"✅ تمت الموافقة على طلب السحب #{tx_id}.\n"
            f"📤 المبلغ الصافي: {tx['net_amount']:,} ل.س\n"
            f"🆔 معرف التحويل: {txid}"
        )

    await update.message.reply_text(f"تم تسجيل معرف المعاملة #{tx_id} ✅")


# =============================
# ❌ رفض الأدمن مع سبب
# =============================
async def admin_reject_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if int(q.from_user.id) not in ADMIN_IDS:
        return await q.answer("❌ غير مصرح.")

    tx_id = int(q.data.split(":")[1])
    context.user_data["reject_tx_id"] = tx_id
    await q.message.reply_text("✏️ الرجاء إدخال سبب الرفض:")
    context.user_data["awaiting_reason"] = True


async def receive_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_reason"):
        return

    reason = update.message.text.strip()
    tx_id = context.user_data.get("reject_tx_id")

    store.update_transaction_status("syriatel_withdrawals", tx_id, "rejected", rejected_at=datetime.now())
    store.add_audit_log("syriatel_withdrawals", tx_id, "rejected", actor="admin", reason=reason)

    tx = store.get_transaction("syriatel_withdrawals", tx_id)
    tg_id = store.get_user_telegram_by_id(tx["user_id"])
    if tg_id:
        await context.bot.send_message(
            tg_id,
            f"🚫 تم رفض عملية السحب #{tx_id}.\n"
            f"📝 السبب: {reason}"
        )

    await update.message.reply_text(f"✅ تم تسجيل رفض العملية #{tx_id} مع السبب.")
    context.user_data.clear()


# =============================
# 📦 تسجيل الهاندلرز
# =============================
def register_handlers(dp):
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_withdraw, pattern="^syriatel_withdraw$")],
        states={
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_withdraw)],
            CONFIRM: [CallbackQueryHandler(finalize_withdraw, pattern="^withdraw_")],
        },
        fallbacks=[
            CallbackQueryHandler(lambda u, c: u.callback_query.message.delete(), pattern="^cancel_action$")
        ],
    )

    dp.add_handler(conv)
    dp.add_handler(CallbackQueryHandler(admin_approve_withdraw, pattern="^admin_approve_wd"))
    dp.add_handler(CallbackQueryHandler(admin_reject_withdraw, pattern="^admin_reject_wd"))
    dp.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_admin_txid))
    dp.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reject_reason))
