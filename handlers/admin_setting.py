from aiogram import types, Dispatcher
from config import ADMIN_IDS
import store

# ====================================================
# 🛡️ التحقق من صلاحيات الأدمن
# ====================================================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ====================================================
# ⚙️ عرض الإعدادات الحالية مع لوحة تفاعلية
# ====================================================
async def show_settings(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.reply("❌ ليس لديك صلاحية الوصول إلى هذه الأوامر.")

    usd_rate = store.get_usd_to_nsp_rate() or "غير محدد"
    sham_wallet = store.get_shamcash_wallet() or "غير محددة"
    syriatel_nums = store.get_syriatel_numbers() or []

    text = (
        "⚙️ **الإعدادات الحالية:**\n\n"
        f"💲 *USD → NSP Rate:* `{usd_rate}`\n"
        f"💼 *ShamCash Wallet:* `{sham_wallet}`\n"
        f"📱 *Syriatel Numbers:* `{', '.join(syriatel_nums) if syriatel_nums else 'غير محددة'}`"
    )

    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("💲 تعديل معدل التحويل", callback_data="admin_set_rate"),
        types.InlineKeyboardButton("💼 تعديل محفظة ShamCash", callback_data="admin_set_wallet"),
        types.InlineKeyboardButton("📱 تعديل أرقام Syriatel", callback_data="admin_set_syriatel"),
        types.InlineKeyboardButton("🔄 تحديث القيم", callback_data="admin_refresh_settings"),
        types.InlineKeyboardButton("🔙 رجوع", callback_data="admin_back_to_help")
    )

    await message.reply(text, reply_markup=keyboard, parse_mode="Markdown")


# ====================================================
# 💲 تعديل معدل التحويل
# ====================================================
async def set_usd_rate(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.reply("❌ ليس لديك صلاحية.")

    args = message.text.split()
    if len(args) != 2:
        return await message.reply("❗ استخدم الأمر بهذا الشكل:\n`/set_rate 5200`", parse_mode="Markdown")

    try:
        new_rate = int(args[1])
        if new_rate <= 0:
            raise ValueError
    except ValueError:
        return await message.reply("⚠️ الرجاء إدخال رقم موجب صحيح.\nمثال: `/set_rate 5200`", parse_mode="Markdown")

    store.update_usd_to_nsp_rate(new_rate)
    await message.reply(f"✅ تم تحديث معدل التحويل إلى {new_rate} NSP لكل 1 USD")


# ====================================================
# 💼 تعديل محفظة ShamCash
# ====================================================
async def set_shamcash_wallet(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.reply("❌ ليس لديك صلاحية.")

    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        return await message.reply("❗ استخدم الأمر بهذا الشكل:\n`/set_shamcash_wallet 0999888777`", parse_mode="Markdown")

    new_wallet = args[1].strip()
    if not new_wallet:
        return await message.reply("⚠️ الرجاء إدخال عنوان أو رقم محفظة صالح.")

    store.update_shamcash_wallet(new_wallet)
    await message.reply(f"💼 تم تحديث محفظة ShamCash إلى:\n`{new_wallet}`", parse_mode="Markdown")


# ====================================================
# 📱 تعديل أرقام Syriatel
# ====================================================
async def set_syriatel_numbers(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.reply("❌ ليس لديك صلاحية.")

    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        return await message.reply(
            "❗ استخدم الأمر بهذا الشكل:\n`/set_syriatel_numbers 0999888777,0988111222`",
            parse_mode="Markdown"
        )

    numbers = [num.strip() for num in args[1].split(",") if num.strip()]
    if not numbers:
        return await message.reply("⚠️ لم يتم العثور على أرقام صالحة.")

    store.update_syriatel_numbers(numbers)
    await message.reply(f"📱 تم تحديث أرقام سيريتل إلى:\n`{', '.join(numbers)}`", parse_mode="Markdown")


# ====================================================
# 🧭 لوحة مساعدة الأدمن
# ====================================================
async def help_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.reply("❌ ليس لديك صلاحية لاستخدام هذا الأمر.")

    text = (
        "🧭 **لوحة أوامر الأدمن:**\n\n"
        "🔹 `/show_settings` — عرض جميع الإعدادات الحالية\n"
        "🔹 `/set_rate <number>` — ضبط معدل التحويل USD → NSP\n"
        "🔹 `/set_shamcash_wallet <wallet>` — تعديل محفظة ShamCash\n"
        "🔹 `/set_syriatel_numbers <num1,num2>` — تعديل أرقام Syriatel\n\n"
        "👇 يمكنك استخدام لوحة التحكم أدناه:"
    )

    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("⚙️ عرض الإعدادات", callback_data="admin_show_settings"),
        types.InlineKeyboardButton("💲 ضبط المعدل", callback_data="admin_set_rate"),
        types.InlineKeyboardButton("💼 ضبط المحفظة", callback_data="admin_set_wallet"),
        types.InlineKeyboardButton("📱 ضبط Syriatel", callback_data="admin_set_syriatel")
    )

    await message.reply(text, reply_markup=keyboard, parse_mode="Markdown")


# ====================================================
# 🎛️ التعامل مع الأزرار التفاعلية
# ====================================================
async def handle_admin_buttons(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if not is_admin(user_id):
        return await callback_query.answer("❌ غير مصرح لك.", show_alert=True)

    action = callback_query.data
    await callback_query.answer()

    fake_msg = callback_query.message
    fake_msg.from_user = callback_query.from_user

    if action in ["admin_show_settings", "admin_refresh_settings"]:
        await show_settings(fake_msg)
    elif action == "admin_set_rate":
        await callback_query.message.reply("💲 أرسل الآن:\n`/set_rate 5200`", parse_mode="Markdown")
    elif action == "admin_set_wallet":
        await callback_query.message.reply("💼 أرسل الآن:\n`/set_shamcash_wallet 0999888777`", parse_mode="Markdown")
    elif action == "admin_set_syriatel":
        await callback_query.message.reply("📱 أرسل الآن:\n`/set_syriatel_numbers 0999888777,0988111222`", parse_mode="Markdown")
    elif action == "admin_back_to_help":
        await help_admin(fake_msg)


# ====================================================
# 🧩 تسجيل الأوامر والـ callbacks
# ====================================================
def register_admin_settings_handlers(dp: Dispatcher):
    dp.register_message_handler(help_admin, commands=["help_admin"])
    dp.register_message_handler(show_settings, commands=["show_settings"])
    dp.register_message_handler(set_usd_rate, commands=["set_rate"])
    dp.register_message_handler(set_shamcash_wallet, commands=["set_shamcash_wallet"])
    dp.register_message_handler(set_syriatel_numbers, commands=["set_syriatel_numbers"])
    dp.register_callback_query_handler(handle_admin_buttons, lambda c: c.data.startswith("admin_"))
