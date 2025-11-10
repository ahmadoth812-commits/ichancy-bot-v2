# handlers/coinex_deposit.py
import aiohttp
import hashlib
import hmac
import time
import json
import sqlite3
from aiogram import types, Router
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import COINEX_API_KEY, COINEX_SECRET_KEY
from utils.fernet_utils import fernet_decrypt
from database.store import get_user_balance, update_user_balance

router = Router()

# إعداد المفاتيح من env (مشفرة مسبقاً)
API_KEY = fernet_decrypt(COINEX_API_KEY)
SECRET_KEY = fernet_decrypt(COINEX_SECRET_KEY)

COINEX_BASE_URL = "https://api.coinex.com/v2"
SUPPORTED_CHAINS = ["BEP20", "TRC20"]

DB_PATH = "database/ichancy.db"


# === دوال مساعدة ===
def generate_signature(payload: dict, secret_key: str) -> str:
    """
    إنشاء توقيع HMAC SHA256 بناءً على وثائق CoinEx v2
    """
    sorted_params = sorted(payload.items())
    query = "&".join(f"{k}={v}" for k, v in sorted_params)
    sign = hmac.new(secret_key.encode(), query.encode(), hashlib.sha256).hexdigest().upper()
    return sign


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


async def get_cached_address(user_id: int, chain: str):
    """
    فحص إن كان لدى المستخدم عنوان محفوظ سابقًا لنفس السلسلة
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT address FROM coinex_deposit_addresses WHERE user_id=? AND chain=?",
        (user_id, chain),
    )
    row = cur.fetchone()
    conn.close()
    return row["address"] if row else None


async def cache_address(user_id: int, chain: str, address: str):
    """
    تخزين عنوان الإيداع في قاعدة البيانات
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO coinex_deposit_addresses (user_id, chain, address, created_at) VALUES (?, ?, ?, ?)",
        (user_id, chain, address, int(time.time())),
    )
    conn.commit()
    conn.close()


async def get_deposit_address(chain: str):
    """
    جلب عنوان الإيداع من CoinEx API
    """
    url = f"{COINEX_BASE_URL}/account/deposit/address"
    payload = {
        "access_id": API_KEY,
        "tonce": int(time.time() * 1000),
        "coin_type": "USDT",
        "smart_contract_name": chain,
    }
    payload["signature"] = generate_signature(payload, SECRET_KEY)

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=payload) as resp:
            data = await resp.json()
            if "data" in data and "url" in data["data"]:
                return data["data"]["url"]
            elif "data" in data and "address" in data["data"]:
                return data["data"]["address"]
            else:
                return None


# === واجهة المستخدم ===
@router.message(Command("deposit_coinex"))
async def start_coinex_deposit(message: types.Message):
    """
    عرض واجهة اختيار السلسلة للإيداع
    """
    builder = InlineKeyboardBuilder()
    for chain in SUPPORTED_CHAINS:
        builder.button(text=f"💰 إيداع USDT ({chain})", callback_data=f"coinex_deposit_{chain}")
    builder.adjust(1)
    await message.answer(
        "يرجى اختيار السلسلة التي تريد الإيداع عليها:",
        reply_markup=builder.as_markup()
    )


@router.callback_query(lambda c: c.data.startswith("coinex_deposit_"))
async def process_deposit_callback(call: types.CallbackQuery):
    """
    بعد اختيار السلسلة، يتم جلب عنوان الإيداع المناسب من CoinEx أو من الكاش
    """
    user_id = call.from_user.id
    chain = call.data.split("_")[-1]

    await call.message.edit_text("⏳ جارٍ جلب عنوان الإيداع الخاص بك، يرجى الانتظار...")

    # فحص الكاش أولاً
    address = await get_cached_address(user_id, chain)
    if not address:
        address = await get_deposit_address(chain)
        if address:
            await cache_address(user_id, chain, address)
        else:
            await call.message.answer("⚠️ لم نتمكن من جلب عنوان الإيداع. حاول لاحقاً.")
            return

    text = (
        f"✅ يمكنك الآن إرسال USDT إلى العنوان التالي:\n\n"
        f"<b>{address}</b>\n\n"
        f"السلسلة: <b>{chain}</b>\n"
        f"العملة: <b>USDT</b>\n\n"
        f"📌 بعد الإيداع، أرسل معرف المعاملة (TXID) ليتم التحقق التلقائي.\n"
        f"⏳ قد تستغرق العملية بضع دقائق حتى يتم تأكيدها على البلوكشين."
    )

    await call.message.answer(text, parse_mode="HTML")


# === التحقق من الإيداعات تلقائيًا (اختياري) ===
async def verify_deposit(txid: str, user_id: int, chain: str):
    """
    التحقق من الإيداع عبر CoinEx API بعد أن يرسل المستخدم txid.
    يمكن تشغيلها دورياً عبر scheduler.
    """
    url = f"{COINEX_BASE_URL}/account/deposit/history"
    payload = {
        "access_id": API_KEY,
        "tonce": int(time.time() * 1000),
        "coin_type": "USDT",
    }
    payload["signature"] = generate_signature(payload, SECRET_KEY)

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=payload) as resp:
            data = await resp.json()
            deposits = data.get("data", {}).get("records", [])

            for dep in deposits:
                if dep["tx_id"] == txid and dep["smart_contract_name"] == chain:
                    # تحقق ناجح
                    amount_usdt = float(dep["amount"])
                    conn = get_db_connection()
                    conn.execute(
                        "INSERT INTO deposits (user_id, txid, chain, amount_usdt, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (user_id, txid, chain, amount_usdt, "confirmed", int(time.time())),
                    )
                    conn.commit()
                    conn.close()
                    await update_user_balance(user_id, amount_usdt)
                    return True
    return False
