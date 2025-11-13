import mysql.connector
from datetime import datetime
import logging
import config

# إعداد الـ logger
logger = logging.getLogger(__name__)

# ==========================
# اتصال قاعدة البيانات
# ==========================
def getDatabaseConnection():
    """Connects to MySQL database."""
    return mysql.connector.connect(
        host=config.DB_HOST,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=config.DB_NAME
    )

# ==========================
# دالة تنفيذ عامة للاستعلامات
# ==========================
def _execute_query(sql, params=None, fetch=False, fetchone=False):
    conn = getDatabaseConnection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(sql, params)
        if fetchone:
            result = cursor.fetchone()
        elif fetch:
            result = cursor.fetchall()
        else:
            result = cursor.lastrowid
        conn.commit()
        return result
    except mysql.connector.Error as err:
        logger.error(f"Database Error: {err}")
        conn.rollback()
        return None
    finally:
        cursor.close()
        conn.close()

# ==========================
# دوال المستخدمين
# ==========================
def get_user_by_id(user_id):
    """Retrieve user by internal user ID."""
    return _execute_query("SELECT * FROM users WHERE id = %s", (user_id,), fetchone=True)

def get_user_by_telegram_id(telegram_id):
    """Retrieve user by Telegram ID."""
    return _execute_query("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,), fetchone=True)

def get_user_telegram_by_id(user_id):
    """Retrieve Telegram ID for a given internal user ID."""
    result = _execute_query("SELECT telegram_id FROM users WHERE id = %s", (user_id,), fetchone=True)
    return result["telegram_id"] if result else None

def get_user_balance(user_id):
    """Get current balance for a user."""
    result = _execute_query("SELECT balance FROM users WHERE id = %s", (user_id,), fetchone=True)
    return result["balance"] if result else 0

def add_balance(user_id, amount):
    """Add amount to user's balance."""
    _execute_query("UPDATE users SET balance = balance + %s WHERE id = %s", (amount, user_id))

def deduct_balance(user_id, amount):
    """Deduct amount from user's balance."""
    _execute_query("UPDATE users SET balance = balance - %s WHERE id = %s", (amount, user_id))

# ==========================
# دوال المعاملات
# ==========================
def get_transaction(table_name, tx_id):
    """Get a transaction by its ID from a specific table."""
    valid_tables = [
        "syriatel_transactions", "shamcash_transactions",
        "coinex_transactions", "coinex_withdrawals",
        "shamcash_withdrawals", "syriatel_withdrawals"
    ]
    if table_name not in valid_tables:
        logger.error(f"Invalid table name: {table_name}")
        return None
    return _execute_query(f"SELECT * FROM {table_name} WHERE id = %s", (tx_id,), fetchone=True)

def update_transaction_status(
    table_name,
    tx_id,
    status,
    reason=None,
    txid_external=None,
    approved_at=None,
    rejected_at=None
):
    """Update transaction status and optional details."""
    valid_tables = [
        "syriatel_transactions", "shamcash_transactions",
        "coinex_transactions", "coinex_withdrawals",
        "shamcash_withdrawals", "syriatel_withdrawals"
    ]
    if table_name not in valid_tables:
        logger.error(f"Error: Invalid table name {table_name} in update_transaction_status")
        return

    sql_parts = ["status = %s"]
    params = [status]

    if reason is not None:
        sql_parts.append("reason = %s")
        params.append(reason)

    if txid_external is not None:
        # توحيد اسم العمود لجميع الأنواع
        txid_column = "txid"
        if table_name == "coinex_withdrawals":
            txid_column = "coinex_txid"
        sql_parts.append(f"{txid_column} = %s")
        params.append(txid_external)

    if approved_at is not None:
        sql_parts.append("approved_at = %s")
        params.append(approved_at)

    if rejected_at is not None:
        sql_parts.append("rejected_at = %s")
        params.append(rejected_at)

    params.append(tx_id)
    sql = f"UPDATE {table_name} SET {', '.join(sql_parts)} WHERE id = %s"
    _execute_query(sql, params)

def add_audit_log(source, tx_id, action, actor="system", reason=None):
    """Add an entry to the audit log."""
    _execute_query(
        "INSERT INTO audit_log (source, tx_id, action, actor, reason, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
        (source, tx_id, action, actor, reason, datetime.now())
    )

# ==========================
# معدلات التحويل
# ==========================
def get_usd_to_nsp_rate():
    """Get the current USD to NSP conversion rate."""
    # يمكن لاحقًا سحبها من قاعدة بيانات أو لوحة تحكم
    return 5000  # معدل افتراضي

# ==========================
# دوال أخرى
# ==========================
def get_syriatel_numbers():
    """Get a list of Syriatel deposit numbers."""
    # مستقبلاً يمكن حفظها بجدول syriatel_numbers
    return ["099xxxxxxxx", "098xxxxxxxx"]

def is_coinex_address_whitelisted(address):
    """Check if a CoinEx withdrawal address is whitelisted."""
    # لاحقًا تحقق من جدول فعلي
    logger.info(f"Checking if address {address} is whitelisted (currently True for demo)")
    return True

def get_user_telegram_by_tx(table_name, tx_id):
    """Get Telegram ID of user associated with a transaction."""
    if table_name not in ["shamcash_withdrawals", "syriatel_withdrawals"]:
        return None
    tx = get_transaction(table_name, tx_id)
    if tx:
        user = get_user_by_id(tx["user_id"])
        return user["telegram_id"] if user else None
    return None

def finalize_shamcash_withdraw(tx_id, external_txid):
    """Finalize ShamCash withdrawal with external transaction ID."""
    _execute_query(
        "UPDATE shamcash_withdrawals SET status = %s, txid = %s, approved_at = %s WHERE id = %s",
        ("approved", external_txid, datetime.now(), tx_id)
    )
