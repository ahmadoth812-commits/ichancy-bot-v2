import logging
from telegram import Bot

logger = logging.getLogger(__name__)

# You need to initialize the bot somewhere in your main application
# and pass it to this utility, or retrieve the token from config.
# For simplicity, we'll assume the bot instance is passed or accessed globally.
_bot_instance: Bot = None

def set_bot_instance(bot: Bot):
    global _bot_instance
    _bot_instance = bot

async def notify_user(telegram_id, message, parse_mode=None):
    if not _bot_instance:
        logger.error("Bot instance not set for notifications utility.")
        return
    try:
        await _bot_instance.send_message(chat_id=telegram_id, text=message, parse_mode=parse_mode)
    except Exception as e:
        logger.error(f"Failed to notify user {telegram_id}: {e}")

async def notify_admin(message, parse_mode=None):
    if not _bot_instance:
        logger.error("Bot instance not set for notifications utility.")
        return
    import config # Import config inside function to avoid circular dependency
    for admin_id in config.ADMIN_IDS:
        try:
            await _bot_instance.send_message(chat_id=admin_id, text=message, parse_mode=parse_mode)
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")