import logging
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
import config
from services.coinex_adapter import get_deposit_address, get_deposit_history
from utils.notifications import notify_admin # For admin notifications

logger = logging.getLogger(__name__)

# Conversation states
SELECT_CHAIN, CONFIRM_TRANSFER = range(2)

SUPPORTED_CHAINS = ["BEP20", "TRC20"]

async def start_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الخطوة الأولى — يختار المستخدم نوع السلسلة"""
    q = update.callback_query
    await q.answer()
    
    text = "🌐 اختر نوع السلسلة التي ترغب بالإيداع من خلالها:"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 BEP20", callback_data="coinex_chain_BEP20")],
        [InlineKeyboardButton("🔵 TRC20", callback_data="coinex_chain_TRC20")],
        [InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]
    ])
    await q.edit_message_text(text, reply_markup=kb) # Use edit_message_text instead of send_message
    return SELECT_CHAIN


async def get_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """جلب عنوان الإيداع من CoinEx API"""
    q = update.callback_query
    await q.answer()
    chain = q.data.split("_")[-1]
    if chain not in SUPPORTED_CHAINS:
        await q.edit_message_text("❌ سلسلة غير مدعومة حالياً.")
        return ConversationHandler.END # End conversation for unsupported chain

    context.user_data["chain"] = chain

    try:
        addr_info = await get_deposit_address(coin="USDT", chain=chain)
        # Assuming addr_info structure like {'code': 0, 'data': {'address': '...'}}
        addr = addr_info.get("data", {}).get("address")
        if not addr:
            raise ValueError(f"لا يوجد عنوان متاح حالياً. استجابة CoinEx: {addr_info}")
    except Exception as e:
        logger.error(f"CoinEx Address Error: {e}")
        await q.edit_message_text("⚠️ تعذر جلب عنوان الإيداع حالياً، حاول لاحقاً.")
        return ConversationHandler.END

    text = (
        f"💵 قم بإرسال المبلغ الذي ترغب بإيداعه إلى العنوان التالي على شبكة {chain}:\n\n"
        f"`{addr}`\n\n"
        "بعد الإرسال، اضغط على الزر أدناه لإعلام البوت بالتحويل."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تم الإرسال", callback_data="coinex_sent")],
        [InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_action")]
    ])
    await q.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    return CONFIRM_TRANSFER


async def confirm_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التحقق من وجود معاملة إيداع جديدة في CoinEx"""
    q = update.callback_query
    await q.answer("جاري التحقق من الإيداع...")

    user_telegram_id = str(q.from_user.id)
    user = store.get_user_by_telegram_id(user_telegram_id)
    if not user:
        await q.edit_message_text("⚠️ حسابك غير مسجل. استخدم /start أولاً.")
        return ConversationHandler.END

    chain = context.user_data.get("chain", "BEP20")

    try:
        # Fetch deposit history for USDT on the specified chain
        deposits_response = await get_deposit_history("USDT", chain=chain, limit=5)
        if deposits_response.get("code") != 0 or not deposits_response.get("data"):
            logger.warning(f"CoinEx deposit history API error: {deposits_response}")
            await q.edit_message_text("⚠️ تعذر جلب سجل الإيداعات حالياً، حاول لاحقاً.")
            return ConversationHandler.END
            
        deposits = deposits_response["data"] # Assuming 'data' is a list of deposits

        found_deposit = None
        # This logic needs to be robust. Simply taking the latest might not be enough.
        # Ideally, you'd match by deposit address or a specific identifier.
        # For this example, we'll try to find a recent FINISHED deposit not already recorded.
        for dep in deposits:
            txid = dep.get("tx_id")
            amount = float(dep.get("amount", 0))
            status = dep.get("status")

            # Check if this transaction already exists in our DB to prevent double processing
            existing_tx = store._execute_query("SELECT id FROM coinex_transactions WHERE txid = %s", (txid,), fetchone=True)
            if existing_tx:
                continue # Skip already processed transactions

            if status == "FINISHED" and amount > 0:
                # Add more robust checks if possible (e.g., if CoinEx provides a user-specific deposit address or tag)
                found_deposit = dep
                break
        
        if not found_deposit:
            await q.edit_message_text("⌛ لا توجد عمليات إيداع مكتملة جديدة مرتبطة بحسابك بعد. حاول بعد قليل.")
            return ConversationHandler.END

        txid = found_deposit.get("tx_id")
        amount = float(found_deposit.get("amount", 0))
        
    except Exception as e:
        logger.error(f"CoinEx Confirm Error: {e}")
        await q.edit_message_text("❌ حدث خطأ أثناء التحقق من العملية.")
        return ConversationHandler.END

    # تحويل المبلغ من USDT → NSP بسعر الأدمن
    rate = store.get_usd_to_nsp_rate()
    if not rate or rate <= 0:
        await q.edit_message_text("⚠️ سعر التحويل غير متوفر حالياً. يرجى المحاولة لاحقاً.")
        return ConversationHandler.END
        
    nsp_value = int(amount * rate)

    # حفظ المعاملة في قاعدة البيانات
    tx_id = store._execute_query("""
        INSERT INTO coinex_transactions (user_id, chain, usdt_amount, nsp_value, txid, status, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (user["id"], chain, amount, nsp_value, txid, "approved", datetime.now()), fetchone=False) # returns lastrowid

    if tx_id:
        # تحديث رصيد المستخدم
        store.add_balance(user["id"], nsp_value)
        store.add_audit_log("coinex_deposit", tx_id, "approved", actor=f"user_{user_telegram_id}", reason=f"Auto deposit {amount} USDT → {nsp_value} NSP")

        await q.edit_message_text(
            f"✅ تم تأكيد الإيداع بنجاح!\n"
            f"💰 المبلغ: {amount} USDT ({nsp_value} NSP)\n"
            f"🔗 السلسلة: {chain}\n"
            f"🆔 TxID: `{txid}`",
            parse_mode="Markdown"
        )

        # إشعار الأدمن
        msg = (
            f"💹 تم تسجيل إيداع CoinEx تلقائياً:\n"
            f"👤 المستخدم: @{q.from_user.username or q.from_user.full_name}\n"
            f"💰 {amount} USDT ({nsp_value} NSP)\n"
            f"🔗 {chain}\n🆔 TxID: {txid}"
        )
        await notify_admin(msg)

    else:
        await q.edit_message_text("❌ حدث خطأ في تسجيل الإيداع بقاعدة البيانات.")

    context.user_data.clear() # Clear user data for this conversation
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
            CONFIRM_TRANSFER: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_transfer), # Allow text for manual txid entry if needed, or specific button
                               CallbackQueryHandler(confirm_transfer, pattern="^coinex_sent$")],
        },
        fallbacks=[CallbackQueryHandler(cancel_action, pattern="^cancel_action$"),
                   CommandHandler("cancel", cancel_action)],
    )
    dp.add_handler(conv)
