# handlers/coinex_deposit.py
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
)
import store
import config
from services.coinex_adapter import (
    get_deposit_address,
    get_deposit_history
)

logger = logging.getLogger(__name__)

# Conversation states
SELECT_CHAIN, CONFIRM_TRANSFER = range(2)

SUPPORTED_CHAINS = ["BEP20", "TRC20"]
ADMIN_IDS = getattr(config, "ADMIN_IDS", [])

async def start_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الخطوة الأولى — يختار المستخدم نوع السلسلة"""
    await update.callback_query.answer()
    text = "🌐 اختر نوع السلسلة التي ترغب بالإيداع من خلالها:"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 BEP20", callback_data="coinex_chain_BEP20")],
        [InlineKeyboardButton("🔵 TRC20", callback_data="coinex_chain_TRC20")],
    ])
    await update.effective_chat.send_message(text, reply_markup=kb)
    return SELECT_CHAIN


async def get_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """جلب عنوان الإيداع من CoinEx API"""
    q = update.callback_query
    await q.answer()
    chain = q.data.split("_")[-1]
    if chain not in SUPPORTED_CHAINS:
        return await q.edit_message_text("❌ سلسلة غير مدعومة حالياً.")

    context.user_data["chain"] = chain

    try:
        addr_info = await get_deposit_address(coin="USDT", chain=chain)
        addr = addr_info.get("address")
        if not addr:
            raise ValueError("لا يوجد عنوان متاح حالياً.")
    except Exception as e:
        logger.error(f"CoinEx Address Error: {e}")
        return await q.edit_message_text("⚠️ تعذر جلب عنوان الإيداع حالياً، حاول لاحقاً.")

    text = (
        f"💵 قم بإرسال المبلغ الذي ترغب بإيداعه إلى العنوان التالي على شبكة {chain}:\n\n"
        f"`{addr}`\n\n"
        "بعد الإرسال، اضغط على الزر أدناه لإعلام البوت بالتحويل."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تم الإرسال", callback_data="coinex_sent")]
    ])
    await q.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    return CONFIRM_TRANSFER


async def confirm_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التحقق من وجود معاملة إيداع جديدة في CoinEx"""
    q = update.callback_query
    await q.answer()

    user = store.getUserByTelegramId(str(q.from_user.id))
    if not user:
        await q.edit_message_text("⚠️ حسابك غير مسجل. استخدم /start أولاً.")
        return ConversationHandler.END

    chain = context.user_data.get("chain", "BEP20")

    try:
        deposits = await get_deposit_history("USDT")
        if not deposits:
            await q.edit_message_text("⌛ لا توجد عمليات جديدة بعد، حاول بعد قليل.")
            return ConversationHandler.END

        # نأخذ آخر عملية فقط — ويمكن لاحقاً ربطها برقم المحفظة الخاصة بالمستخدم
        latest = deposits[0]
        txid = latest.get("tx_id")
        amount = float(latest.get("amount", 0))
        status = latest.get("status")

        if status != "FINISHED":
            await q.edit_message_text("⚠️ العملية لم تكتمل بعد، حاول لاحقاً.")
            return ConversationHandler.END

    except Exception as e:
        logger.error(f"CoinEx Confirm Error: {e}")
        await q.edit_message_text("❌ حدث خطأ أثناء التحقق من العملية.")
        return ConversationHandler.END

    # تحويل المبلغ من USDT → NSP بسعر الأدمن
    rate = store.get_usd_to_nsp_rate()
    nsp_value = int(amount * rate)

    # حفظ المعاملة في قاعدة البيانات
    db = store.getDatabaseConnection()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO coinex_transactions (user_id, chain, usdt_amount, nsp_value, txid, status, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (user["id"], chain, amount, nsp_value, txid, "approved", datetime.now()))
    tx_id = cur.lastrowid
    db.commit()
    db.close()

    # تحديث رصيد المستخدم
    store.add_balance(user["id"], nsp_value)
    store.add_audit_log("coinex", tx_id, "approved", f"Auto deposit {amount} USDT → {nsp_value} NSP")

    await q.edit_message_text(
        f"✅ تم تأكيد الإيداع بنجاح!\n"
        f"💰 المبلغ: {amount} USDT ({nsp_value} NSP)\n"
        f"🔗 السلسلة: {chain}\n"
        f"🆔 TxID: `{txid}`",
        parse_mode="Markdown"
    )

    # إشعار الأدمن (للمتابعة فقط)
    msg = (
        f"💹 تم تسجيل إيداع CoinEx تلقائياً:\n"
        f"👤 المستخدم: @{q.from_user.username or q.from_user.full_name}\n"
        f"💰 {amount} USDT ({nsp_value} NSP)\n"
        f"🔗 {chain}\n🆔 TxID: {txid}"
    )
    for admin in ADMIN_IDS:
        try:
            await context.bot.send_message(admin, msg)
        except:
            pass

    return ConversationHandler.END


def register_handlers(dp):
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_deposit, pattern="^coinex_deposit$")],
        states={
            SELECT_CHAIN: [CallbackQueryHandler(get_address, pattern="^coinex_chain_")],
            CONFIRM_TRANSFER: [CallbackQueryHandler(confirm_transfer, pattern="^coinex_sent$")],
        },
        fallbacks=[],
    )
    dp.add_handler(conv)
