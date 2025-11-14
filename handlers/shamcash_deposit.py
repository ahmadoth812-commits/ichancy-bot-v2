# handlers/shamcash_deposit.py
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

CURRENCY, AMOUNT, TXID, ADMIN_REJECT_REASON = range(4)

async def run_db(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


async def start_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    text = "💵 اختر نوع العملة التي قمت بالتحويل بها:"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇺🇸 USD", callback_data="shamcash_usd"),
         InlineKeyboardButton("🇸🇾 NSP", callback_data="shamcash_nsp")],
        [InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]
    ])
    await q.edit_message_text(text, reply_markup=kb)
    return CURRENCY


async def ask_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    context.user_data["currency"] = "USD" if "usd" in q.data else "NSP"
    currency = context.user_data["currency"]

    shamcash_wallet = await run_db(store.get_shamcash_wallet)

    if not shamcash_wallet or shamcash_wallet in ("Not Configured", "غير محدد", ""):
        await q.edit_message_text("⚠️ لم يتم ضبط عنوان محفظة ShamCash بعد. يرجى المحاولة لاحقًا.")
        return ConversationHandler.END

    text = (
        f"📍 عنوان محفظة الإيداع:\n<code>{shamcash_wallet}</code>\n\n"
        f"💰 الرجاء إدخال المبلغ الذي قمت بتحويله ({currency}):"
    )
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]]), parse_mode=ParseMode.HTML)
    return AMOUNT


