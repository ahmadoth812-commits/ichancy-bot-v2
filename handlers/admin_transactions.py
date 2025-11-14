# handlers/admin_transactions.py
import logging
import asyncio
import store
import config
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, ConversationHandler, filters
from utils.notifications import notify_user, notify_admin

logger = logging.getLogger(__name__)

ADMIN_REJECT_STATE = range(1)

async def run_db(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


async def fetch_pending_transactions():
    conn = await run_db(store.getDatabaseConnection)
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT 'syriatel_deposit' AS source_type, id, user_id, amount AS amount, status, txid, created_at
            FROM syriatel_transactions WHERE status='pending'
            UNION ALL
            SELECT 'shamcash_deposit' AS source_type, id, user_id, amount AS amount, status, txid, created_at
            FROM shamcash_transactions WHERE status='pending'
            UNION ALL
            SELECT 'coinex_withdraw' AS source_type, id, user_id, usdt_amount AS amount, status, chain, created_at
            FROM coinex_withdrawals WHERE status='pending'
            UNION ALL
            SELECT 'shamcash_withdraw' AS source_type, id, user_id, net_amount AS amount, status, wallet_address AS details, created_at
            FROM shamcash_withdrawals WHERE status='pending'
            UNION ALL
            SELECT 'syriatel_withdraw' AS source_type, id, user_id, net_amount AS amount, status, phone AS details, created_at
            FROM syriatel_withdrawals WHERE status='pending'
            ORDER BY created_at ASC
        """)
        results = cursor.fetchall()
        return results
    except Exception as e:
        logger.exception("Error fetching pending transactions: %s", e)
        return []
    finally:
        cursor.close()
        conn.close()


async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in config.ADMIN_IDS:
        await update.message.reply_text("❌ غير مصرح لك بالوصول إلى لوحة تحكم الأدمن.")
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 الطلبات المعلقة", callback_data="show_pending_admin")],
        [InlineKeyboardButton("🔍 سجل العمليات", callback_data="show_audit_log_admin")],
    ])
    await update.message.reply_text("⚙️ لوحة تحكم الأدمن:", reply_markup=kb)


async def show_pending_transactions_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in config.ADMIN_IDS:
        await q.edit_message_text("❌ غير مصرح لك.")
        return
    txs = await fetch_pending_transactions()
    if not txs:
        await q.edit_message_text("✅ لا توجد عمليات قيد الانتظار حالياً.")
        return
    for tx in txs:
        user = await run_db(store.get_user_by_id, tx["user_id"])
        username = user["username"] if user else f"ID: {tx['user_id']}"
        table_name = ""
        if "syriatel_deposit" in tx['source_type']: table_name = "syriatel_transactions"
        elif "shamcash_deposit" in tx['source_type']: table_name = "shamcash_transactions"
        elif "coinex_withdraw" in tx['source_type']: table_name = "coinex_withdrawals"
        elif "shamcash_withdraw" in tx['source_type']: table_name = "shamcash_withdrawals"
        elif "syriatel_withdraw" in tx['source_type']: table_name = "syriatel_withdrawals"
        else: table_name = "UNKNOWN"
        builder = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ موافقة", callback_data=f"approve_admin_{table_name}_{tx['id']}")],
            [InlineKeyboardButton("❌ رفض", callback_data=f"reject_admin_{table_name}_{tx['id']}")]
        ])
        details_info = ""
        if tx['source_type'] == 'coinex_withdraw':
            details_info = f"🔗 الشبكة: {tx.get('chain','')}\n"
        elif tx['source_type'] == 'shamcash_withdraw':
            details_info = f"🏦 المحفظة: {tx.get('details','')}\n"
        elif tx['source_type'] == 'syriatel_withdraw':
            details_info = f"📞 الرقم: {tx.get('details','')}\n"
        created = tx['created_at']
        # created may be datetime already; try format generically
        try:
            ts = created.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            try:
                ts = datetime.fromtimestamp(created).strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                ts = str(created)
        msg = (
            f"📌 <b>عملية جديدة ({tx['source_type']})</b>\n"
            f"👤 المستخدم: <a href='tg://user?id={user['telegram_id'] if user else tx['user_id']}'>{username}</a>\n"
            f"💰 المبلغ: {tx['amount']}\n"
            f"{details_info}"
            f"🕒 الوقت: {ts}"
        )
        await q.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=builder)


async def approve_transaction_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in config.ADMIN_IDS:
        await q.edit_message_text("❌ غير مصرح لك.")
        return
    parts = q.data.split("_")
    if len(parts) < 4:
        return await q.edit_message_text("⚠️ بيانات غير صحيحة.")
    table_name = parts[2]
    tx_id = int(parts[3])
    tx = await run_db(store.get_transaction, table_name, tx_id)
    if not tx or tx.get("status") != "pending":
        return await q.edit_message_text("❌ لم يتم العثور على العملية أو تمت مراجعتها.")
    user_id = tx["user_id"]
    amount = tx.get("amount") or tx.get("usdt_amount")
    if table_name == "coinex_withdrawals":
        await run_db(store.update_transaction_status, table_name, tx_id, "approved_by_admin", None, None, datetime.now(), None)
        await notify_user(await run_db(store.get_user_telegram_by_id, user_id), f"✅ تمت الموافقة على طلب سحب CoinEx الخاص بك #{tx_id}. قيد التنفيذ...")
        await run_db(store.add_audit_log, "coinex_withdrawals", tx_id, "approved_by_admin", f"admin_{q.from_user.id}")
        await q.edit_message_text(f"✅ تمت الموافقة المبدئية على سحب CoinEx رقم {tx_id}.")
        return
    if table_name in ("syriatel_transactions", "shamcash_transactions"):
        if table_name == "shamcash_transactions" and tx.get("currency") == "USD":
            rate = await run_db(store.get_usd_to_nsp_rate)
            amount = int(tx["amount"] * rate)
        await run_db(store.add_balance, user_id, amount)
        await run_db(store.update_transaction_status, table_name, tx_id, "approved", None, None, datetime.now(), None)
        await run_db(store.add_audit_log, table_name, tx_id, "approved", f"admin_{q.from_user.id}")
        await notify_user(await run_db(store.get_user_telegram_by_id, user_id), f"✅ تمت الموافقة على عملية الإيداع. المبلغ المضاف: {amount}")
        await q.edit_message_text(f"✅ تمت الموافقة على العملية رقم {tx_id} ({table_name})")
        return
    if table_name in ("shamcash_withdrawals", "syriatel_withdrawals"):
        await run_db(store.update_transaction_status, table_name, tx_id, "approved_awaiting_txid", None, None, datetime.now(), None)
        await run_db(store.add_audit_log, table_name, tx_id, "approved_awaiting_txid", f"admin_{q.from_user.id}")
        await notify_user(await run_db(store.get_user_telegram_by_id, user_id), f"✅ تمت الموافقة على طلب السحب الخاص بك #{tx_id}. يرجى انتظار معرف التحويل.")
        await q.edit_message_text(f"✅ تمت الموافقة المبدئية على العملية رقم {tx_id} ({table_name}).\nالرجاء إرسال معرف التحويل باستخدام الأمر /set_{table_name}_txid {tx_id} <TxID>")
        return
    await q.edit_message_text("⚠️ نوع العملية غير مدعوم للموافقة المباشرة من هنا.")


async def reject_transaction_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in config.ADMIN_IDS:
        return await q.edit_message_text("❌ غير مصرح لك.")
    parts = q.data.split("_")
    if len(parts) < 4:
        return await q.edit_message_text("⚠️ بيانات غير صحيحة.")
    table_name = parts[2]
    tx_id = int(parts[3])
    context.user_data["reject_table_name"] = table_name
    context.user_data["reject_tx_id"] = tx_id
    await q.message.reply_text("❌ يرجى إدخال سبب الرفض:")
    return 0  # ADMIN_REJECT_STATE


async def handle_reject_reason_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip()
    table_name = context.user_data.pop("reject_table_name", None)
    tx_id = context.user_data.pop("reject_tx_id", None)
    if not table_name or not tx_id:
        await update.message.reply_text("⚠️ حدث خطأ في معالجة الرفض. يرجى المحاولة مرة أخرى.")
        return ConversationHandler.END
    await run_db(store.update_transaction_status, table_name, tx_id, "rejected", reason, None, None, datetime.now())
    await run_db(store.add_audit_log, table_name, tx_id, "rejected", f"admin_{update.effective_user.id}", reason)
    tx = await run_db(store.get_transaction, table_name, tx_id)
    if tx:
        user_telegram = await run_db(store.get_user_telegram_by_id, tx["user_id"])
        if user_telegram:
            await notify_user(user_telegram, f"🚫 تم رفض عمليتك. السبب: {reason}")
    await update.message.reply_text(f"تم رفض العملية رقم {tx_id} ({table_name}) 🚫")
    return ConversationHandler.END


async def show_audit_log_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in config.ADMIN_IDS:
        await q.edit_message_text("❌ غير مصرح لك.")
        return
    conn = await run_db(store.getDatabaseConnection)
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 10")
        logs = cursor.fetchall()
    except Exception as e:
        logger.exception("Error fetching audit log: %s", e)
        logs = []
    finally:
        cursor.close()
        conn.close()
    if not logs:
        await q.edit_message_text("📭 لا يوجد سجل عمليات بعد.")
        return
    msg = "🧾 <b>آخر 10 عمليات في النظام:</b>\n\n"
    for log in logs:
        try:
            ts = log['created_at'].strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            ts = str(log['created_at'])
        msg += f"🕒 {ts} — {log.get('action','')} "
        if log.get('reason'):
            msg += f"(السبب: {log['reason']})"
        msg += f" — بواسطة {log.get('actor','system')}\n"
    await q.edit_message_text(msg, parse_mode=ParseMode.HTML)


def register_handlers(dp):
    dp.add_handler(CommandHandler("admin_panel", show_admin_panel, filters.User(config.ADMIN_IDS)))
    dp.add_handler(CallbackQueryHandler(show_pending_transactions_admin_callback, pattern="^show_pending_admin$", block=False))
    dp.add_handler(CallbackQueryHandler(show_audit_log_admin_callback, pattern="^show_audit_log_admin$", block=False))
    admin_reject_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(reject_transaction_admin, pattern="^reject_admin_")],
        states={0: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reject_reason_admin)]},
        fallbacks=[],
        map_to_parent={ConversationHandler.END: ConversationHandler.END}
    )
    dp.add_handler(admin_reject_conv)
    dp.add_handler(CallbackQueryHandler(approve_transaction_admin, pattern="^approve_admin_"))
