# handlers/shamcash_withdraw.py
import asyncio
import logging
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
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

AMOUNT, WALLET, CONFIRM, REJECT_REASON = range(4)

async def run_db(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


def _fmt(n):
    return f"{int(n):,} NSP"


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    text = (
        f"💸 <b>سحب عبر ShamCash</b>\n\n"
        f"🔹 الحد الأدنى: <b>{_fmt(config.SHAMCASH_MIN_WITHDRAW_NSP)}</b>\n"
        f"🔹 عمولة المنصة: <b>{int(config.SHAMCASH_COMMISSION * 100)}%</b>\n\n"
        "💰 الرجاء إدخال المبلغ الذي ترغب بسحبه:"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]])
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    return AMOUNT


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
    user = await run_db(store.get_user_by_telegram_id, user_telegram_id)
    if not user:
        await update.message.reply_text("⚠️ حسابك غير مسجل. استخدم /start أولاً.")
        context.user_data.clear()
        return ConversationHandler.END

    balance = await run_db(store.get_user_balance, user["id"]) or 0
    if amount > balance:
        await update.message.reply_text(f"🚫 رصيدك الحالي: {_fmt(balance)} — غير كافٍ.")
        return ConversationHandler.END

    context.user_data["amount"] = amount
    await update.message.reply_text("📨 أرسل الآن عنوان محفظة <b>ShamCash</b> (Address):", parse_mode=ParseMode.HTML,
                                   reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]]))
    return WALLET


