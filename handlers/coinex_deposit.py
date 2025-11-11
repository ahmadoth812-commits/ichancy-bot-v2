# handlers/coinex_deposit.py
import os
import hmac
import json
import time
import hashlib
import logging
import aiohttp
from datetime import datetime
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
import store
from fernet_utils import decrypt_secret

logger = logging.getLogger(__name__)

# Conversation states
SELECT_CHAIN, CONFIRM_TRANSFER = range(2)

# Load decrypted API keys
API_KEY = decrypt_secret(os.getenv("COINEX_API_KEY"))
API_SECRET = decrypt_secret(os.getenv("COINEX_API_SECRET"))

SUPPORTED_CHAINS = ["BEP20", "TRC20"]

# Helper function to create CoinEx signature
def sign_request(params: dict, secret: str) -> str:
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()

async def start_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask the user which chain they want to use (BEP20 or TRC20)."""
    await update.callback_query.answer()
    text = "🌐 اختر نوع السلسلة التي ترغب بالإيداع من خلالها:"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 BEP20", callback_data="coinex_chain_BEP20")],
        [InlineKeyboardButton("🔵 TRC20", callback_data="coinex_chain_TRC20")]
    ])
    await update.effective_chat.send_message(text, reply_markup=kb)
    return SELECT_CHAIN


async def get_deposit_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetch deposit address for the chosen chain from CoinEx."""
    query = update.callback_query
    await query.answer()

    chain = query.data.split("_")[-1]
    if chain not in SUPPORTED_CHAINS:
        return await query.edit_message_text("❌ سلسلة غير مدعومة.")
    context.user_data["chain"] = chain

    # Prepare API request
    url = "https://api.coinex.com/v2/sub_account/deposit_address"
    params = {
        "access_id": API_KEY,
        "timestamp": int(time.time() * 1000),
        "coin_type": "USDT",
        "smart_contract_name": chain,
    }
    sign = sign_request(params, API_SECRET)
    headers = {"Authorization": sign}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=params, headers=headers) as resp:
            data = await resp.json()
            if data.get("code") != 0:
                return await query.edit_message_text("⚠️ تعذر جلب عنوان الإيداع. حاول لاحقًا.")
            addr = data["data"]["address"]

    text = (
        f"💵 قم بإرسال المبلغ الذي ترغب بإيداعه إلى العنوان التالي على شبكة {chain}:\n\n"
        f"`{addr}`\n\n"
        "بعد الإرسال، اضغط على الزر أدناه لإعلام البوت بالتحويل."
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ تم الإرسال", callback_data="coinex_sent")]])
    await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    return CONFIRM_TRANSFER


async def confirm_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check CoinEx deposit history for new transaction matching user."""
    q = update.callback_query
    await q.answer()
    user = store.getUserByTelegramId(str(q.from_user.id))
    if not user:
        await q.edit_message_text("⚠️ حسابك غير مسجل. استخدم /start أولاً.")
        return ConversationHandler.END

    chain = context.user_data.get("chain", "BEP20")

    # Fetch deposits from CoinEx
    url = "https://api.coinex.com/v2/deposit_history"
    params = {
        "access_id": API_KEY,
        "timestamp": int(time.time() * 1000),
        "coin_type": "USDT",
    }
    sign = sign_request(params, API_SECRET)
    headers = {"Authorization": sign}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, headers=headers) as resp:
            data = await resp.json()

    deposits = data.get("data", {}).get("data", [])
    if not deposits:
        await q.edit_message_text("⌛ لم يتم العثور على أي عملية إيداع جديدة بعد، يرجى الانتظار قليلاً.")
        return ConversationHandler.END

    # Simulate matching logic by latest transaction (can be refined)
    latest_tx = deposits[0]
    txid = latest_tx["tx_id"]
    amount = float(latest_tx["amount"])
    status = latest_tx["status"]
    if status != "FINISHED":
        await q.edit_message_text("⚠️ العملية لم تكتمل بعد، حاول بعد قليل.")
        return ConversationHandler.END

    # Convert USD → NSP
    rate = store.get_usd_to_nsp_rate()
    nsp_value = int(amount * rate)

    # Store transaction
    db = store.getDatabaseConnection()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO coinex_transactions (user_id, chain, usdt_amount, nsp_value, txid, status, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (user["id"], chain, amount, nsp_value, txid, "approved", datetime.now()))
    tx_id = cur.lastrowid
    db.commit()
    db.close()

    # Add to balance
    store.add_balance(user["id"], nsp_value)
    store.add_audit_log("coinex", tx_id, "approved", f"Auto deposit confirmed from {chain}")

    await q.edit_message_text(
        f"✅ تم تأكيد الإيداع بنجاح!\n"
        f"💰 المبلغ: {amount} USDT ({nsp_value} NSP)\n"
        f"🔗 السلسلة: {chain}\n"
        f"🆔 TxID: `{txid}`",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


def register_handlers(dp):
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_deposit, pattern="^coinex_deposit$")],
        states={
            SELECT_CHAIN: [CallbackQueryHandler(get_deposit_address, pattern="^coinex_chain_")],
            CONFIRM_TRANSFER: [CallbackQueryHandler(confirm_transfer, pattern="^coinex_sent$")],
        },
        fallbacks=[],
    )
    dp.add_handler(conv)
