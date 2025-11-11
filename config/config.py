import os
from dotenv import load_dotenv

load_dotenv() # Load environment variables from .env file

# Telegram Bot Token
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# MySQL Database Configuration
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "ichancy_bot")

# Admin IDs (Telegram User IDs)
ADMIN_IDS = [int(id) for id in os.getenv("ADMIN_IDS", "YOUR_ADMIN_ID_HERE").split(',')]

# CoinEx API Configuration
COINEX_API_KEY = os.getenv("COINEX_API_KEY", "YOUR_COINEX_API_KEY")
COINEX_API_SECRET = os.getenv("COINEX_API_SECRET", "YOUR_COINEX_API_SECRET")
COINEX_ACCESS_ID = os.getenv("COINEX_ACCESS_ID", "") # Optional

# Withdrawal/Deposit Limits and Fees
SHAMCASH_MIN_USD = int(os.getenv("SHAMCASH_MIN_USD", "5"))
SHAMCASH_MIN_NSP = int(os.getenv("SHAMCASH_MIN_NSP", "25000"))
SHAMCASH_MIN_WITHDRAW_NSP = int(os.getenv("SHAMCASH_MIN_WITHDRAW_NSP", "50000"))
SHAMCASH_COMMISSION = float(os.getenv("SHAMCASH_COMMISSION", "0.10"))

SYRIATEL_MIN_AMOUNT = int(os.getenv("SYRIATEL_MIN_AMOUNT", "25000"))
SYRIATEL_MIN_WITHDRAW = int(os.getenv("SYRIATEL_MIN_WITHDRAW", "50000"))
SYRIATEL_MAX_WITHDRAW = int(os.getenv("SYRIATEL_MAX_WITHDRAW", "500000"))
SYRIATEL_FEE_PERCENT = int(os.getenv("SYRIATEL_FEE_PERCENT", "10"))

COINEX_MIN_WITHDRAW_NSP = int(os.getenv("COINEX_MIN_WITHDRAW_NSP", "10000"))
COINEX_FEE_PERCENT = float(os.getenv("COINEX_FEE_PERCENT", "0.0"))