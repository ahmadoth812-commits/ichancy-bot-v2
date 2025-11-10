# handlers/admin_transactions.py
import sqlite3
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime
from database.store import get_user_by_id, update_user_balance, add_audit_log
from utils.notifications import notify_user

router = Router()
DB_PATH = "database/ichancy.db"


# ========================== الدوال العامة ==========================
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_pending_transactions():
    """إحضار كل العمليات المعلقة سواء سحب أو إيداع"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT 'syriatel' AS source, id, user_id, amount_syp AS amount, status, txid, created_at
        FROM syriatel_transactions WHERE status='pending'
        UNION
        SELECT 'shamcash' AS source, id, user_id, amount_usd AS amount, status, txid, created_at
        FROM shamcash_transactions WHERE status='pending'
        UNION
        SELECT 'coinex_withdraw' AS source, id, user_id, amount_usdt AS amount, status, txid, created_at
        FROM coinex_withdrawals WHERE status='pending'
        ORDER BY created_at ASC
    """)
    results = cur.fetchall()
    conn.close()
    return results


def update_transaction_status(source, tx_id, status, reason=None):
    """تحديث حالة العملية (موافقة / رفض)"""
    conn = get_db_connection()
    cur = conn.cursor()

    table_map = {
        "syriatel": "syriatel_transactions",
        "shamcash": "shamcash_transactions",
        "coinex_withdraw": "coinex_withdrawals"
    }

    table = table_map.get(source)
    if not table:
        return

    if reason:
        cur.execute(f"UPDATE {table} SET status=?, reason=? WHERE id=?", (status, reason, tx_id))
    else:
        cur.execute(f"UPDATE {table} SET status=?, reason=NULL WHERE id=?", (status, tx_id))

    conn.commit()
    conn.close()


def credit_user_balance(user_id, amount, currency="nsp"):
    """زيادة رصيد المستخدم بعد الموافقة"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()


# ========================== واجهة الأدمن ==========================
@router.message(Command("admin_panel"))
async def show_admin_panel(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.button(text="📥 الطلبات المعلقة", callback_data="show_pending")
    builder.button(text="🔍 سجل العمليات", callback_data="show_audit_log")
    builder.adjust(1)
    await message.answer("⚙️ لوحة تحكم الأدمن:", reply_markup=builder.as_markup())


# ========================== عرض الطلبات ==========================
@router.callback_query(F.data == "show_pending")
async def show_pending_transactions(call: types.CallbackQuery):
    txs = fetch_pending_transactions()
    if not txs:
        await call.message.answer("✅ لا توجد عمليات قيد الانتظار حالياً.")
        return

    for tx in txs:
        user = get_user_by_id(tx["user_id"])
        username = user["username"] if user else "مستخدم مجهول"

        builder = InlineKeyboardBuilder()
        builder.button(text="✅ موافقة", callback_data=f"approve_{tx['source']}_{tx['id']}")
        builder.button(text="❌ رفض", callback_data=f"reject_{tx['source']}_{tx['id']}")
        builder.adjust(2)

        msg = (
            f"📌 <b>عملية جديدة ({tx['source']})</b>\n"
            f"👤 المستخدم: {username}\n"
            f"💰 المبلغ: {tx['amount']}\n"
            f"🕒 الوقت: {datetime.fromtimestamp(tx['created_at']).strftime('%Y-%m-%d %H:%M:%S')}"
        )

        await call.message.answer(msg, parse_mode="HTML", reply_markup=builder.as_markup())


# ========================== الموافقة ==========================
@router.callback_query(F.data.startswith("approve_"))
async def approve_transaction(call: types.CallbackQuery):
    _, source, tx_id = call.data.split("_")
    tx_id = int(tx_id)

    conn = get_db_connection()
    cur = conn.cursor()
    if source == "syriatel":
        cur.execute("SELECT user_id, amount_syp AS amount FROM syriatel_transactions WHERE id=?", (tx_id,))
    elif source == "shamcash":
        cur.execute("SELECT user_id, amount_usd AS amount FROM shamcash_transactions WHERE id=?", (tx_id,))
    elif source == "coinex_withdraw":
        cur.execute("SELECT user_id, net_amount_usdt AS amount FROM coinex_withdrawals WHERE id=?", (tx_id,))
    else:
        await call.answer("⚠️ نوع المعاملة غير معروف.", show_alert=True)
        conn.close()
        return

    tx = cur.fetchone()
    conn.close()

    if not tx:
        await call.answer("❌ لم يتم العثور على العملية.")
        return

    user_id = tx["user_id"]
    amount = tx["amount"]

    # اعتماد العملية حسب نوعها
    if source in ("syriatel", "shamcash"):
        credit_user_balance(user_id, amount)
        update_transaction_status(source, tx_id, "approved")
        await notify_user(user_id, f"✅ تمت الموافقة على عملية الإيداع ({source}).\n💰 المبلغ المضاف: {amount}")
    elif source == "coinex_withdraw":
        update_transaction_status(source, tx_id, "approved")
        await notify_user(user_id, f"✅ تمت الموافقة على عملية السحب ({source}).")

    await add_audit_log(user_id, f"Admin approved {source} transaction ID: {tx_id}")
    await call.message.answer(f"تمت الموافقة على العملية رقم {tx_id} ({source}) ✅")


# ========================== الرفض ==========================
@router.callback_query(F.data.startswith("reject_"))
async def reject_transaction(call: types.CallbackQuery):
    _, source, tx_id = call.data.split("_")
    tx_id = int(tx_id)

    await call.message.answer("❌ يرجى إدخال سبب الرفض:")
    call.message.bot.session = {"reject_source": source, "reject_id": tx_id, "admin_id": call.from_user.id}


@router.message(lambda m: m.text and "reject_source" in getattr(m.bot, "session", {}))
async def handle_reject_reason(message: types.Message):
    session = message.bot.session
    reason = message.text
    source = session["reject_source"]
    tx_id = session["reject_id"]

    update_transaction_status(source, tx_id, "rejected", reason)
    await message.answer(f"تم رفض العملية رقم {tx_id} ({source}) 🚫")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM shamcash_transactions WHERE id=?", (tx_id,))
    tx = cur.fetchone()
    conn.close()

    if tx:
        await notify_user(tx["user_id"], f"🚫 تم رفض عمليتك ({source}). السبب: {reason}")
        await add_audit_log(tx["user_id"], f"Admin rejected {source} transaction ID: {tx_id} - reason: {reason}")


# ========================== سجل العمليات ==========================
@router.callback_query(F.data == "show_audit_log")
async def show_audit_log(call: types.CallbackQuery):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 10")
    logs = cur.fetchall()
    conn.close()

    if not logs:
        await call.message.answer("📭 لا يوجد سجل عمليات بعد.")
        return

    msg = "🧾 <b>آخر 10 عمليات في النظام:</b>\n\n"
    for log in logs:
        msg += f"🕒 {datetime.fromtimestamp(log['created_at']).strftime('%Y-%m-%d %H:%M:%S')} — {log['action']}\n"
    await call.message.answer(msg, parse_mode="HTML")
