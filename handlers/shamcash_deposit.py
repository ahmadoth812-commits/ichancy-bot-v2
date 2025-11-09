# handlers/shamcash_deposit.py

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    ReplyKeyboardRemove
)
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    CommandHandler,
    filters,
)
import asyncio
import Logger
import store
from services.transaction_notification_service import transaction_notification_service

logger = Logger.getLogger()

# الحالات في المحادثة
SELECT_CURRENCY, ENTER_AMOUNT, ENTER_TXID = range(3)

# ⚙️ استرجاع إعدادات المحفظة من قاعدة البيانات
def get_shamcash_settings():
    try:
        settings = store.get_admin_settings()
        return {
            "wallet_url": settings.get("shamcash_wallet_url", "https://shamcash.com"),
            "qr_image": settings.get("shamcash_qr_image", "https://example.com/qr.png"),
        }
    except Exception as e:
        logger.error(f"Error fetching ShamCash settings: {e}")
        return {
            "wallet_url": "https://shamcash.com",
            "qr_image": "https://example.com/qr.png",
        }

# ⬇️ بدء عملية الشحن
async def start_shamcash_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    keyboard = [
        [
            InlineKeyboardButton("💵 USD", callback_data="shamcash_usd"),
            InlineKeyboardButton("💠 NSP", callback_data="shamcash_nsp"),
        ]
    ]
    await query.edit_message_text(
        text="🔸 *يرجى اختيار نوع العملة التي قمت بالتحويل بها:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SELECT_CURRENCY

# ⬇️ عرض تفاصيل المحفظة (QR + رابط)
async def currency_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    currency = query.data.split("_")[1].upper()
    context.user_data["currency"] = currency

    settings = get_shamcash_settings()

    msg = (
        f"🏦 *تفاصيل التحويل إلى محفظة ShamCash ({currency}):*\n\n"
        f"🔗 رابط المحفظة: {settings['wallet_url']}\n"
        f"📸 *قم بمسح رمز QR لإرسال المبلغ:*\n\n"
        f"💡 *بعد التحويل، اضغط الزر أدناه لإدخال التفاصيل.*"
    )

    keyboard = [[InlineKeyboardButton("تم التحويل ✅", callback_data="confirm_shamcash_transfer")]]
    await query.edit_message_text(
        text=msg,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    await query.message.reply_photo(photo=settings["qr_image"])
    return ConversationHandler.END

# ⬇️ بعد الضغط على "تم التحويل"
async def confirm_shamcash_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text("💰 *يرجى إدخال قيمة المبلغ الذي قمت بتحويله:*", parse_mode="Markdown")
    return ENTER_AMOUNT

# ⬇️ إدخال المبلغ
async def get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.replace('.', '', 1).isdigit():
        await update.message.reply_text("⚠️ الرجاء إدخال قيمة رقمية صحيحة.")
        return ENTER_AMOUNT

    context.user_data["amount"] = float(text)
    await update.message.reply_text("🔢 *يرجى إدخال معرف عملية التحويل (TxID):*", parse_mode="Markdown")
    return ENTER_TXID

# ⬇️ إدخال TxID وتسجيل المعاملة
async def get_txid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txid = update.message.text.strip()
    amount = context.user_data.get("amount")
    currency = context.user_data.get("currency")
    telegram_user_id = str(update.effective_user.id)

    try:
        transaction_id = store.insertTransaction(
            telegram_id=telegram_user_id,
            value=amount,
            action_type="deposit",
            provider_type=f"shamcash_{currency.lower()}",
            transfer_num=txid,
        )

        context.user_data["transaction_id"] = transaction_id
        logger.info(f"Inserted ShamCash transaction #{transaction_id} ({currency}) for {telegram_user_id}")

        # إشعار الأدمن
        asyncio.create_task(
            transaction_notification_service.notify_admin_new_transaction(transaction_id, f"shamcash_{currency.lower()}")
        )

        summary = (
            "✅ *تم تسجيل طلبك للمراجعة من قبل الإدارة.*\n\n"
            "📦 *تفاصيل العملية:*\n"
            f"🔹 العملة: {currency}\n"
            f"🔹 المبلغ: {amount}\n"
            f"🔹 معرف التحويل (TxID): `{txid}`\n"
            f"🆔 رقم الطلب: #{transaction_id}\n\n"
            "⏳ سيتم إشعارك بعد مراجعة طلبك من قبل الأدمن."
        )
        await update.message.reply_text(summary, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error saving ShamCash transaction: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء تسجيل العملية. حاول لاحقًا.")

    context.user_data.clear()
    return ConversationHandler.END

# ⬇️ دالة الإلغاء
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 تم إلغاء عملية الشحن.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ⬇️ ConversationHandler
def conversation_handler():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_shamcash_deposit, pattern="^shamcash_deposit$"),
            CallbackQueryHandler(currency_selected, pattern="^shamcash_(usd|nsp)$"),
            CallbackQueryHandler(confirm_shamcash_transfer, pattern="^confirm_shamcash_transfer$"),
        ],
        states={
            ENTER_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_amount)],
            ENTER_TXID: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_txid)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
