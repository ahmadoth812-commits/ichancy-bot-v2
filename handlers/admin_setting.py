# handlers/admin_settings.py
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
import config
import store

# -------------------------
# Helpers
# -------------------------
def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS

# -------------------------
# /show_settings
# -------------------------
async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        return await update.message.reply_text("❌ ليس لديك صلاحية الوصول إلى هذه الأوامر.")

    # Prefer async getters where possible
    usd_rate = await store.async_get_usd_to_nsp_rate()
    sham_wallet = await store.async_get_shamcash_wallet()
    syriatel_nums = await store.async_get_syriatel_numbers()

    text = (
        "⚙️ *الإعدادات الحالية:*\n\n"
        f"💲 USD → NSP Rate: `{usd_rate}`\n"
        f"💼 ShamCash Wallet: `{sham_wallet}`\n"
        f"📱 Syriatel Numbers: `{', '.join(syriatel_nums) if syriatel_nums else 'غير محددة'}`\n"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💲 تعديل معدل التحويل", callback_data="admin_set_rate")],
        [InlineKeyboardButton("💼 تعديل محفظة ShamCash", callback_data="admin_set_wallet")],
        [InlineKeyboardButton("📱 تعديل أرقام Syriatel", callback_data="admin_set_syriatel")],
        [InlineKeyboardButton("🔄 تحديث القيم", callback_data="admin_refresh_settings")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_back_to_help")]
    ])

    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")

# -------------------------
# /set_rate
# -------------------------
async def set_usd_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        return await update.message.reply_text("❌ ليس لديك صلاحية.")

    args = context.args
    if len(args) != 1:
        return await update.message.reply_text("❗ استخدم الأمر بهذا الشكل:\n`/set_rate 5200`", parse_mode="Markdown")
    try:
        new_rate = int(args[0])
        if new_rate <= 0:
            raise ValueError()
    except ValueError:
        return await update.message.reply_text("⚠️ الرجاء إدخال رقم موجب صحيح.\nمثال: `/set_rate 5200`", parse_mode="Markdown")

    store.update_usd_to_nsp_rate(new_rate)
    await update.message.reply_text(f"✅ تم تحديث معدل التحويل إلى {new_rate} NSP لكل 1 USD")

# -------------------------
# /set_shamcash_wallet
# -------------------------
async def set_shamcash_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        return await update.message.reply_text("❌ ليس لديك صلاحية.")

    args = context.args
    if len(args) < 1:
        return await update.message.reply_text("❗ استخدم الأمر بهذا الشكل:\n`/set_shamcash_wallet 0999888777`", parse_mode="Markdown")

    new_wallet = " ".join(args).strip()
    if not new_wallet:
        return await update.message.reply_text("⚠️ الرجاء إدخال عنوان أو رقم محفظة صالح.")

    store.update_shamcash_wallet(new_wallet)
    await update.message.reply_text(f"💼 تم تحديث محفظة ShamCash إلى:\n`{new_wallet}`", parse_mode="Markdown")

# -------------------------
# /set_syriatel_numbers
# -------------------------
async def set_syriatel_numbers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        return await update.message.reply_text("❌ ليس لديك صلاحية.")

    args = context.args
    if len(args) < 1:
        return await update.message.reply_text("❗ استخدم الأمر بهذا الشكل:\n`/set_syriatel_numbers 0999888777,0988111222`", parse_mode="Markdown")

    nums_raw = " ".join(args)
    numbers = [n.strip() for n in nums_raw.split(",") if n.strip()]
    if not numbers:
        return await update.message.reply_text("⚠️ لم يتم العثور على أرقام صالحة.")

    store.update_syriatel_numbers(numbers)
    await update.message.reply_text(f"📱 تم تحديث أرقام سيريتل إلى:\n`{', '.join(numbers)}`", parse_mode="Markdown")

# -------------------------
# /help_admin
# -------------------------
async def help_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        return await update.message.reply_text("❌ ليس لديك صلاحية لاستخدام هذا الأمر.")

    text = (
        "🧭 *لوحة أوامر الأدمن:*\n\n"
        "🔹 /show_settings — عرض الإعدادات الحالية\n"
        "🔹 /set_rate <number> — ضبط معدل USD → NSP\n"
        "🔹 /set_shamcash_wallet <wallet> — تعديل محفظة ShamCash\n"
        "🔹 /set_syriatel_numbers <num1,num2> — تعديل أرقام Syriatel\n\n"
        "أو استخدم الأزرار أدناه:"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ عرض الإعدادات", callback_data="admin_show_settings"),
         InlineKeyboardButton("💲 ضبط المعدل", callback_data="admin_set_rate")],
        [InlineKeyboardButton("💼 ضبط المحفظة", callback_data="admin_set_wallet"),
         InlineKeyboardButton("📱 ضبط أرقام Syriatel", callback_data="admin_set_syriatel")]
    ])
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")

# -------------------------
# CallbackQuery handler for buttons
# -------------------------
async def handle_admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    if not is_admin(user.id):
        return await q.answer("❌ غير مصرح لك.", show_alert=True)

    action = q.data

    # Make fake message object for reuse of show_settings/help_admin if needed
    if action in ("admin_show_settings", "admin_refresh_settings"):
        # reuse show_settings by creating a message-like object
        fake_msg = q.message
        fake_msg.from_user = q.from_user
        # call show_settings with a fake Update: easiest is to call its logic directly
        await show_settings(update, context)
        return

    if action == "admin_set_rate":
        await q.message.reply_text("💲 أرسل الآن الأمر:\n`/set_rate 5200`", parse_mode="Markdown")
    elif action == "admin_set_wallet":
        await q.message.reply_text("💼 أرسل الآن الأمر:\n`/set_shamcash_wallet 0999888777`", parse_mode="Markdown")
    elif action == "admin_set_syriatel":
        await q.message.reply_text("📱 أرسل الآن الأمر:\n`/set_syriatel_numbers 0999888777,0988111222`", parse_mode="Markdown")
    elif action == "admin_back_to_help":
        await help_admin(update, context)

# -------------------------
# Register function
# -------------------------
def register_handlers(dp):
    dp.add_handler(CommandHandler("help_admin", help_admin))
    dp.add_handler(CommandHandler("show_settings", show_settings))
    dp.add_handler(CommandHandler("set_rate", set_usd_rate))
    dp.add_handler(CommandHandler("set_shamcash_wallet", set_shamcash_wallet))
    dp.add_handler(CommandHandler("set_syriatel_numbers", set_syriatel_numbers))
    dp.add_handler(CallbackQueryHandler(handle_admin_buttons, pattern="^admin_"))