async def ask_txid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
    except Exception:
        await update.message.reply_text("❌ الرجاء إدخال رقم صالح.")
        return AMOUNT

    cur = context.user_data["currency"]
    min_amount = config.SHAMCASH_MIN_USD if cur == "USD" else config.SHAMCASH_MIN_NSP
    if amount < min_amount:
        await update.message.reply_text(f"⚠️ الحد الأدنى للإيداع هو {min_amount} {cur}.")
        return AMOUNT

    context.user_data["amount"] = amount
    await update.message.reply_text("🔢 الرجاء إدخال معرف عملية التحويل (TxID):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]]))
    return TXID


async def finalize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txid = update.message.text.strip()
    data = context.user_data
    currency, amount = data["currency"], data["amount"]
    user_telegram_id = str(update.effective_user.id)
    user = await run_db(store.get_user_by_telegram_id, user_telegram_id)
    if not user:
        await update.message.reply_text("⚠️ حسابك غير مسجل.")
        context.user_data.clear()
        return ConversationHandler.END

    existing_tx = await run_db(store._execute_query, "SELECT id FROM shamcash_transactions WHERE txid = %s AND status != 'rejected'", (txid,), fetchone=True)
    if existing_tx:
        await update.message.reply_text("⚠️ لقد قمت بتقديم طلب إيداع بنفس معرف المعاملة هذا من قبل.")
        context.user_data.clear()
        return ConversationHandler.END

    tx_id = await run_db(store._execute_query, """
        INSERT INTO shamcash_transactions (user_id, currency, amount, txid, status, created_at)
        VALUES (%s,%s,%s,%s,%s,%s)
    """, (user["id"], currency, amount, txid, "pending", datetime.now()))
    if tx_id:
        await run_db(store.add_audit_log, "shamcash_deposit", tx_id, "pending", f"user_{user_telegram_id}", f"User submitted deposit in {currency}")
        await update.message.reply_text("✅ تم تسجيل طلب الإيداع بانتظار مراجعة الإدارة.")
        context.user_data.clear()
        shamcash_wallet = await run_db(store.get_shamcash_wallet) or "غير محدد"
        msg = (
            f"🔔 <b>طلب إيداع جديد عبر ShamCash</b>\n\n"
            f"👤 المستخدم: <a href='tg://user?id={user_telegram_id}'>@{update.effective_user.username or update.effective_user.full_name}</a>\n"
            f"💰 المبلغ: <code>{amount}</code> {currency}\n"
            f"🆔 TxID: <code>{txid}</code>\n"
            f"🏦 المحفظة المستلمة: <code>{shamcash_wallet}</code>\n"
            f"رقم العملية: <code>{tx_id}</code>"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ موافقة", callback_data=f"admin_approve_shamcash_dep:{tx_id}")],
            [InlineKeyboardButton("❌ رفض", callback_data=f"admin_reject_shamcash_dep:{tx_id}")]
        ])
        await notify_admin(msg, reply_markup=kb, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ حدث خطأ في تسجيل الإيداع بقاعدة البيانات.")
        context.user_data.clear()
    return ConversationHandler.END


async def admin_approve_dep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if int(q.from_user.id) not in config.ADMIN_IDS:
        return await q.answer("❌ غير مصرح.")
    tx_id = int(q.data.split(":")[1])
    tx = await run_db(store.get_transaction, "shamcash_transactions", tx_id)
    if not tx or tx["status"] != "pending":
        return await q.answer("⚠️ العملية غير موجودة أو تمت مراجعتها سابقًا.")
    value = tx["amount"]
    if tx["currency"] == "USD":
        rate = await run_db(store.get_usd_to_nsp_rate)
        value = int(value * rate)
    await run_db(store.add_balance, tx["user_id"], value)
    await run_db(store.update_transaction_status, "shamcash_transactions", tx_id, "approved", None, None, datetime.now(), None)
    await run_db(store.add_audit_log, "shamcash_deposit", tx_id, "approved", f"admin_{q.from_user.id}", "Admin approved deposit")
    user_telegram = await run_db(store.get_user_telegram_by_id, tx["user_id"])
    if user_telegram:
        await notify_user(user_telegram, f"✅ تمت الموافقة على إيداعك #{tx_id} بمبلغ <b>{value} NSP</b>.", parse_mode=ParseMode.HTML)
    await q.edit_message_text(f"✅ تمت الموافقة على العملية #{tx_id}.")


async def admin_reject_dep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if int(q.from_user.id) not in config.ADMIN_IDS:
        return await q.answer("❌ غير مصرح.")
    tx_id = int(q.data.split(":")[1])
    context.user_data["reject_tx_id"] = tx_id
    await q.message.reply_text("✏️ الرجاء إدخال سبب الرفض:")
    return ADMIN_REJECT_REASON


async def receive_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip()
    tx_id = context.user_data.pop("reject_tx_id", None)
    if not tx_id:
        await update.message.reply_text("⚠️ حدث خطأ في معالجة الرفض. يرجى المحاولة مرة أخرى.")
        return ConversationHandler.END
    await run_db(store.update_transaction_status, "shamcash_transactions", tx_id, "rejected", reason, None, None, datetime.now())
    await run_db(store.add_audit_log, "shamcash_deposit", tx_id, "rejected", f"admin_{update.effective_user.id}", reason)
    tx = await run_db(store.get_transaction, "shamcash_transactions", tx_id)
    if tx:
        user_telegram = await run_db(store.get_user_telegram_by_id, tx["user_id"])
        if user_telegram:
            await notify_user(user_telegram, f"🚫 تم رفض عملية الإيداع #{tx_id}.\n📝 السبب: {reason}")
    await update.message.reply_text(f"✅ تم تسجيل سبب الرفض للعملية #{tx_id}.")
    context.user_data.clear()
    return ConversationHandler.END


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
        entry_points=[CallbackQueryHandler(start_deposit, pattern="^shamcash_deposit$")],
        states={
            CURRENCY: [CallbackQueryHandler(ask_amount, pattern="^shamcash_(usd|nsp)$")],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_txid)],
            TXID: [MessageHandler(filters.TEXT & ~filters.COMMAND, finalize)],
            ADMIN_REJECT_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reject_reason)],
        },
        fallbacks=[CallbackQueryHandler(cancel_action, pattern="^cancel_action$"), CommandHandler("cancel", cancel_action)],
        allow_reentry=True
    )
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(admin_approve_dep, pattern="^admin_approve_shamcash_dep"))
    app.add_handler(CallbackQueryHandler(admin_reject_dep, pattern="^admin_reject_shamcash_dep"))
