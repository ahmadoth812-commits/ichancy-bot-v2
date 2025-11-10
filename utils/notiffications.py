# utils/notifications.py
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from datetime import datetime
import asyncio
import logging

# 🔧 إعدادات عامة
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ⚙️ هذه الدالة ستُستخدم من الملفات الأخرى لإرسال إشعارات موحدة
async def notify_user(user_id: int, message: str, bot: Bot = None):
    """
    إرسال إشعار إلى المستخدم.
    - user_id: معرف المستخدم في التلغرام.
    - message: نص الرسالة المراد إرسالها.
    - bot: كائن الـBot، يتم تمريره من الملفات الأخرى.
    """
    if bot is None:
        logger.warning(f"⚠️ لم يتم تمرير كائن bot لإرسال إشعار إلى المستخدم {user_id}")
        return

    try:
        await bot.send_message(chat_id=user_id, text=message)
        logger.info(f"📩 تم إرسال إشعار للمستخدم {user_id}: {message}")
    except TelegramForbiddenError:
        logger.warning(f"🚫 المستخدم {user_id} حظر البوت أو أوقفه.")
    except TelegramBadRequest as e:
        logger.error(f"❌ خطأ أثناء إرسال رسالة للمستخدم {user_id}: {e}")
    except Exception as e:
        logger.error(f"⚠️ فشل إرسال إشعار للمستخدم {user_id}: {e}")


async def notify_admins(admin_ids: list[int], message: str, bot: Bot):
    """
    إرسال إشعار جماعي لجميع الأدمن.
    - admin_ids: قائمة بمعرفات الأدمن.
    - message: نص الإشعار.
    - bot: كائن الـBot.
    """
    for admin_id in admin_ids:
        try:
            await bot.send_message(chat_id=admin_id, text=message)
            await asyncio.sleep(0.5)  # لتفادي Flood Limit
        except Exception as e:
            logger.error(f"⚠️ فشل إرسال إشعار للأدمن {admin_id}: {e}")


async def notify_transaction_created(user_id: int, tx_type: str, amount: float, currency: str, bot: Bot):
    """
    إشعار المستخدم عند إنشاء معاملة جديدة (إيداع / سحب).
    """
    msg = (
        f"📦 <b>تم تسجيل طلب {tx_type}</b>\n"
        f"💰 المبلغ: {amount} {currency}\n"
        f"🕒 الوقت: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"⏳ الطلب قيد المراجعة من قبل الإدارة."
    )
    await notify_user(user_id, msg, bot)


async def notify_transaction_update(user_id: int, tx_type: str, status: str, amount: float, currency: str, bot: Bot, reason: str = None):
    """
    إشعار المستخدم بعد تحديث حالة العملية (موافقة أو رفض).
    """
    if status == "approved":
        msg = (
            f"✅ <b>تمت الموافقة على عملية {tx_type}</b>\n"
            f"💰 المبلغ: {amount} {currency}\n"
            f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"🎉 تم تنفيذ العملية بنجاح."
        )
    else:
        msg = (
            f"🚫 <b>تم رفض عملية {tx_type}</b>\n"
            f"💰 المبلغ: {amount} {currency}\n"
            f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📝 السبب: {reason or 'غير محدد'}"
        )
    await notify_user(user_id, msg, bot)
