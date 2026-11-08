import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select, func
from aiogram.types import MenuButtonDefault

from config import config
from models import init_db, async_session, User, CardViewLog, ExamAttempt
from logic import today_local
from handlers_user import router as user_router
from handlers_admin import router as admin_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def daily_reminder(bot: Bot):
    today = today_local()
    
    async with async_session() as session:
        res_users = await session.execute(select(User).where(User.notifications_enabled == True))
        users = res_users.scalars().all()

        for user in users:
            res_logs = await session.execute(
                select(CardViewLog).where(
                    CardViewLog.user_id == user.id,
                    CardViewLog.view_date == today
                ).limit(1)
            )
            has_logs = res_logs.scalar_one_or_none() is not None

            res_exams = await session.execute(
                select(ExamAttempt).where(
                    ExamAttempt.user_id == user.id,
                    func.date(ExamAttempt.finished_at) == today.isoformat()
                ).limit(1)
            )
            has_exams = res_exams.scalar_one_or_none() is not None

            if not has_logs and not has_exams:
                try:
                    await bot.send_message(
                        user.id,
                        "⏰ <b>Напоминание</b>\n\n"
                        "Ты сегодня еще не занимался английским! \n"
                        "Удели 5 минут учебе, чтобы не терять прогресс. 🇬🇧"
                    )
                except Exception:
                    pass


async def main() -> None:
    await init_db()
    logger.info("DB initialized")

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Роутеры
    dp.include_router(user_router)
    dp.include_router(admin_router)

    await bot.set_chat_menu_button(menu_button=MenuButtonDefault())

    # Планировщик напоминаний
    scheduler = AsyncIOScheduler(timezone=config.timezone)
    scheduler.add_job(daily_reminder, CronTrigger(hour=22, minute=0), args=[bot])
    scheduler.start()
    logger.info("Scheduler started")

    try:
        logger.info("Starting polling")
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")