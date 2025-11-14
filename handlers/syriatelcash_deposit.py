# handlers/syriatelcash_deposit.py
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

AMOUNT, TXID = range(2)

async def run_db(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


async def start_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    numbers = await run_db(store.get_syriatel_numbers)
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
    await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    return AMOUNT


async def ask_txid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This handler receives the amount (from message) or is triggered after callback
    if update.callback_query:
        # user clicked "syriatel_done" — ask amount
        q = update.callback_query
        await q.answer()
        await q.message.reply_text(
            "💰 الرجاء إدخال المبلغ الذي قمت بتحويله (بالليرة السورية):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]])
        )
        return AMOUNT

    # if message: it's the amount provided
    txt = update.message.text.strip().replace(",", "")
    try:
        amount = int(txt)
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح للمبلغ.")
        return AMOUNT

    if amount < config.SYRIATEL_MIN_AMOUNT:
        await update.message.reply_text(f"⚠️ أقل مبلغ يمكن تحويله هو {config.SYRIATEL_MIN_AMOUNT:,} SYP.")
        return AMOUNT

    context.user_data["amount"] = amount
    await update.message.reply_text(
        "🔢 الرجاء إدخال رقم عملية التحويل (Transaction ID):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]])
    )
    return TXID


async def finalize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END

    txid = update.message.text.strip()
    amount = context.user_data.get("amount")
    user_telegram_id = str(update.effective_user.id)

    user = await run_db(store.get_user_by_telegram_id, user_telegram_id)
    if not user:
        await update.message.reply_text("⚠️ حسابك غير مسجل في النظام.")
        context.user_data.clear()
        return ConversationHandler.END

    # duplicate check
    existing_tx = await run_db(store._execute_query,
                               "SELECT id FROM syriatel_transactions WHERE txid = %s AND status != 'rejected'",
                               (txid,), fetchone=True)
    if existing_tx:
        await update.message.reply_text("⚠️ لقد قمت بتقديم طلب إيداع بنفس معرف المعاملة هذا من قبل.")
        context.user_data.clear()
        return ConversationHandler.END

    try:
        tx_id = await run_db(
            store._execute_query,
            """
            INSERT INTO syriatel_transactions (user_id, amount, txid, status, created_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user["id"], amount, txid, "pending", datetime.now())
        )
    except Exception as e:
        logger.exception("DB error inserting syriatel deposit: %s", e)
        await update.message.reply_text("❌ حدث خطأ في تسجيل الإيداع بقاعدة البيانات.")
        context.user_data.clear()
        return ConversationHandler.END

    if tx_id:
        await run_db(store.add_audit_log, "syriatel_deposit", tx_id, "pending", f"user_{user_telegram_id}", "User submitted deposit")
        await update.message.reply_text(
            "✅ تم تسجيل عملية الإيداع الخاصة بك.\n🕓 قيد المراجعة من قبل الإدارة.\n📩 سيتم إعلامك فور اتخاذ القرار."
        )
        context.user_data.clear()

        msg = (
            f"🔔 <b>طلب إيداع جديد عبر Syriatel Cash</b>\n\n"
            f"👤 المستخدم: <a href='tg://user?id={user_telegram_id}'>@{update.effective_user.username or update.effective_user.full_name}</a>\n"
            f"💰 المبلغ: <code>{amount:,} SYP</code>\n"
            f"🆔 معرف العملية: <code>{txid}</code>\n\n"
            f"يرجى المراجعة والموافقة أو الرفض."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ موافقة", callback_data=f"admin_approve_syriatel_dep:{tx_id}")],
            [InlineKeyboardButton("❌ رفض", callback_data=f"admin_reject_syriatel_dep:{tx_id}")]
        ])
        await notify_admin(msg, reply_markup=kb, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ حدث خطأ في تسجيل الإيداع بقاعدة البيانات.")
        context.user_data.clear()

    return ConversationHandler.END


async def admin_approve_syriatel_dep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    admin_id = int(q.from_user.id)
    if admin_id not in config.ADMIN_IDS:
        return await q.answer("❌ غير مصرح لك.")
    try:
        tx_id = int(q.data.split(":")[1])
    except Exception:
        return await q.answer("⚠️ معرف العملية غير صالح.")

    tx = await run_db(store.get_transaction, "syriatel_transactions", tx_id)
    if not tx or tx.get("status") != "pending":
        return await q.answer("⚠️ العملية غير موجودة أو تمت مراجعتها مسبقًا.")

    await run_db(store.add_balance, tx["user_id"], tx["amount"])
    await run_db(store.update_transaction_status, "syriatel_transactions", tx_id, "approved", None, None, datetime.now(), None)
    await run_db(store.add_audit_log, "syriatel_deposit", tx_id, "approved", f"admin_{admin_id}", "Deposit approved by admin")

    user_telegram = await run_db(store.get_user_telegram_by_id, tx["user_id"])
    if user_telegram:
        await notify_user(user_telegram,
                          f"✅ تمّت الموافقة على إيداعك #{tx_id}\n💰 المبلغ: {tx['amount']:,} SYP\n🕓 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    await q.edit_message_text(f"✅ تمت الموافقة على العملية #{tx_id} بنجاح.")


async def admin_reject_syriatel_dep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    admin_id = int(q.from_user.id)
    if admin_id not in config.ADMIN_IDS:
        return await q.answer("❌ غير مصرح لك.")
    tx_id = int(q.data.split(":")[1])
    context.user_data["reject_tx_id"] = tx_id
    await q.message.reply_text("🚫 الرجاء كتابة سبب الرفض:")
    return TXID  # reuse TXID state to capture reason (we'll treat TXID state as reason input here)


async def receive_reject_reason_syriatel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip()
    tx_id = context.user_data.pop("reject_tx_id", None)
    if not tx_id:
        await update.message.reply_text("⚠️ حدث خطأ في معالجة الرفض. يرجى المحاولة مرة أخرى.")
        return ConversationHandler.END

    await run_db(store.update_transaction_status, "syriatel_transactions", tx_id, "rejected", reason, None, None, datetime.now())
    await run_db(store.add_audit_log, "syriatel_deposit", tx_id, "rejected", f"admin_{update.effective_user.id}", reason)

    tx = await run_db(store.get_transaction, "syriatel_transactions", tx_id)
    if tx:
        user_telegram = await run_db(store.get_user_telegram_by_id, tx["user_id"])
        if user_telegram:
            await notify_user(user_telegram,
                              f"🚫 تم رفض عملية الإيداع #{tx_id}\n💰 المبلغ: {tx['amount']:,} SYP\n📝 السبب: {reason}")
    await update.message.reply_text(f"تم تسجيل رفض العملية #{tx_id} ✅")
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
        entry_points=[CallbackQueryHandler(start_deposit, pattern="^syriatel_deposit$")],
        states={
            AMOUNT: [CallbackQueryHandler(ask_txid, pattern="^syriatel_done$"), MessageHandler(filters.TEXT & ~filters.COMMAND, ask_txid)],
            TXID: [MessageHandler(filters.TEXT & ~filters.COMMAND, finalize)],
        },
        fallbacks=[CallbackQueryHandler(cancel_action, pattern="^cancel_action$"), CommandHandler("cancel", cancel_action)],
        allow_reentry=True
    )
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(admin_approve_syriatel_dep, pattern="^admin_approve_syriatel_dep"))
    app.add_handler(CallbackQueryHandler(admin_reject_syriatel_dep, pattern="^admin_reject_syriatel_dep"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reject_reason_syriatel))