async def get_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wallet = update.message.text.strip()
    # minimal validation: length
    if len(wallet) < 6 or len(wallet) > 128:
        await update.message.reply_text("❌ العنوان غير صالح. أعد المحاولة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]]))
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
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]
    ])
    await update.message.reply_text(summary, reply_markup=kb, parse_mode=ParseMode.HTML)
    return CONFIRM


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_telegram_id = str(q.from_user.id)
    user = await run_db(store.get_user_by_telegram_id, user_telegram_id)
    if not user:
        await q.edit_message_text("⚠️ حسابك غير مسجل.")
        context.user_data.clear()
        return ConversationHandler.END

    amount = context.user_data["amount"]
    wallet = context.user_data["wallet"]
    commission = int(amount * config.SHAMCASH_COMMISSION)
    net = amount - commission

    # deduct balance
    await run_db(store.deduct_balance, user["id"], amount)

    tx_id = await run_db(store._execute_query, """
        INSERT INTO shamcash_withdrawals
        (user_id, wallet_address, requested_amount, commission, net_amount, status, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (user["id"], wallet, amount, commission, net, "pending", datetime.now()))
    if tx_id:
        await run_db(store.add_audit_log, "shamcash_withdrawal", tx_id, "pending", f"user_{user_telegram_id}", "User requested withdrawal")
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
            [InlineKeyboardButton("❌ رفض", callback_data=f"admin_shamcash_reject:{tx_id}")]
        ])
        await notify_admin(msg, reply_markup=kb, parse_mode=ParseMode.HTML)
    else:
        await q.edit_message_text("❌ حدث خطأ في تسجيل طلب السحب بقاعدة البيانات.")
        context.user_data.clear()
    return ConversationHandler.END


async def admin_approve_shamcash_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if int(q.from_user.id) not in config.ADMIN_IDS:
        return await q.answer("❌ غير مصرح.")
    tx_id = int(q.data.split(":")[1])
    tx = await run_db(store.get_transaction, "shamcash_withdrawals", tx_id)
    if not tx or tx["status"] != "pending":
        return await q.answer("⚠️ العملية غير موجودة أو تمت مراجعتها.")
    await run_db(store.update_transaction_status, "shamcash_withdrawals", tx_id, "approved_awaiting_txid", None, None, datetime.now(), None)
    await run_db(store.add_audit_log, "shamcash_withdrawal", tx_id, "approved_awaiting_txid", f"admin_{q.from_user.id}", "Admin approved awaiting txid")
    user_telegram = await run_db(store.get_user_telegram_by_id, tx["user_id"])
    if user_telegram:
        await notify_user(user_telegram, f"✅ تمت الموافقة المبدئية على طلب سحبك #{tx_id}. يرجى انتظار معرف التحويل.")
    await q.edit_message_text(f"✅ تمت الموافقة المبدئية على العملية #{tx_id}.\n📤 أرسل الآن رقم المعاملة عبر الأمر:\n<code>/set_shamcash_txid {tx_id} &lt;txid&gt;</code>", parse_mode=ParseMode.HTML)


async def admin_reject_shamcash_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if int(q.from_user.id) not in config.ADMIN_IDS:
        return await q.answer("❌ غير مصرح.")
    context.user_data["reject_id"] = int(q.data.split(":")[1])
    await q.message.reply_text("✏️ الرجاء إدخال سبب الرفض:")
    return REJECT_REASON


async def receive_reject_reason_shamcash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip()
    tx_id = context.user_data.pop("reject_id", None)
    if not tx_id:
        await update.message.reply_text("⚠️ حدث خطأ في معالجة الرفض. يرجى المحاولة مرة أخرى.")
        return ConversationHandler.END
    await run_db(store.update_transaction_status, "shamcash_withdrawals", tx_id, "rejected", reason, None, None, datetime.now())
    await run_db(store.add_audit_log, "shamcash_withdrawal", tx_id, "rejected", f"admin_{update.effective_user.id}", reason)
    tx = await run_db(store.get_transaction, "shamcash_withdrawals", tx_id)
    if tx:
        await run_db(store.add_balance, tx["user_id"], tx["requested_amount"])
        user_telegram = await run_db(store.get_user_telegram_by_id, tx["user_id"])
        if user_telegram:
            await notify_user(user_telegram, f"🚫 تم رفض طلب السحب #{tx_id}.\n📝 السبب: {reason}\n✅ تم إعادة رصيد {_fmt(tx['requested_amount'])} إلى حسابك.")
    await update.message.reply_text(f"تم تسجيل سبب الرفض للعملية #{tx_id}. ✅")
    return ConversationHandler.END


async def set_shamcash_txid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if int(update.effective_user.id) not in config.ADMIN_IDS:
        return await update.message.reply_text("❌ غير مصرح.")
    if len(context.args) < 2:
        return await update.message.reply_text("الاستخدام:\n<code>/set_shamcash_txid &lt;id&gt; &lt;txid&gt;</code>", parse_mode=ParseMode.HTML)
    try:
        tx_id, external_txid = int(context.args[0]), context.args[1]
    except Exception:
        return await update.message.reply_text("❌ معرف العملية أو معرف التحويل غير صالح.")
    tx = await run_db(store.get_transaction, "shamcash_withdrawals", tx_id)
    if not tx:
        return await update.message.reply_text("⚠️ العملية غير موجودة.")
    if tx["status"] not in ["approved_awaiting_txid", "pending"]:
        return await update.message.reply_text(f"⚠️ العملية #{tx_id} ليست في حالة انتظار معرف التحويل أو معلقة.")
    await run_db(store.finalize_shamcash_withdraw, tx_id, external_txid)
    await run_db(store.add_audit_log, "shamcash_withdrawal", tx_id, "approved", f"admin_{update.effective_user.id}", f"TxID set: {external_txid}")
    user_telegram = await run_db(store.get_user_telegram_by_id, tx["user_id"])
    if user_telegram:
        await notify_user(user_telegram, f"✅ تمت الموافقة على سحبك #{tx_id}.\n🆔 معرف التحويل: <code>{external_txid}</code>", parse_mode=ParseMode.HTML)
    await update.message.reply_text("تم تسجيل المعاملة بنجاح ✅")


async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text("❎ تم إلغاء العملية.")
        except Exception:
            pass
    elif update.message:
        await update.message.reply_text("❎ تم إلغاء العملية.")
    context.user_data.clear()
    return ConversationHandler.END


def register_handlers(app):
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(entry, pattern="^shamcash_withdraw$")],
        states={
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_amount)],
            WALLET: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_wallet)],
            CONFIRM: [CallbackQueryHandler(confirm, pattern="^confirm_withdraw$")],
            REJECT_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reject_reason_shamcash)],
        },
        fallbacks=[CallbackQueryHandler(cancel_action, pattern="^cancel_action$"), CommandHandler("cancel", cancel_action)],
        allow_reentry=True
    )
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(admin_approve_shamcash_withdraw, pattern="^admin_shamcash_approve"))
    app.add_handler(CallbackQueryHandler(admin_reject_shamcash_withdraw, pattern="^admin_shamcash_reject"))
    app.add_handler(CommandHandler("set_shamcash_txid", set_shamcash_txid))
