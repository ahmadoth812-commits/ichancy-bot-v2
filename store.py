# store.py (مُحدّث)
import mysql.connector
from datetime import datetime
import logging
import config
import asyncio

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
# دالة تنفيذ عامة للاستعلامات (blocking)
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
# Async wrapper (run blocking DB in threadpool)
# ==========================
async def async_execute_query(sql, params=None, fetch=False, fetchone=False):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _execute_query(sql, params, fetch, fetchone))

# ==========================
# دوال المستخدمين (blocking)
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
# Async helpers for balance ops
# ==========================
async def async_add_balance(user_id, amount):
    return await async_execute_query("UPDATE users SET balance = balance + %s WHERE id = %s", (amount, user_id))

async def async_deduct_balance(user_id, amount):
    return await async_execute_query("UPDATE users SET balance = balance - %s WHERE id = %s", (amount, user_id))

# ==========================
# دوال المعاملات
# ==========================
valid_tables = [
    "syriatel_transactions", "shamcash_transactions",
    "coinex_transactions", "coinex_withdrawals",
    "shamcash_withdrawals", "syriatel_withdrawals"
]

def get_transaction(table_name, tx_id):
    if table_name not in valid_tables:
        logger.error(f"Invalid table name: {table_name}")
        return None
    return _execute_query(f"SELECT * FROM {table_name} WHERE id = %s", (tx_id,), fetchone=True)

async def async_get_transaction(table_name, tx_id):
    if table_name not in valid_tables:
        logger.error(f"Invalid table name: {table_name}")
        return None
    return await async_execute_query(f"SELECT * FROM {table_name} WHERE id = %s", (tx_id,), fetchone=True)

def update_transaction_status(
    table_name,
    tx_id,
    status,
    reason=None,
    txid_external=None,
    approved_at=None,
    rejected_at=None
):
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

async def async_update_transaction_status(*args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: update_transaction_status(*args, **kwargs))

def add_audit_log(source, tx_id, action, actor="system", reason=None):
    _execute_query(
        "INSERT INTO audit_log (source, tx_id, action, actor, reason, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
        (source, tx_id, action, actor, reason, datetime.now())
    )

# ==========================
# إعدادات (settings) في DB
# ==========================
def set_setting(key_name, value):
    # Ensure settings table has key_name as PRIMARY KEY or UNIQUE
    return _execute_query(
        "INSERT INTO settings (key_name, value, updated_at) VALUES (%s, %s, NOW()) "
        "ON DUPLICATE KEY UPDATE value = VALUES(value), updated_at = NOW()",
        (key_name, str(value))
    )

def get_setting(key_name):
    res = _execute_query("SELECT value FROM settings WHERE key_name = %s", (key_name,), fetchone=True)
    return res["value"] if res and "value" in res else None

# Rates and settings
def get_usd_to_nsp_rate():
    result = get_setting("usd_to_nsp_rate")
    try:
        return int(result) if result is not None else 5000
    except Exception:
        return 5000

async def async_get_usd_to_nsp_rate():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_usd_to_nsp_rate)

def update_usd_to_nsp_rate(new_rate):
    set_setting("usd_to_nsp_rate", str(new_rate))
    add_audit_log("settings", 0, "update_rate", actor="admin", reason=f"New rate set to {new_rate}")

# ShamCash wallet operations
def get_shamcash_wallet():
    return get_setting("shamcash_wallet") or "Not Configured"

async def async_get_shamcash_wallet():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_shamcash_wallet)

def update_shamcash_wallet(new_wallet):
    set_setting("shamcash_wallet", new_wallet)
    add_audit_log("settings", 0, "update_shamcash_wallet", actor="admin", reason=new_wallet)

# Syriatel numbers operations
def get_syriatel_numbers():
    val = get_setting("syriatel_numbers")
    if val:
        return [n.strip() for n in val.split(",") if n.strip()]
    return []

async def async_get_syriatel_numbers():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_syriatel_numbers)

def update_syriatel_numbers(numbers_list):
    set_setting("syriatel_numbers", ",".join(numbers_list))
    add_audit_log("settings", 0, "update_syriatel_numbers", actor="admin", reason=",".join(numbers_list))

# ==========================
# Helpers
# ==========================
def is_coinex_address_whitelisted(address):
    """Check if a CoinEx withdrawal address is whitelisted."""
    # Implement a real DB check here later
    logger.info(f"Checking if address {address} is whitelisted (demo returns True)")
    return True

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
