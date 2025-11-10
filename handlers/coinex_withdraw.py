# handlers/coinex_withdraw.py
import aiohttp
import hashlib
import hmac
import time
import sqlite3
from aiogram import types, Router
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import COINEX_API_KEY, COINEX_SECRET_KEY
from utils.fernet_utils import fernet_decrypt
from database.store import get_user_balance, update_user_balance

router = Router()

API_KEY = fernet_decrypt(COINEX_API_KEY)
SECRET_KEY = fernet_decrypt(COINEX_SECRET_KEY)
COINEX_BASE_URL = "https://api.coinex.com/v2"
SUPPORTED_CHAINS = ["BEP20", "TRC20"]

DB_PATH = "database/ichancy.db"
WITHDRAW_MIN = 10.0  # الحد الأدنى للسحب بالدولار
BOT_FEE_PERCENT = 10  # نسبة العمولة لصالح البوت


# ======================== دوال مساعدة عامة ========================
def generate_signature(payload: dict, secret_key: str) -> str:
    """إنشاء توقيع HMAC SHA256 بناءً على توثيق CoinEx v2"""
    sorted_params = sorted(payload.items())
    query = "&".join(f"{k}={v}" for k, v in sorted_params)
    sign = hmac.new(secret_key.encode(), query.encode(), hashlib.sha256).hexdigest().upper()
    return sign


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


async def record_withdraw_request(user_id, amount, net_amount, chain, address, txid=None, status="pending", reason=None):
    conn = get_db_connection()
    conn.execute("""
        INSERT INTO coinex_withdrawals (user_id, amount_usdt, net_amount_usdt, chain, address, txid, status, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, amount, net_amount, chain, address, txid, status, reason, int(time.time())))
    conn.commit()
    conn.close()


async def freeze_user_balance(user_id: int, amount: float):
    """تجميد الرصيد مؤقتاً أثناء انتظار موافقة الأدمن"""
    conn = get_db_connection()
    conn.execute("UPDATE users SET frozen_balance = frozen_balance + ?, balance = balance - ? WHERE user_id=?",
                 (amount, amount, user_id))
    conn.commit()
    conn.close()


async def unfreeze_user_balance(user_id: int, amount: float):
    """إلغاء التجميد في حال الرفض أو الفشل"""
    conn = get_db_connection()
    conn.execute("UPDATE users SET frozen_balance = frozen_balance - ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()


async def approve_withdraw_request(request_id: int, txid: str):
    """تأكيد عملية السحب من قبل الأدمن"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM coinex_withdrawals WHERE id=?", (request_id,))
    req = cur.fetchone()
    if not req:
        conn.close()
        return False

    conn.execute("""
        UPDATE coinex_withdrawals
        SET status=?, txid=?, reason=NULL
        WHERE id=?
    """, ("approved", txid, request_id))
    conn.commit()

    await unfreeze_user_balance(req["user_id"], req["net_amount_usdt"])
    conn.close()
    return True


async def reject_withdraw_request(request_id: int, reason: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM coinex_withdrawals WHERE id=?", (request_id,))
    req = cur.fetchone()
    if not req:
        conn.close()
        return False

    conn.execute("""
        UPDATE coinex_withdrawals
        SET status=?, reason=?
        WHERE id=?
    """, ("rejected", reason, request_id))
    conn.commit()

    # إعادة المبلغ للمستخدم
    conn.execute("UPDATE users SET balance = balance + ?, frozen_balance = frozen_balance - ? WHERE user_id=?",
                 (req["amount_usdt"], req["amount_usdt"], req["user_id"]))
    conn.commit()
    conn.close()
    return True


# ======================== whitelist التحقق من ========================
def is_address_whitelisted(user_id: int, address: str, chain: str) -> bool:
    """يتحقق إن كان العنوان مسجل مسبقاً في whitelist"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT 1 FROM user_whitelist_addresses
        WHERE user_id = ? AND address = ? AND chain = ?
    """, (user_id, address, chain))
    result = cur.fetchone()
    conn.close()
    return result is not None


