import logging
import store
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, ConversationHandler, filters
from utils.notifications import notify_user, notify_admin # Assuming notify_admin is also in utils

logger = logging.getLogger(__name__)

# Conversation states for admin rejection
ADMIN_REJECT_STATE = range(1)

# ========================== الدوال العامة ==========================
# تم نقل معظم دوال DB إلى store.py

async def fetch_pending_transactions():
    """إحضار كل العمليات المعلقة سواء سحب أو إيداع"""
    # Assuming store.py handles fetching from multiple tables
    conn = store.getDatabaseConnection()
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
        logger.error(f"Error fetching pending transactions: {e}")
        return []
    finally:
        cursor.close()
        conn.close()

# ========================== واجهة الأدمن ==========================
async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in config.ADMIN_IDS:
        await update.message.reply_text("❌ غير مصرح لك بالوصول إلى لوحة تحكم الأدمن.")
        return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 الطلبات المعلقة", callback_data="show_pending_admin")],
        [InlineKeyboardButton("🔍 سجل العمليات", callback_data="show_audit_log_admin")],
    ])
    await update.message.reply_text("⚙️ لوحة تحكم الأدمن:", reply_markup=kb)

# ========================== عرض الطلبات ==========================
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
        user = store.get_user_by_id(tx["user_id"])
        username = user["username"] if user else f"ID: {tx['user_id']}"
        
        # Determine appropriate table for update based on source_type
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
            details_info = f"🔗 الشبكة: {tx['chain']}\n"
        elif tx['source_type'] == 'shamcash_withdraw':
            details_info = f"🏦 المحفظة: {tx['details']}\n"
        elif tx['source_type'] == 'syriatel_withdraw':
            details_info = f"📞 الرقم: {tx['details']}\n"

        msg = (
            f"📌 <b>عملية جديدة ({tx['source_type']})</b>\n"
            f"👤 المستخدم: <a href='tg://user?id={user['telegram_id']}'>{username}</a>\n"
            f"💰 المبلغ: {tx['amount']}\n"
            f"{details_info}"
            f"🕒 الوقت: {datetime.fromtimestamp(tx['created_at']).strftime('%Y-%m-%d %H:%M:%S')}"
        )

        await q.message.reply_text(msg, parse_mode="HTML", reply_markup=builder)

# ========================== الموافقة ==========================
async def approve_transaction_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in config.ADMIN_IDS:
        await q.edit_message_text("❌ غير مصرح لك.")
        return

    _, _, table_name, tx_id = q.data.split("_")
    tx_id = int(tx_id)

    tx = store.get_transaction(table_name, tx_id)
    if not tx or tx["status"] != "pending":
        await q.edit_message_text("❌ لم يتم العثور على العملية أو تمت مراجعتها.")
        return

    user_id = tx["user_id"]
    amount = tx.get("amount") or tx.get("usdt_amount") # amount can be syr, usd, or usdt_amount for coinex

    # Special handling for coinex_withdraw, as it needs to trigger actual CoinEx API
    if table_name == "coinex_withdrawals":
        await notify_admin(f"⚠️ الأدمن {q.from_user.username} قام بالموافقة على سحب CoinEx #{tx_id}. يرجى معالجته يدوياً أو تفعيل المعالجة الآلية.", parse_mode="HTML")
        await q.edit_message_text(f"✅ تمت الموافقة المبدئية على سحب CoinEx رقم {tx_id}. يرجى إكمال التنفيذ يدوياً إذا لم يكن آلياً.")
        
        # If automatic execution is desired here, you would call coinex_adapter.withdraw_coinex
        # For now, let's assume admin_coinex_approve (from coinex_withdraw.py) handles the actual API call
        # This callback would just update the status to 'approved_by_admin' or similar,
        # and then the coinex_withdraw handler would pick it up or admin performs manually.
        store.update_transaction_status(table_name, tx_id, "approved_by_admin", approved_at=datetime.now())
        await notify_user(store.get_user_telegram_by_id(user_id), f"✅ تمت الموافقة على طلب سحب CoinEx الخاص بك #{tx_id}. قيد التنفيذ...")
        await store.add_audit_log("coinex_withdrawals", tx_id, "approved_by_admin", actor=f"admin_{q.from_user.id}")
        return
        
    # For deposits: Add balance
    if table_name in ("syriatel_transactions", "shamcash_transactions"):
        # Convert ShamCash USD to NSP if needed
        if table_name == "shamcash_transactions" and tx["currency"] == "USD":
            rate = store.get_usd_to_nsp_rate()
            amount = int(tx["amount"] * rate)
        
        store.add_balance(user_id, amount)
        store.update_transaction_status(table_name, tx_id, "approved", approved_at=datetime.now())
        await notify_user(store.get_user_telegram_by_id(user_id), f"✅ تمت الموافقة على عملية الإيداع ({table_name.replace('_transactions','')}).\n💰 المبلغ المضاف: {amount}")
        await store.add_audit_log(table_name, tx_id, "approved", actor=f"admin_{q.from_user.id}")
        await q.edit_message_text(f"✅ تمت الموافقة على العملية رقم {tx_id} ({table_name})")

    # For withdrawals that are NOT CoinEx (ShamCash, Syriatel)
    elif table_name in ("shamcash_withdrawals", "syriatel_withdrawals"):
        # For these, approval means admin will send money and provide TXID later
        store.update_transaction_status(table_name, tx_id, "approved_awaiting_txid", approved_at=datetime.now())
        await notify_user(store.get_user_telegram_by_id(user_id), f"✅ تمت الموافقة على طلب السحب الخاص بك #{tx_id}. يرجى انتظار معرف التحويل.")
        await store.add_audit_log(table_name, tx_id, "approved_awaiting_txid", actor=f"admin_{q.from_user.id}")
        await q.edit_message_text(
            f"✅ تمت الموافقة المبدئية على العملية رقم {tx_id} ({table_name}).\n"
            f"الرجاء إرسال معرف التحويل (TxID) باستخدام الأمر /set_{table_name}_txid {tx_id} <TxID>"
        )
        
    else:
        await q.edit_message_text("⚠️ نوع العملية غير مدعوم للموافقة المباشرة من هنا.")


