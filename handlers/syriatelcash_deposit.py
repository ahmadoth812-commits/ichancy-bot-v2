# handlers/syriatel_cash_deposit.py

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

# تعريف الحالات في المحادثة
SELECT_AMOUNT, ENTER_TRANSFER_NUM = range(2)

# ⚙️ عرض أرقام التحويل (تأتي من إعدادات قاعدة البيانات)
def get_syriatel_numbers():
    """إرجاع قائمة أرقام التحويل من إعدادات DB"""
    try:
        settings = store.get_admin_settings()
        return settings.get("syriatel_numbers", ["83935571", "00229271"])
    except Exception as e:
        logger.error(f"Error fetching Syriatel numbers: {e}")
        return ["83935571", "00229271"]

# ⬇️ دالة بدء عملية الشحن
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "syriatel_cash_deposit":
        numbers = get_syriatel_numbers()
        message_text = (
            "🔹 *الرجاء التحويل إلى أحد الأرقام التالية بطريقة التحويل اليدوي:*\n\n"
            f"📱 {numbers[0]}\n"
            f"📱 {numbers[1]}\n\n"
            "⚠️ *أقل قيمة للشحن هي 25,000 SYP*\n"
            "يرجى عدم إرسال مبالغ أقل لأنها لن تُقبل أو تُسترجع.\n\n"
            "بعد التحويل، اضغط الزر أدناه 👇"
        )
        keyboard = [[InlineKeyboardButton("تم التحويل ✅", callback_data="confirm_transfer")]]
        await query.edit_message_text(
            text=message_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return ConversationHandler.END

# ⬇️ بدء إدخال المبلغ بعد الضغط على "تم التحويل"
async def confirm_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text("💰 *يرجى إدخال قيمة المبلغ الذي قمت بتحويله (SYP):*", parse_mode="Markdown")
    return SELECT_AMOUNT

# ⬇️ استلام المبلغ من المستخدم
async def get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) < 25000:
        await update.message.reply_text("⚠️ المبلغ غير صالح. الرجاء إدخال مبلغ رقمي لا يقل عن 25,000 SYP.")
        return SELECT_AMOUNT

    context.user_data["amount"] = int(text)
    await update.message.reply_text("🔢 *يرجى إدخال رقم عملية التحويل:*", parse_mode="Markdown")
    return ENTER_TRANSFER_NUM

# ⬇️ استلام رقم عملية التحويل
async def get_transfer_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    transfer_num = update.message.text.strip()
    amount = context.user_data.get("amount")
    telegram_user_id = str(update.effective_user.id)

    # حفظ المعاملة في قاعدة البيانات
    try:
        transaction_id = store.insertTransaction(
            telegram_id=telegram_user_id,
            value=amount,
            action_type="deposit",
            provider_type="syriatel",
            transfer_num=transfer_num,
        )

        context.user_data["transaction_id"] = transaction_id
        logger.info(f"Inserted new Syriatel transaction #{transaction_id} for {telegram_user_id}")

        # إشعار الأدمن تلقائيًا
        asyncio.create_task(
            transaction_notification_service.notify_admin_new_transaction(transaction_id, "syriatel")
        )

        # رسالة تأكيد للمستخدم
        summary = (
            "✅ *تم إرسال طلبك للمراجعة من قبل الإدارة.*\n\n"
            "📦 *تفاصيل العملية:*\n"
            f"🔹 رقم العملية: `{transfer_num}`\n"
            f"💰 المبلغ: {amount:,} SYP\n"
            f"🆔 رقم الطلب: #{transaction_id}\n\n"
            "⏳ سيتم إشعارك بعد مراجعة طلبك من قبل الأدمن."
        )

        await update.message.reply_text(summary, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error creating Syriatel transaction: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء تسجيل العملية. حاول لاحقًا.")

    # تنظيف بيانات المستخدم من الجلسة
    context.user_data.clear()
    return ConversationHandler.END

# ⬇️ دالة الإلغاء
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 تم إلغاء عملية الشحن.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ⬇️ إنشاء ConversationHandler للربط مع البوت
def conversation_handler():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(button_handler, pattern="^syriatel_cash_deposit$"),
            CallbackQueryHandler(confirm_transfer, pattern="^confirm_transfer$"),
        ],
        states={
            SELECT_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_amount)],
            ENTER_TRANSFER_NUM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_transfer_number)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
