# utils/notifications.py
import logging

logger = logging.getLogger(__name__)

_bot_instance = None

def set_bot_instance(bot):
    global _bot_instance
    _bot_instance = bot

async def notify_user(telegram_id, message, parse_mode=None, reply_markup=None):
    if not _bot_instance:
        logger.error("Bot instance not set for notifications utility.")
        return
    try:
        await _bot_instance.send_message(
            chat_id=telegram_id, 
            text=message, 
            parse_mode=parse_mode,
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Failed to notify user {telegram_id}: {e}")

async def notify_admin(message, parse_mode=None, reply_markup=None):
    if not _bot_instance:
        logger.error("Bot instance not set for notifications utility.")
        return
    import config
    for admin_id in config.ADMIN_IDS:
        try:
            await _bot_instance.send_message(
                chat_id=admin_id, 
                text=message, 
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")