# ======================== واجهة المستخدم ========================
@router.message(Command("withdraw_coinex"))
async def start_coinex_withdraw(message: types.Message):
    builder = InlineKeyboardBuilder()
    for chain in SUPPORTED_CHAINS:
        builder.button(text=f"🔻 سحب USDT ({chain})", callback_data=f"coinex_withdraw_{chain}")
    builder.adjust(1)
    await message.answer("💵 يرجى اختيار السلسلة التي ترغب السحب عليها:", reply_markup=builder.as_markup())


@router.callback_query(lambda c: c.data.startswith("coinex_withdraw_"))
async def handle_withdraw_chain(call: types.CallbackQuery):
    user_id = call.from_user.id
    chain = call.data.split("_")[-1]
    await call.message.answer(f"📤 أدخل المبلغ المراد سحبه (الحد الأدنى {WITHDRAW_MIN}$):")
    await call.answer()
    call.message.bot.session = {"chain": chain}


@router.message(lambda m: m.text.replace('.', '', 1).isdigit())
async def handle_withdraw_amount(message: types.Message):
    user_id = message.from_user.id
    amount = float(message.text)
    balance = await get_user_balance(user_id)

    if amount < WITHDRAW_MIN:
        await message.answer(f"❌ الحد الأدنى للسحب هو {WITHDRAW_MIN}$")
        return
    if balance < amount:
        await message.answer("❌ رصيدك غير كافٍ لإتمام عملية السحب.")
        return

    fee = amount * BOT_FEE_PERCENT / 100
    net_amount = amount - fee

    await message.answer(
        f"💰 سيتم خصم عمولة {BOT_FEE_PERCENT}% = {fee}$\n"
        f"المبلغ الصافي الذي سيُرسل إليك: {net_amount}$\n\n"
        "📩 أرسل الآن عنوان محفظتك الذي تريد استلام المبلغ عليه:"
    )

    message.bot.session["amount"] = amount
    message.bot.session["net_amount"] = net_amount


@router.message(lambda m: m.text.startswith("0x") or m.text.startswith("T"))
async def handle_withdraw_address(message: types.Message):
    user_id = message.from_user.id
    address = message.text
    chain = message.bot.session.get("chain")
    amount = message.bot.session.get("amount")
    net_amount = message.bot.session.get("net_amount")

    # ✅ تحقق من whitelist
    if not is_address_whitelisted(user_id, address, chain):
        await message.answer(
            "⚠️ هذا العنوان غير مسجل في القائمة البيضاء (whitelist).\n"
            "يرجى طلب إضافته عبر الأدمن قبل تنفيذ السحب."
        )
        return

    await message.answer("⏳ جاري التحقق من البيانات وإرسال الطلب...")

    await freeze_user_balance(user_id, amount)
    await record_withdraw_request(user_id, amount, net_amount, chain, address)

    await message.answer(
        "✅ تم تسجيل طلب السحب بنجاح.\n"
        "⏱️ بانتظار موافقة الأدمن لإتمام العملية.\n\n"
        f"🔗 السلسلة: {chain}\n💵 المبلغ الصافي: {net_amount}$\n📤 المحفظة: <code>{address}</code>",
        parse_mode="HTML"
    )


# ======================== تنفيذ السحب بعد الموافقة ========================
async def execute_withdraw(address: str, chain: str, amount: float):
    """تنفيذ عملية السحب الفعلية بعد موافقة الأدمن"""
    url = f"{COINEX_BASE_URL}/account/withdraw"
    payload = {
        "access_id": API_KEY,
        "tonce": int(time.time() * 1000),
        "coin_type": "USDT",
        "smart_contract_name": chain,
        "coin_address": address,
        "actual_amount": str(amount),
    }
    payload["signature"] = generate_signature(payload, SECRET_KEY)

    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=payload) as resp:
            result = await resp.json()
            return result
