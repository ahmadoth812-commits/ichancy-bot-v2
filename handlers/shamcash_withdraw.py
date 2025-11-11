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
from utils.notifications import notify_user, notify_admin # For notifications

logger = logging.getLogger(__name__)

# Conversation states
AMOUNT, WALLET, CONFIRM, REJECT_REASON, SET_TXID_STATE = range(5) # Added SET_TXID_STATE for admin

WALLET_REGEX = re.compile(r"^[a-fA-F0-9]{24,64}$")


def _fmt(n):
    return f"{int(n):,} NSP"


# =============================
# 💸 بدء عملية السحب
# =============================
async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    text = (
        f"💸 <b>سحب عبر ShamCash</b>\n\n"
        f"🔹 الحد الأدنى: <b>{_fmt(config.SHAMCASH_MIN_WITHDRAW_NSP)}</b>\n"
        f"🔹 عمولة المنصة: <b>{int(config.SHAMCASH_COMMISSION * 100)}%</b>\n\n"
        "💰 الرجاء إدخال المبلغ الذي ترغب بسحبه:"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]
    ])
    await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb) # Using edit_message_text
    return AMOUNT


# =============================
# 💰 إدخال المبلغ
# =============================
async def get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip().replace(",", "")
    try:
        amount = int(txt)
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح.")
        return AMOUNT

    if amount < config.SHAMCASH_MIN_WITHDRAW_NSP:
        await update.message.reply_text(f"⚠️ الحد الأدنى للسحب هو {_fmt(config.SHAMCASH_MIN_WITHDRAW_NSP)}.")
        return AMOUNT

    user_telegram_id = str(update.effective_user.id)
    user = store.get_user_by_telegram_id(user_telegram_id)
    if not user:
        await update.message.reply_text("⚠️ حسابك غير مسجل. استخدم /start أولاً.")
        context.user_data.clear()
        return ConversationHandler.END

    balance = store.get_user_balance(user["id"]) or 0
    if amount > balance:
        await update.message.reply_text(f"🚫 رصيدك الحالي: {_fmt(balance)} — غير كافٍ.")
        return ConversationHandler.END

    context.user_data["amount"] = amount
    await update.message.reply_text("📨 أرسل الآن عنوان محفظة <b>ShamCash</b> (Address):", parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]])
                                  )
    return WALLET


# =============================
# 🏦 إدخال عنوان المحفظة
# =============================
async def get_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wallet = update.message.text.strip()
    if not WALLET_REGEX.match(wallet):
        await update.message.reply_text("❌ العنوان غير صالح. أعد المحاولة.",
                                       reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]])
                                      )
        return WALLET

    context.user_data["wallet"] = wallet
    amount = context.user_data["amount"]
    commission = int(amount * config.SHAMCASH_COMMISSION)
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
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")] # Changed from cancel_withdraw
    ])
    await update.message.reply_text(summary, reply_markup=kb, parse_mode="HTML")
    return CONFIRM


