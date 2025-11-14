# main.py
import logging
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
import config
from utils.notifications import set_bot_instance

# === استيراد جميع الهاندلرز ===
from handlers.shamcash_deposit import register_handlers as register_shamcash_deposit
from handlers.syriatelcash_deposit import register_handlers as register_syriatel_deposit
from handlers.coinex_deposit import register_handlers as register_coinex_deposit

from handlers.shamcash_withdraw import register_handlers as register_shamcash_withdraw
from handlers.syriatelcash_withdraw import register_handlers as register_syriatel_withdraw
from handlers.coinex_withdraw import register_handlers as register_coinex_withdraw

from handlers.admin_transactions import register_handlers as register_admin_handlers
from handlers.address_management import register_handlers as register_address_handlers
from handlers.admin_setting_handler import register_handlers as register_admin_setting_handlers


# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ==============================
#       START FUNCTION
# ==============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_name = (getattr(user, "first_name", None) or getattr(user, "username", None) or "المستخدم")

    keyboard = [
        [
            InlineKeyboardButton("💰 رصيدي", callback_data="show_balance"),
            InlineKeyboardButton("📥 إيداع", callback_data="deposit_options")
        ],
        [
            InlineKeyboardButton("📤 سحب", callback_data="withdraw_options"),
            InlineKeyboardButton("🏦 عناويني", callback_data="manage_whitelist_addresses")
        ],
        [
            InlineKeyboardButton("📊 الإحصائيات", callback_data="show_stats"),
            InlineKeyboardButton("🆘 المساعدة", callback_data="show_help")
        ]
    ]

    text = (
        f"مرحباً {user_name}! 👋\n\n"
        "اختر الخدمة التي تريدها:"
    )

    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


# ==============================
#      DEPOSIT OPTIONS
# ==============================
async def deposit_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("🏦 Syriatel Cash", callback_data="syriatel_deposit")],
        [InlineKeyboardButton("💳 ShamCash", callback_data="shamcash_deposit")],
        [InlineKeyboardButton("🌐 CoinEx", callback_data="coinex_deposit")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
    ]

    await query.edit_message_text(
        "📥 اختر طريقة الإيداع:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ==============================
#      WITHDRAW OPTIONS
# ==============================
async def withdraw_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("🏦 Syriatel Cash", callback_data="syriatel_withdraw")],
        [InlineKeyboardButton("💳 ShamCash", callback_data="shamcash_withdraw")],
        [InlineKeyboardButton("🌐 CoinEx", callback_data="coinex_withdraw")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
    ]

    await query.edit_message_text(
        "📤 اختر طريقة السحب:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ==============================
#        SHOW BALANCE
# ==============================
async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import store

    query = update.callback_query
    await query.answer()

    user = store.get_user_by_telegram_id(str(query.from_user.id))

    if not user:
        await query.edit_message_text(
            "⚠️ حسابك غير مسجل. استخدم /start أولاً.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]])
        )
        return

    balance = store.get_user_balance(user["id"])

    if balance is None:
        await query.edit_message_text(
            "⚠️ حدث خطأ أثناء جلب الرصيد. حاول لاحقًا.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]])
        )
        return

    await query.edit_message_text(
        f"💰 رصيدك الحالي: {balance:,} NSP",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]])
    )


# ==============================
#          HELP MENU
# ==============================
async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    help_text = (
        "🆘 مركز المساعدة\n\n"
        "📥 الإيداع:\n"
        "- Syriatel Cash: تحويل إلى أرقام Syriatel\n"
        "- ShamCash: تحويل USD أو NSP\n"
        "- CoinEx: إيداع USDT\n\n"
        "📤 السحب:\n"
        "- Syriatel Cash: سحب إلى الأرقام\n"
        "- ShamCash: سحب إلى محفظتك\n"
        "- CoinEx: سحب USDT\n\n"
        "🏦 العناوين الموثوقة:\n"
        "- أضف عناوينك الآمنة للسحب السريع"
    )

    await query.edit_message_text(
        help_text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]])
    )


# ==============================
#         STATISTICS
# ==============================
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "📊 الإحصائيات قريباً...",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]])
    )


# ==============================
#       BACK TO MAIN MENU
# ==============================
async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await start(update, context)


# ==============================
#       MAIN APPLICATION
# ==============================
def main():
    # تحقق من وجود التوكن قبل محاولة بناء الـ Application
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not configured. ضع TELEGRAM_BOT_TOKEN في .env أو متغيرات البيئة.")
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN")

    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # تمرير نسخة البوت لوحدة الاشعارات (مهم ليعمل notify_user/notify_admin)
    set_bot_instance(application.bot)

    # تحذير إن كانت قائمة المشرفين فارغة لكي لا يفاجئك عدم وصول التنبيهات
    if not getattr(config, "ADMIN_IDS", []):
        logger.warning("ADMIN_IDS غير مهيأ — notify_admin لن يرسل رسائل لمشرفين. ضع ADMIN_IDS في .env إن أردت إشعارات للمشرفين.")

    # أوامر
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", show_help))

    # Back buttons
    application.add_handler(CallbackQueryHandler(back_to_main, pattern="^back_to_main$"))
    application.add_handler(CallbackQueryHandler(deposit_options, pattern="^deposit_options$"))
    application.add_handler(CallbackQueryHandler(withdraw_options, pattern="^withdraw_options$"))
    application.add_handler(CallbackQueryHandler(show_balance, pattern="^show_balance$"))
    application.add_handler(CallbackQueryHandler(show_stats, pattern="^show_stats$"))
    application.add_handler(CallbackQueryHandler(show_help, pattern="^show_help$"))

    # تسجيل كل الهاندلرز
    register_shamcash_deposit(application)
    register_syriatel_deposit(application)
    register_coinex_deposit(application)

    register_shamcash_withdraw(application)
    register_syriatel_withdraw(application)
    register_coinex_withdraw(application)

    register_admin_handlers(application)
    register_address_handlers(application)
    register_admin_setting_handlers(application)

    try:
        print("🤖 البوت يعمل الآن...")
        application.run_polling()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.exception("Unhandled exception while running the bot: %s", e)
    finally:
        logger.info("Application stopped")


if __name__ == "__main__":
    main()