# ========================== الرفض ==========================
async def reject_transaction_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in config.ADMIN_IDS:
        await q.edit_message_text("❌ غير مصرح لك.")
        return

    _, _, table_name, tx_id = q.data.split("_")
    context.user_data["reject_table_name"] = table_name
    context.user_data["reject_tx_id"] = int(tx_id)

    await q.message.reply_text("❌ يرجى إدخال سبب الرفض:")
    return ADMIN_REJECT_STATE # Enter the conversation state

async def handle_reject_reason_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip()
    table_name = context.user_data.pop("reject_table_name", None)
    tx_id = context.user_data.pop("reject_tx_id", None)

    if not table_name or not tx_id:
        await update.message.reply_text("⚠️ حدث خطأ في معالجة الرفض. يرجى المحاولة مرة أخرى.")
        return ConversationHandler.END

    store.update_transaction_status(table_name, tx_id, "rejected", reason=reason, rejected_at=datetime.now())
    await update.message.reply_text(f"تم رفض العملية رقم {tx_id} ({table_name}) 🚫")

    tx = store.get_transaction(table_name, tx_id)
    if tx:
        user_telegram_id = store.get_user_telegram_by_id(tx["user_id"])
        if user_telegram_id:
            await notify_user(user_telegram_id, f"🚫 تم رفض عمليتك ({table_name.replace('_transactions','').replace('_withdrawals','')}). السبب: {reason}")
        await store.add_audit_log(table_name, tx_id, "rejected", actor=f"admin_{update.effective_user.id}", reason=reason)

    return ConversationHandler.END


# ========================== سجل العمليات ==========================
async def show_audit_log_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in config.ADMIN_IDS:
        await q.edit_message_text("❌ غير مصرح لك.")
        return

    conn = store.getDatabaseConnection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 10")
        logs = cursor.fetchall()
    except Exception as e:
        logger.error(f"Error fetching audit log: {e}")
        logs = []
    finally:
        cursor.close()
        conn.close()

    if not logs:
        await q.edit_message_text("📭 لا يوجد سجل عمليات بعد.")
        return

    msg = "🧾 <b>آخر 10 عمليات في النظام:</b>\n\n"
    for log in logs:
        msg += f"🕒 {datetime.fromtimestamp(log['created_at'].timestamp()).strftime('%Y-%m-%d %H:%M:%S')} — {log['action']}"
        if log['reason']:
            msg += f" (السبب: {log['reason']})"
        msg += f" — بواسطة {log['actor']}\n"
    await q.edit_message_text(msg, parse_mode="HTML")


# ========================== تسجيل الهاندلرز ==========================
def register_handlers(dp):
    dp.add_handler(CommandHandler("admin_panel", show_admin_panel, filters.User(config.ADMIN_IDS)))
    dp.add_handler(CallbackQueryHandler(show_pending_transactions_admin_callback, pattern="^show_pending_admin$", block=False))
    dp.add_handler(CallbackQueryHandler(show_audit_log_admin_callback, pattern="^show_audit_log_admin$", block=False))
    
    # Conversation handler for admin rejection reason
    admin_reject_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(reject_transaction_admin, pattern="^reject_admin_")],
        states={
            ADMIN_REJECT_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reject_reason_admin)],
        },
        fallbacks=[],
        map_to_parent={ ConversationHandler.END: ConversationHandler.END } # allows nested convos to end parent
    )
    dp.add_handler(admin_reject_conv)
    
    dp.add_handler(CallbackQueryHandler(approve_transaction_admin, pattern="^approve_admin_"))
