# handlers/coinex_deposit.py
import logging
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
)
import store
import config
from services.coinex_adapter import get_deposit_address, get_deposit_history
from utils.notifications import notify_admin

logger = logging.getLogger(__name__)

# Conversation states
SELECT_CHAIN, CONFIRM_TRANSFER = range(2)
SUPPORTED_CHAINS = ["BEP20", "TRC20"]

async def start_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 1: Ask user for chain"""
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 BEP20", callback_data="coinex_chain_BEP20")],
        [InlineKeyboardButton("🔵 TRC20", callback_data="coinex_chain_TRC20")],
        [InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]
    ])
    await q.edit_message_text("🌐 اختر نوع السلسلة للإيداع:", reply_markup=kb)
    return SELECT_CHAIN

async def get_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetch deposit address from CoinEx API"""
    q = update.callback_query
    await q.answer()
    chain = q.data.split("_")[-1]
    if chain not in SUPPORTED_CHAINS:
        await q.edit_message_text("❌ سلسلة غير مدعومة حالياً.")
        return ConversationHandler.END

    context.user_data["chain"] = chain

    try:
        addr_info = await get_deposit_address(coin="USDT", chain=chain)
        addr = None
        if isinstance(addr_info, dict):
            # API shape may differ; try to find address in common places
            addr = addr_info.get("data", {}).get("address") if addr_info.get("data") else None
            if not addr:
                # fallback keys
                addr = addr_info.get("address") or (addr_info.get("data") and addr_info["data"][0].get("address") if isinstance(addr_info["data"], list) else None)
        if not addr:
            raise ValueError(f"No address returned from CoinEx: {addr_info}")
    except Exception as e:
        logger.error(f"CoinEx Address Error: {e}")
        await q.edit_message_text("⚠️ تعذر جلب عنوان الإيداع، حاول لاحقاً.")
        return ConversationHandler.END

    context.user_data["deposit_address"] = addr

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تم الإرسال", callback_data="coinex_sent")],
        [InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]
    ])
    await q.edit_message_text(
        f"💵 أرسل المبلغ إلى العنوان التالي على شبكة {chain}:\n\n`{addr}`\n\n"
        "ثم اضغط على الزر أدناه لإبلاغ البوت بالتحويل.",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    return CONFIRM_TRANSFER

async def confirm_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check for new deposit, convert to NSP, update balance, log, and notify"""
    q = update.callback_query
    await q.answer("جاري التحقق من الإيداع...")

    user_telegram_id = str(q.from_user.id)
    user = store.get_user_by_telegram_id(user_telegram_id)
    if not user:
        await q.edit_message_text("⚠️ حسابك غير مسجل. استخدم /start أولاً.")
        return ConversationHandler.END

    chain = context.user_data.get("chain")
    deposit_address = context.user_data.get("deposit_address")

    try:
        deposits_response = await get_deposit_history("USDT", chain=chain, limit=50)
        if not isinstance(deposits_response, dict):
            logger.warning(f"Unexpected deposit history response type: {deposits_response}")
            await q.edit_message_text("⚠️ تعذر جلب سجل الإيداعات، حاول لاحقاً.")
            return ConversationHandler.END

        if deposits_response.get("code") not in (0, None) and not deposits_response.get("data"):
            # Some APIs return code==0 on success. If not present, still try to read data.
            logger.warning(f"CoinEx deposit history API warning: {deposits_response}")
        deposits = deposits_response.get("data") or []
        found_deposit = None

        # البحث عن أول عملية جديدة تخص المستخدم وعنوانه
        for dep in deposits:
            txid = dep.get("tx_id") or dep.get("txid") or dep.get("id")
            amount = float(dep.get("amount", 0))
            status = dep.get("status") or dep.get("state")
            to_addr = dep.get("to_address") or dep.get("address") or dep.get("to")

            # تحقق من عدم تسجيل المعاملة مسبقًا
            if not txid:
                continue

            existing_tx = store._execute_query(
                "SELECT id FROM coinex_transactions WHERE txid = %s", (txid,), fetchone=True
            )
            if existing_tx:
                continue

            # تحقق أن الإيداع مكتمل ووجهته هي عنوان المستخدم
            if (str(status).upper() in ("FINISHED", "COMPLETED", "SUCCESS")) and amount > 0 and to_addr == deposit_address:
                found_deposit = dep
                break

        if not found_deposit:
            await q.edit_message_text("⌛ لا توجد إيداعات مكتملة جديدة مرتبطة بعنوانك بعد.")
            return ConversationHandler.END

        txid = found_deposit.get("tx_id") or found_deposit.get("txid") or found_deposit.get("id")
        amount = float(found_deposit.get("amount", 0))
    except Exception as e:
        logger.error(f"CoinEx Confirm Error: {e}")
        await q.edit_message_text("❌ حدث خطأ أثناء التحقق من العملية.")
        return ConversationHandler.END

    # تحويل USDT → NSP
    rate = store.get_usd_to_nsp_rate()
    if not rate or rate <= 0:
        await q.edit_message_text("⚠️ سعر التحويل غير متوفر حالياً.")
        return ConversationHandler.END
    nsp_value = int(amount * rate)

    # حفظ المعاملة في DB
    tx_db_id = store._execute_query(
        """
        INSERT INTO coinex_transactions (user_id, chain, usdt_amount, nsp_value, txid, status, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        """,
        (user["id"], chain, amount, nsp_value, txid, "approved", datetime.now()),
        fetchone=False
    )

    if tx_db_id:
        store.add_balance(user["id"], nsp_value)
        store.add_audit_log(
            "coinex_deposit",
            tx_db_id,
            "approved",
            actor=f"user_{user_telegram_id}",
            reason=f"Auto deposit {amount} USDT → {nsp_value} NSP"
        )

        await q.edit_message_text(
            f"✅ تم تأكيد الإيداع بنجاح!\n"
            f"💰 المبلغ: {amount} USDT ({nsp_value} NSP)\n"
            f"🔗 السلسلة: {chain}\n"
            f"🆔 TxID: `{txid}`",
            parse_mode="Markdown"
        )

        # إشعار الأدمن
        admin_msg = (
            f"💹 تم تسجيل إيداع CoinEx تلقائياً:\n"
            f"👤 المستخدم: @{q.from_user.username or q.from_user.full_name}\n"
            f"💰 {amount} USDT ({nsp_value} NSP)\n"
            f"🔗 {chain}\n🆔 TxID: {txid}"
        )
        await notify_admin(admin_msg)
    else:
        await q.edit_message_text("❌ حدث خطأ أثناء تسجيل الإيداع في قاعدة البيانات.")

    context.user_data.clear()
    return ConversationHandler.END

# Cancellation handler
async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❎ تم إلغاء العملية.")
    elif update.message:
        await update.message.reply_text("❎ تم إلغاء العملية.")
    context.user_data.clear()
    return ConversationHandler.END

def register_handlers(dp):
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_deposit, pattern="^coinex_deposit$")],
        states={
            SELECT_CHAIN: [CallbackQueryHandler(get_address, pattern="^coinex_chain_")],
            CONFIRM_TRANSFER: [CallbackQueryHandler(confirm_transfer, pattern="^coinex_sent$")],
        },
        fallbacks=[CallbackQueryHandler(cancel_action, pattern="^cancel_action$")],
    )
    dp.add_handler(conv)
