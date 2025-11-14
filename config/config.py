# config/config.py
import os
from typing import List, Optional

# حاول تحميل .env إن كانت python-dotenv مثبتة؛ عدم وجودها لا يعيق التشغيل في بيئات الإنتاج
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


def _parse_admin_ids(raw: str) -> List[int]:
    ids: List[int] = []
    if not raw:
        return ids
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        try:
            ids.append(int(p))
        except ValueError:
            # نتجاوز القيم غير الصالحة بدل الرمي؛ هذا يمنع كسر التطبيق بسبب placeholder غير صالح
            continue
    return ids


# TOKEN: لا تضَع قيمة افتراضية حسّاسة هنا
TELEGRAM_BOT_TOKEN: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")

# MySQL Database Configuration
DB_HOST: str = os.getenv("DB_HOST", "localhost")
DB_USER: str = os.getenv("DB_USER", "root")
DB_PASSWORD: Optional[str] = os.getenv("DB_PASSWORD")
DB_NAME: str = os.getenv("DB_NAME", "ichancy_bot")


# Admin IDs (Telegram User IDs) - الآن يتم تحليلها بأمان دون رمي استثناء عند القيمة الافتراضية
ADMIN_IDS = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))


# CoinEx API Configuration - ضع None إن لم تُعرّف المتغيّرات (لتقليل فرصة تسريب defaults)
COINEX_ACCESS_ID: Optional[str] = os.getenv("COINEX_ACCESS_ID")
COINEX_SECRET_KEY: Optional[str] = os.getenv("COINEX_SECRET_KEY")


# Helpers لتحويل أنواع القيم البيئية بشكل آمن
def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# Withdrawal/Deposit Limits and Fees
SHAMCASH_MIN_USD: int = _int_env("SHAMCASH_MIN_USD", 5)
SHAMCASH_MIN_NSP: int = _int_env("SHAMCASH_MIN_NSP", 25000)
SHAMCASH_MIN_WITHDRAW_NSP: int = _int_env("SHAMCASH_MIN_WITHDRAW_NSP", 50000)
SHAMCASH_COMMISSION: float = _float_env("SHAMCASH_COMMISSION", 0.10)

SYRIATEL_MIN_AMOUNT: int = _int_env("SYRIATEL_MIN_AMOUNT", 25000)
SYRIATEL_MIN_WITHDRAW: int = _int_env("SYRIATEL_MIN_WITHDRAW", 50000)
SYRIATEL_MAX_WITHDRAW: int = _int_env("SYRIATEL_MAX_WITHDRAW", 500000)
SYRIATEL_FEE_PERCENT: int = _int_env("SYRIATEL_FEE_PERCENT", 10)

COINEX_MIN_WITHDRAW_NSP: int = _int_env("COINEX_MIN_WITHDRAW_NSP", 10000)
COINEX_FEE_PERCENT: float = _float_env("COINEX_FEE_PERCENT", 0.0)