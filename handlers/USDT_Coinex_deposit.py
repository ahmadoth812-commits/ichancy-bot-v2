# handlers/coinex_deposit.py
import os
import hashlib
import time
import json
import asyncio
import qrcode
from io import BytesIO
from aiogram import types, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from utils.fernet_utils import decrypt_value
from database.store import add_pending_transaction, update_transaction_status
from config.loader import bot, ADMINS
from services.audit_logger import log_action
from services.exchange_utils import convert_usdt_to_nsp
import requests

router = Router()


# ==================== STATES ====================
class CoinExDeposit(StatesGroup):
    choosing_network = State()
    waiting_amount = State()
    confirming_tx = State()


# ==================== HELPERS ====================

def generate_signature(params: dict, secret_key: str):
    """CoinEx API signing function"""
    sorted_params = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sign_str = f"{sorted_params}&secret_key={secret_key}"
    return hashlib.md5(sign_str.encode("utf-8")).hexdigest().upper()


def get_headers(api_key: str, signature: str, tonce: int):
    return {
        "X-COINEX-KEY": api_key,
        "X-COINEX-SIGN": signature,
        "X-COINEX-TONCE": str(tonce),
        "Content-Type": "application/json"
    }


async def get_deposit_address(api_key: str, secret_key: str, chain: str):
    """Fetch deposit address from CoinEx"""
    url = "https://api.coinex.com/v2/assets/deposit-address"
    params = {
        "access_id": api_key,
        "ccy": "USDT",
        "chain": chain,
        "tonce": int(time.time() * 1000)
    }
    sign = generate_signature(params, secret_key)
    headers = get_headers(api_key, sign, params["tonce"])
    response = requests.get(url, headers=headers, params=params)
    data = response.json()
    if data.get("code") == 0:
        return data["data"]["address"]
    else:
        print("⚠️ API Error:", data)
        return None


def generate_qr(address: str) -> BytesIO:
    """Generate QR image for address"""
    img = qrcode.make(address)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


async def fetch_coinex_deposits(api_key: str, secret_key: str):
    """Background: fetch deposit history"""
    url = "https://api.coinex.com/v2/assets/deposit-history"
    params = {
        "access_id": api_key,
        "ccy": "USDT",
        "limit": 20,
        "tonce": int(time.time() * 1000)
    }
    sign = generate_signature(params, secret_key)
    headers = get_headers(api_key, sign, params["tonce"])
    response = requests.get(url, headers=headers, params=params)
    data = response.json()
    return data.get("data", [])


async def monitor_pending_deposits():
    """Background task: confirm pending deposits"""
    api_key = decrypt_value(os.getenv("COINEX_API_KEY"))
    secret_key = decrypt_value(os.getenv("COINEX_SECRET_KEY"))
    deposits = await asyncio.to_thread(fetch_coinex_deposits, api_key, secret_key)
    for dep in deposits:
        if dep.get("status") == "SUCCESS":
            txid = dep.get("tx_id")
            amount = float(dep.get("amount", 0))
            # convert USD → NSP
            nsp_amount = await convert_usdt_to_nsp(amount)
            await update_transaction_status(txid=txid, status="confirmed", amount_nsp=nsp_amount)
            await log_action("system", f"Auto-confirmed CoinEx deposit TX={txid} ({nsp_amount} NSP)")


# ==================== HANDLERS ====================

@router.message(Command("coinex_deposit"))
async def start_coinex_deposit(message: types.Message, state: FSMContext):
    """Start CoinEx deposit process"""
    await message.answer(
        "💰 اختر السلسلة التي تريد الإيداع عبرها:",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="BEP20")],
                [types.KeyboardButton(text="TRC20")],
                [types.KeyboardButton(text="❌ إلغاء")]
            ],
            resize_keyboard=True
        )
    )
    await state.set_state(CoinExDeposit.choosing_network)


@router.message(CoinExDeposit.choosing_network)
async def choose_network(message: types.Message, state: FSMContext):
    """Handle user's chain choice"""
    chain = message.text.strip().upper()
    if chain not in ["BEP20", "TRC20"]:
        await message.answer("⚠️ الرجاء اختيار شبكة صحيحة: BEP20 أو TRC20")
        return

    await state.update_data(chain=chain)

    api_key = decrypt_value(os.getenv("COINEX_API_KEY"))
    secret_key = decrypt_value(os.getenv("COINEX_SECRET_KEY"))

    await message.answer("⏳ جاري جلب عنوان الإيداع من CoinEx ...")

    address = await asyncio.to_thread(get_deposit_address, api_key, secret_key, chain)
    if not address:
        await message.answer("❌ تعذر الحصول على العنوان. حاول لاحقًا أو راجع الدعم.")
        return

    # Generate QR code
    qr_buf = generate_qr(address)
    caption = (
        f"✅ عنوان الإيداع على شبكة {chain}:\n"
        f"`{address}`\n\n"
        "يرجى إرسال المبلغ المطلوب إلى هذا العنوان.\n"
        "بعد الإرسال، اضغط زر (تم التحويل)."
    )
    await bot.send_photo(
        chat_id=message.chat.id,
        photo=types.BufferedInputFile(qr_buf.getvalue(), filename="coinex_qr.png"),
        caption=caption,
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="تم التحويل")],
                [types.KeyboardButton(text="❌ إلغاء")]
            ],
            resize_keyboard=True
        )
    )
    await state.set_state(CoinExDeposit.waiting_amount)


@router.message(CoinExDeposit.waiting_amount)
async def handle_amount_entry(message: types.Message, state: FSMContext):
    """Handle user confirming transfer"""
    if message.text.strip() == "❌ إلغاء":
        await state.clear()
        await message.answer("🚫 تم إلغاء العملية.")
        return

    await message.answer("💵 يرجى إدخال قيمة المبلغ الذي قمت بتحويله بالدولار (USDT):")
    await state.update_data(waiting_for_amount=True)


@router.message(lambda msg, st: st.get("waiting_for_amount", False))
async def save_amount(message: types.Message, state: FSMContext):
    """Save the transfer amount"""
    try:
        amount = float(message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ الرجاء إدخال رقم صالح.")
        return

    data = await state.get_data()
    chain = data["chain"]

    # Convert to NSP
    nsp_amount = await convert_usdt_to_nsp(amount)

    txid = f"pending_{int(time.time())}_{message.from_user.id}"
    await add_pending_transaction(
        user_id=message.from_user.id,
        method="CoinEx",
        currency="USDT",
        chain=chain,
        amount=amount,
        txid=txid,
        status="pending"
    )

    await message.answer(
        f"✅ تم تسجيل طلب الإيداع بنجاح.\n"
        f"💵 المبلغ: {amount} USDT ≈ {nsp_amount:.2f} NSP\n"
        "⏳ نرجو الانتظار حتى يتم تأكيد المعاملة على البلوكشين."
    )

    for admin_id in ADMINS:
        await bot.send_message(
            admin_id,
            f"📥 طلب إيداع جديد عبر CoinEx\n"
            f"👤 المستخدم: {message.from_user.full_name} ({message.from_user.id})\n"
            f"🌐 الشبكة: {chain}\n"
            f"💵 المبلغ: {amount} USDT ≈ {nsp_amount:.2f} NSP\n"
            f"TXID: {txid}\n"
            f"⏳ الحالة: Pending"
        )

    await log_action(message.from_user.id, f"Created CoinEx deposit {amount} USDT ({nsp_amount} NSP) pending.")
    await state.clear()
