from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot
import database
import time
import logging

logger = logging.getLogger(__name__)

async def check_expirations(bot: Bot):
    # Сутки в миллисекундах
    ONE_DAY_MS = 24 * 60 * 60 * 1000
    users_to_notify = database.get_users_to_notify(ONE_DAY_MS)
    
    for tg_id, email, expiry_time in users_to_notify:
        try:
            # Расчет оставшихся часов
            remaining_ms = expiry_time - int(time.time() * 1000)
            remaining_hours = int(remaining_ms / (1000 * 60 * 60))
            
            await bot.send_message(
                chat_id=tg_id,
                text=f"⚠️ **Внимание!** Срок действия вашей подписки `{email}` истекает примерно через {remaining_hours} ч.\n\n"
                     f"Вы можете продлить подписку, выбрав тариф в меню."
            )
            database.set_notified(tg_id)
        except Exception as e:
            logger.error(f"Failed to send expiration warning to {tg_id}: {e}")

def start_scheduler(bot: Bot):
    scheduler = AsyncIOScheduler()
    # Проверка каждые 30 минут
    scheduler.add_job(check_expirations, "interval", minutes=30, args=[bot])
    scheduler.start()
