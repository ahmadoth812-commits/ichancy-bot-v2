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
    return _execute_query("SELECT * FROM users WHERE id = %s", (user_id,), fetchone=True)

def get_user_by_telegram_id(telegram_id):
    return _execute_query("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,), fetchone=True)

def get_user_telegram_by_id(user_id):
    result = _execute_query("SELECT telegram_id FROM users WHERE id = %s", (user_id,), fetchone=True)
    return result["telegram_id"] if result else None

def get_user_balance(user_id):
    result = _execute_query("SELECT balance FROM users WHERE id = %s", (user_id,), fetchone=True)
    return result["balance"] if result else 0

def add_balance(user_id, amount):
    _execute_query("UPDATE users SET balance = balance + %s WHERE id = %s", (amount, user_id))

def deduct_balance(user_id, amount):
    _execute_query("UPDATE users SET balance = balance - %s WHERE id = %s", (amount, user_id))

# ==========================
# دوال المعاملات
# ==========================
def get_transaction(table_name, tx_id):
    valid_tables = [
        "syriatel_transactions", "shamcash_transactions",
        "coinex_transactions", "coinex_withdrawals",
        "shamcash_withdrawals", "syriatel_withdrawals"
    ]
    if table_name not in valid_tables:
        logger.error(f"Invalid table name: {table_name}")
        return None
    return _execute_query(f"SELECT * FROM {table_name} WHERE id = %s", (tx_id,), fetchone=True)

def update_transaction_status(table_name, tx_id, status, reason=None, txid_external=None, approved_at=None, rejected_at=None):
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
    _execute_query(
        "INSERT INTO audit_log (source, tx_id, action, actor, reason, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
        (source, tx_id, action, actor, reason, datetime.now())
    )

# ==========================
# إعدادات ديناميكية
# ==========================
def get_usd_to_nsp_rate():
    result = _execute_query("SELECT value FROM settings WHERE key_name = %s", ("usd_to_nsp_rate",), fetchone=True)
    if result and result["value"].isdigit():
        return int(result["value"])
    return 5000

def update_usd_to_nsp_rate(new_rate):
    _execute_query(
        "UPDATE settings SET value = %s, updated_at = NOW() WHERE key_name = %s",
        (str(new_rate), "usd_to_nsp_rate")
    )
    add_audit_log("system", 0, "update_rate", "admin", f"New rate set to {new_rate}")

def get_syriatel_numbers():
    result = _execute_query("SELECT value FROM settings WHERE key_name = %s", ("syriatel_numbers",), fetchone=True)
    if result and result["value"]:
        return [num.strip() for num in result["value"].split(',')]
    return ["099xxxxxxxx", "098xxxxxxxx"]

def update_syriatel_numbers(numbers_list):
    numbers_str = ",".join(numbers_list)
    _execute_query(
        "UPDATE settings SET value = %s, updated_at = NOW() WHERE key_name = %s",
        (numbers_str, "syriatel_numbers")
    )
    add_audit_log("system", 0, "update_numbers", "admin", f"Updated Syriatel numbers to {numbers_str}")

def get_shamcash_wallet():
    result = _execute_query("SELECT value FROM settings WHERE key_name = %s", ("shamcash_wallet",), fetchone=True)
    if result and result["value"]:
        return result["value"]
    return "Not Configured"

def update_shamcash_wallet(new_wallet):
    _execute_query(
        "UPDATE settings SET value = %s, updated_at = NOW() WHERE key_name = %s",
        (new_wallet, "shamcash_wallet")
    )
    add_audit_log("system", 0, "update_wallet", "admin", f"Updated ShamCash wallet to {new_wallet}")

# ==========================
# إدارة العناوين الموثوقة - ✅ الجديد والمصحح
# ==========================
def add_whitelisted_address(user_id, address, chain, label=None):
    return _execute_query(
        "INSERT INTO coinex_whitelisted_addresses (user_id, address, chain, label) VALUES (%s, %s, %s, %s)",
        (user_id, address, chain, label)
    )

def get_whitelisted_addresses(user_id, chain=None):
    if chain:
        return _execute_query(
            "SELECT * FROM coinex_whitelisted_addresses WHERE user_id = %s AND chain = %s AND is_active = TRUE",
            (user_id, chain), fetch=True
        )
    else:
        return _execute_query(
            "SELECT * FROM coinex_whitelisted_addresses WHERE user_id = %s AND is_active = TRUE",
            (user_id,), fetch=True
        )

def is_coinex_address_whitelisted(user_id, address, chain):
    """✅ الدالة المصححة - تتحقق من العنوان للمستخدم المحدد"""
    result = _execute_query(
        "SELECT id FROM coinex_whitelisted_addresses WHERE user_id = %s AND address = %s AND chain = %s AND is_active = TRUE",
        (user_id, address, chain), fetchone=True
    )
    return result is not None

def remove_whitelisted_address(address_id):
    return _execute_query(
        "UPDATE coinex_whitelisted_addresses SET is_active = FALSE WHERE id = %s",
        (address_id,)
    )

# ==========================
# دوال إضافية
# ==========================
def get_user_telegram_by_tx(table_name, tx_id):
    if table_name not in ["shamcash_withdrawals", "syriatel_withdrawals"]:
        return None
    tx = get_transaction(table_name, tx_id)
    if tx:
        user = get_user_by_id(tx["user_id"])
        return user["telegram_id"] if user else None
    return None

def finalize_shamcash_withdraw(tx_id, external_txid):
    _execute_query(
        "UPDATE shamcash_withdrawals SET status = %s, txid = %s, approved_at = %s WHERE id = %s",
        ("approved", external_txid, datetime.now(), tx_id)
    )