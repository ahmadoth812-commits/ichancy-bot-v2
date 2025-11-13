# ملف جديد: address_management.py
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
    CommandHandler,
)
import store
import config

logger = logging.getLogger(__name__)

# حالات المحادثة
ADD_ADDRESS, CONFIRM_ADDRESS, MANAGE_ADDRESSES = range(3)

async def start_address_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء إدارة العناوين الموثوقة"""
    query = update.callback_query
    await query.answer()
    
    user = store.get_user_by_telegram_id(str(query.from_user.id))
    if not user:
        await query.edit_message_text("⚠️ حسابك غير مسجل.")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("➕ إضافة عنوان جديد", callback_data="add_whitelist_address")],
        [InlineKeyboardButton("📋 عرض عناويني", callback_data="view_my_addresses")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="cancel_action")]
    ]
    
    await query.edit_message_text(
        "🏦 إدارة العناوين الموثوقة\n\n"
        "هنا يمكنك إدارة عناوين المحافظ الموثوقة للسحب إلى CoinEx.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return MANAGE_ADDRESSES

async def add_new_address_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء إضافة عنوان جديد"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("🟢 BEP20", callback_data="chain_BEP20")],
        [InlineKeyboardButton("🔵 TRC20", callback_data="chain_TRC20")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="manage_addresses")]
    ]
    
    await query.edit_message_text(
        "🌐 اختر نوع الشبكة للعنوان:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ADD_ADDRESS

async def get_address_chain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الحصول على نوع الشبكة"""
    query = update.callback_query
    await query.answer()
    
    chain = query.data.split('_')[1]
    context.user_data['chain'] = chain
    
    await query.edit_message_text(
        f"📩 الرجاء إرسال عنوان محفظة {chain}:\n\n"
        "⚠️ تأكد من صحة العنوان قبل الإرسال.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="add_whitelist_address")]])
    )
    return ADD_ADDRESS

async def save_whitelist_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حفظ العنوان في القائمة البيضاء"""
    address = update.message.text.strip()
    chain = context.user_data.get('chain')
    user_telegram_id = str(update.effective_user.id)
    
    user = store.get_user_by_telegram_id(user_telegram_id)
    if not user:
        await update.message.reply_text("⚠️ حسابك غير مسجل.")
        context.user_data.clear()
        return ConversationHandler.END

    # التحقق من صحة العنوان الأساسي
    if len(address) < 20 or not all(c in '0123456789abcdefABCDEF' for c in address.replace('0x', '')):
        await update.message.reply_text(
            "❌ العنوان غير صالح. يرجى إرسال عنوان محفظة صحيح.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إعادة المحاولة", callback_data="add_whitelist_address")]])
        )
        return ADD_ADDRESS

    # التحقق إذا كان العنوان موجود مسبقاً
    existing_addresses = store.get_whitelisted_addresses(user["id"], chain)
    for addr in existing_addresses:
        if addr["address"].lower() == address.lower():
            await update.message.reply_text(
                "⚠️ هذا العنوان مضاف مسبقاً إلى قائمتك.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="manage_addresses")]])
            )
            context.user_data.clear()
            return ConversationHandler.END

    # حفظ العنوان
    address_id = store.add_whitelisted_address(user["id"], address, chain)
    
    if address_id:
        store.add_audit_log("whitelist_address", address_id, "added", 
                           actor=f"user_{user_telegram_id}", 
                           reason=f"Added {chain} address to whitelist")
        
        await update.message.reply_text(
            f"✅ تم إضافة العنوان بنجاح إلى قائمتك الموثوقة.\n\n"
            f"🔗 الشبكة: {chain}\n"
            f"🏦 العنوان: `{address}`\n\n"
            "يمكنك الآن استخدام هذا العنوان للسحب.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏦 إدارة العناوين", callback_data="manage_addresses")]])
        )
    else:
        await update.message.reply_text(
            "❌ فشل في إضافة العنوان. يرجى المحاولة لاحقاً.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="manage_addresses")]])
        )
    
    context.user_data.clear()
    return ConversationHandler.END

async def view_my_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض عناوين المستخدم"""
    query = update.callback_query
    await query.answer()
    
    user = store.get_user_by_telegram_id(str(query.from_user.id))
    if not user:
        await query.edit_message_text("⚠️ حسابك غير مسجل.")
        return ConversationHandler.END

    addresses = store.get_whitelisted_addresses(user["id"])
    
    if not addresses:
        await query.edit_message_text(
            "📭 لا توجد عناوين موثوقة في قائمتك.\n\n"
            "اضف عنواناً موثوقاً لتتمكن من السحب إلى CoinEx.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ إضافة عنوان", callback_data="add_whitelist_address")]])
        )
        return MANAGE_ADDRESSES

    message = "📋 عناوينك الموثوقة:\n\n"
    keyboard = []
    
    for addr in addresses:
        status = "🟢" if addr["is_active"] else "🔴"
        label = f" - {addr['label']}" if addr["label"] else ""
        message += f"{status} {addr['chain']}: `{addr['address']}`{label}\n\n"
        keyboard.append([InlineKeyboardButton(f"🗑️ حذف {addr['chain']}", callback_data=f"remove_address_{addr['id']}")])

    keyboard.append([InlineKeyboardButton("➕ إضافة عنوان جديد", callback_data="add_whitelist_address")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="manage_addresses")])
    
    await query.edit_message_text(
        message,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return MANAGE_ADDRESSES

async def remove_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إزالة عنوان من القائمة"""
    query = update.callback_query
    await query.answer()
    
    address_id = int(query.data.split('_')[2])
    user_telegram_id = str(query.from_user.id)
    
    user = store.get_user_by_telegram_id(user_telegram_id)
    if not user:
        await query.edit_message_text("⚠️ حسابك غير مسجل.")
        return ConversationHandler.END

    if store.remove_whitelisted_address(address_id):
        store.add_audit_log("whitelist_address", address_id, "removed", 
                           actor=f"user_{user_telegram_id}", 
                           reason="User removed address from whitelist")
        
        await query.edit_message_text(
            "✅ تم إزالة العنوان من قائمتك الموثوقة.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏦 العودة للإدارة", callback_data="manage_addresses")]])
        )
    else:
        await query.edit_message_text(
            "❌ فشل في إزالة العنوان. يرجى المحاولة لاحقاً.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="manage_addresses")]])
        )
    
    return ConversationHandler.END

async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء العملية"""
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❎ تم إلغاء العملية.")
    elif update.message:
        await update.message.reply_text("❎ تم إلغاء العملية.")
    context.user_data.clear()
    return ConversationHandler.END

def register_handlers(dp):
    """تسجيل handlers لإدارة العناوين"""
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_address_management, pattern="^manage_whitelist_addresses$")],
        states={
            MANAGE_ADDRESSES: [
                CallbackQueryHandler(add_new_address_start, pattern="^add_whitelist_address$"),
                CallbackQueryHandler(view_my_addresses, pattern="^view_my_addresses$"),
                CallbackQueryHandler(remove_address, pattern="^remove_address_"),
            ],
            ADD_ADDRESS: [
                CallbackQueryHandler(get_address_chain, pattern="^chain_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_whitelist_address),
            ],
        },
        fallbacks=[CallbackQueryHandler(cancel_action, pattern="^cancel_action$"),
                   CallbackQueryHandler(cancel_action, pattern="^manage_addresses$"),
                   CommandHandler("cancel", cancel_action)],
    )
    
    dp.add_handler(conv)