# =============================
# ✅ تأكيد الطلب وحفظه
# =============================
async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_telegram_id = str(q.from_user.id)
    user = store.get_user_by_telegram_id(user_telegram_id)
    if not user:
        await q.edit_message_text("⚠️ حسابك غير مسجل.")
        context.user_data.clear()
        return ConversationHandler.END

    amount = context.user_data["amount"]
    wallet = context.user_data["wallet"]
    commission = int(amount * config.SHAMCASH_COMMISSION)
    net = amount - commission

    # خصم الرصيد
    store.deduct_balance(user["id"], amount)

    tx_id = store._execute_query("""
        INSERT INTO shamcash_withdrawals
        (user_id, wallet_address, requested_amount, commission, net_amount, status, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (user["id"], wallet, amount, commission, net, "pending", datetime.now()), fetchone=False) # returns lastrowid

    if tx_id:
        store.add_audit_log("shamcash_withdrawal", tx_id, "pending", actor=f"user_{user_telegram_id}", reason="User requested withdrawal")

        await q.edit_message_text("✅ تم إرسال طلب السحب، بانتظار موافقة الإدارة.")
        context.user_data.clear()

        msg = (
            f"🔔 <b>طلب سحب جديد عبر ShamCash</b>\n\n"
            f"👤 المستخدم: <a href='tg://user?id={user_telegram_id}'>@{q.from_user.username or q.from_user.full_name}</a>\n"
            f"💰 المبلغ: <code>{_fmt(amount)}</code>\n"
            f"💸 بعد العمولة: <code>{_fmt(net)}</code>\n"
            f"🏦 المحفظة: <code>{wallet}</code>\n"
            f"🆔 رقم العملية: <code>{tx_id}</code>"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ موافقة", callback_data=f"admin_shamcash_approve:{tx_id}")],
            [InlineKeyboardButton("❌ رفض", callback_data=f"admin_shamcash_reject:{tx_id}")] # Changed pattern
        ])
        await notify_admin(msg, reply_markup=kb, parse_mode="HTML")
    else:
        await q.edit_message_text("❌ حدث خطأ في تسجيل طلب السحب بقاعدة البيانات.")
        context.user_data.clear()

    return ConversationHandler.END


# =============================
# 👮‍♂️ الأدمن - الموافقة
# =============================
async def admin_approve_shamcash_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if int(q.from_user.id) not in config.ADMIN_IDS:
        return await q.answer("❌ غير مصرح.")

    tx_id = int(q.data.split(":")[1])
    tx = store.get_transaction("shamcash_withdrawals", tx_id)
    if not tx or tx["status"] != "pending":
        return await q.answer("⚠️ العملية غير موجودة أو تمت مراجعتها.")

    store.update_transaction_status("shamcash_withdrawals", tx_id, "approved_awaiting_txid") # New status
    store.add_audit_log("shamcash_withdrawal", tx_id, "approved_awaiting_txid", actor=f"admin_{q.from_user.id}", reason="Admin approved awaiting txid")

    user_telegram_id = store.get_user_telegram_by_id(tx["user_id"])
    if user_telegram_id:
        await notify_user(
            user_telegram_id,
            f"✅ تمت الموافقة المبدئية على طلب سحبك #{tx_id}. يرجى انتظار معرف التحويل."
        )

    await q.edit_message_text(
        f"✅ تمت الموافقة المبدئية على العملية #{tx_id}.\n"
        f"📤 أرسل الآن رقم المعاملة عبر الأمر:\n"
        f"<code>/set_shamcash_txid {tx_id} &lt;txid&gt;</code>",
        parse_mode="HTML"
    )
    # The conversation could transition to a state waiting for /set_shamcash_txid if admin is the one interacting

    # Not ending conversation here, as admin still needs to provide TXID, maybe later.
    return ConversationHandler.END # End the callback action, but not the overall admin approval process


# =============================
# ❌ الأدمن - الرفض مع السبب
# =============================
async def admin_reject_shamcash_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if int(q.from_user.id) not in config.ADMIN_IDS:
        return await q.answer("❌ غير مصرح.")
    context.user_data["reject_id"] = int(q.data.split(":")[1])
    await q.message.reply_text("✏️ الرجاء إدخال سبب الرفض:")
    return REJECT_REASON


async def receive_reject_reason_shamcash(update: Update, context: ContextTypes.DEFAULT_TYPE): # Renamed to avoid clash
    reason = update.message.text.strip()
    tx_id = context.user_data.pop("reject_id", None)

    if not tx_id:
        await update.message.reply_text("⚠️ حدث خطأ في معالجة الرفض. يرجى المحاولة مرة أخرى.")
        return ConversationHandler.END

    store.update_transaction_status("shamcash_withdrawals", tx_id, "rejected", reason=reason, rejected_at=datetime.now())
    store.add_audit_log("shamcash_withdrawal", tx_id, "rejected", actor=f"admin_{update.effective_user.id}", reason=reason)

    tx = store.get_transaction("shamcash_withdrawals", tx_id)
    if tx:
        user_telegram_id = store.get_user_telegram_by_id(tx["user_id"])
        if user_telegram_id:
            await notify_user(user_telegram_id, f"🚫 تم رفض طلب السحب #{tx_id}.\n📝 السبب: {reason}")
        # Return balance to user if withdrawal was rejected
        store.add_balance(tx["user_id"], tx["requested_amount"]) # Return full requested amount
        await notify_user(user_telegram_id, f"✅ تم إعادة رصيد {_fmt(tx['requested_amount'])} إلى حسابك.")

    await update.message.reply_text(f"تم تسجيل سبب الرفض للعملية #{tx_id}. ✅")
    context.user_data.clear()
    return ConversationHandler.END


# =============================
# 🆔 الأدمن - إدخال TxID
# =============================
async def set_shamcash_txid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if int(update.effective_user.id) not in config.ADMIN_IDS:
        return await update.message.reply_text("❌ غير مصرح.")
    if len(context.args) < 2:
        return await update.message.reply_text("الاستخدام:\n<code>/set_shamcash_txid &lt;id&gt; &lt;txid&gt;</code>", parse_mode="HTML")

    try:
        tx_id, external_txid = int(context.args[0]), context.args[1]
    except ValueError:
        return await update.message.reply_text("❌ معرف العملية أو معرف التحويل غير صالح.")

    tx = store.get_transaction("shamcash_withdrawals", tx_id)
    if not tx:
        return await update.message.reply_text("⚠️ العملية غير موجودة.")
    
    if tx["status"] not in ["approved_awaiting_txid", "pending"]: # Allow setting txid even if not explicitly "approved_awaiting_txid"
        return await update.message.reply_text(f"⚠️ العملية #{tx_id} ليست في حالة انتظار معرف التحويل أو معلقة.")

    store.finalize_shamcash_withdraw(tx_id, external_txid)
    store.add_audit_log("shamcash_withdrawal", tx_id, "approved", actor=f"admin_{update.effective_user.id}", reason=f"TxID set: {external_txid}")

    user_telegram_id = store.get_user_telegram_by_id(tx["user_id"])
    if user_telegram_id:
        await notify_user(
            user_telegram_id,
            f"✅ تمت الموافقة على سحبك #{tx_id}.\n"
            f"🆔 معرف التحويل: <code>{external_txid}</code>",
            parse_mode="HTML"
        )
    await update.message.reply_text("تم تسجيل المعاملة بنجاح ✅")


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
        entry_points=[CallbackQueryHandler(entry, pattern="^shamcash_withdraw$")],
        states={
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_amount)],
            WALLET: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_wallet)],
            CONFIRM: [CallbackQueryHandler(confirm, pattern="^confirm_withdraw$")],
            REJECT_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reject_reason_shamcash)],
            # SET_TXID_STATE could be added here if admin interactions were part of this convo
        },
        fallbacks=[CallbackQueryHandler(cancel_action, pattern="^cancel_action$"),
                   CommandHandler("cancel", cancel_action)],
    )

    dp.add_handler(conv)
    dp.add_handler(CallbackQueryHandler(admin_approve_shamcash_withdraw, pattern="^admin_shamcash_approve"))
    dp.add_handler(CallbackQueryHandler(admin_reject_shamcash_withdraw, pattern="^admin_shamcash_reject"))
    dp.add_handler(CommandHandler("set_shamcash_txid", set_shamcash_txid, filters.User(config.ADMIN_IDS))) # Admin command
