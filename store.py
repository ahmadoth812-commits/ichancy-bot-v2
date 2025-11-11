import mysql.connector
from datetime import datetime
import config

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
    cursor = conn.cursor(dictionary=True) # To get results as dictionaries
    try:
        cursor.execute(sql, params)
        if fetchone:
            result = cursor.fetchone()
        elif fetch:
            result = cursor.fetchall()
        else:
            result = cursor.lastrowid # For INSERT operations
        conn.commit()
        return result
    except mysql.connector.Error as err:
        print(f"Database Error: {err}")
        conn.rollback()
        return None
    finally:
        cursor.close()
        conn.close()

# Users related functions
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

# Transactions related functions
def get_transaction(table_name, tx_id):
    """Get a transaction by its ID from a specific table."""
    # Ensure table_name is safe by checking against a whitelist
    if table_name not in ["syriatel_transactions", "shamcash_transactions", 
                          "coinex_transactions", "coinex_withdrawals",
                          "shamcash_withdrawals", "syriatel_withdrawals"]:
        return None
    return _execute_query(f"SELECT * FROM {table_name} WHERE id = %s", (tx_id,), fetchone=True)

def update_transaction_status(table_name, tx_id, status, reason=None, txid_external=None, approved_at=None, rejected_at=None):
    """Update transaction status and optional details."""
    if table_name not in ["syriatel_transactions", "shamcash_transactions", 
                          "coinex_transactions", "coinex_withdrawals",
                          "shamcash_withdrawals", "syriatel_withdrawals"]:
        print(f"Error: Invalid table name {table_name} in update_transaction_status")
        return

    sql_parts = ["status = %s"]
    params = [status]

    if reason is not None:
        sql_parts.append("reason = %s")
        params.append(reason)
    if txid_external is not None:
        # Note: some tables might have txid, others tx_id. Adjust column name if needed.
        if table_name in ["syriatel_transactions", "shamcash_transactions"]:
            sql_parts.append("txid = %s")
        elif table_name in ["coinex_withdrawals"]: # For CoinEx withdrawal, external TX ID
            sql_parts.append("coinex_txid = %s") # Assuming a column like this
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

# Specific rates and data
def get_usd_to_nsp_rate():
    """Get the current USD to NSP conversion rate."""
    # Implement actual logic to fetch this rate from DB or config
    # For now, return a placeholder rate
    return 5000 # Example rate

def get_syriatel_numbers():
    """Get a list of Syriatel deposit numbers."""
    # Implement actual logic to fetch this from DB
    return ["099xxxxxxxx", "098xxxxxxxx"] # Example numbers

def is_coinex_address_whitelisted(address):
    """Check if a CoinEx withdrawal address is whitelisted."""
    # Implement actual logic to check DB table like 'coinex_whitelisted_addresses'
    # For now, return True for demonstration, but this is a security risk!
    # A proper implementation would check if the address exists in a dedicated whitelist table.
    print(f"Checking if address {address} is whitelisted (currently always True for demo)...")
    return True 

def get_user_telegram_by_tx(table_name, tx_id):
    """Get Telegram ID of user associated with a transaction."""
    if table_name not in ["shamcash_withdrawals", "syriatel_withdrawals"]: # Extend as needed
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
