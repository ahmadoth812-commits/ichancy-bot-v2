# utils/notifications.py
import asyncio
import logging
import os
from typing import Any, Optional, Sequence

import config

logger = logging.getLogger(__name__)

_bot_instance: Optional[Any] = None

# حد التوازي الافتراضي عند ارسال إشعارات للمشرفين (يمكن تغييره عبر env NOTIF_ADMIN_CONCURRENCY)
_ADMIN_CONCURRENCY = int(os.getenv("NOTIF_ADMIN_CONCURRENCY", "5"))


def set_bot_instance(bot: Any):
    """
    خزّن مرجع البوت (استدعاء مرة واحدة عند بدء التطبيق).
    يتوقع أن يحتوي الكائن على coroutine send_message(...)
    """
    global _bot_instance
    if bot is None or not hasattr(bot, "send_message"):
        raise TypeError("bot must be an object with a send_message coroutine method")
    _bot_instance = bot
    logger.debug("Bot instance set for notifications")


async def notify_user(
    telegram_id: int,
    message: str,
    parse_mode: Optional[str] = None,
    reply_markup: Optional[Any] = None,
    timeout: float = 10.0,
) -> bool:
    """
    إرسال رسالة لمستخدم واحد. ترجع True إذا نجحت، False إن فشل.
    """
    if not _bot_instance:
        logger.error("Bot instance not set for notifications utility.")
        return False

    try:
        coro = _bot_instance.send_message(
            chat_id=telegram_id, text=message, parse_mode=parse_mode, reply_markup=reply_markup
        )
        await asyncio.wait_for(coro, timeout=timeout)
        return True
    except asyncio.TimeoutError:
        logger.error("Timeout sending message to %s", telegram_id)
    except Exception as e:
        logger.exception("Failed to notify user %s: %s", telegram_id, e)
    return False


async def notify_admin(
    message: str,
    parse_mode: Optional[str] = None,
    reply_markup: Optional[Any] = None,
    concurrency: Optional[int] = None,
    retry_attempts: int = 0,
    retry_delay: float = 1.0,
) -> dict:
    """
    إرسال الرسالة لكل المشرفين (ADMIN_IDS) مع تحكم بالتوازي وإمكانية إعادة المحاولة.
    يعيد dict من شكل {admin_id: success_bool}
    """
    if not _bot_instance:
        logger.error("Bot instance not set for notifications utility.")
        return {}

    admin_ids: Sequence[int] = getattr(config, "ADMIN_IDS", []) or []
    if not admin_ids:
        logger.info("No admin IDs configured; skip notify_admin")
        return {}

    conc = concurrency or _ADMIN_CONCURRENCY
    sem = asyncio.Semaphore(conc)

    async def _send_with_retries(admin_id: int) -> tuple[int, bool]:
        attempt = 0
        while attempt <= retry_attempts:
            try:
                async with sem:
                    coro = _bot_instance.send_message(
                        chat_id=admin_id, text=message, parse_mode=parse_mode, reply_markup=reply_markup
                    )
                    await coro
                    return admin_id, True
            except Exception as exc:
                logger.warning("notify_admin attempt %s failed for %s: %s", attempt + 1, admin_id, exc)
                attempt += 1
                if attempt <= retry_attempts:
                    await asyncio.sleep(retry_delay * attempt)
        return admin_id, False

    tasks = [asyncio.create_task(_send_with_retries(a)) for a in admin_ids]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    return {admin_id: success for admin_id, success in results}