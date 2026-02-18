#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import sys
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler
)
from telegram import Update

from config import BOT_TOKEN, MAIN_ADMIN, ADMINS
from database import db
from handlers import *
from utils import logger

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
(
    PHONE_INPUT,
    DRIVER_FULL_NAME,
    DRIVER_CAR_MODEL,
    DRIVER_CAR_NUMBER,
    DRIVER_EXPERIENCE,
    REVIEW_RATING,
    REVIEW_COMMENT
) = range(7)

def error_handler(update: Update, context):
    """Глобальный обработчик ошибок"""
    logger.error(f"Update {update} caused error {context.error}")
    try:
        if update and update.effective_message:
            update.effective_message.reply_text(
                "❌ Произошла внутренняя ошибка. Администратор уже уведомлен."
            )
        
        # Уведомляем админов об ошибке
        for admin_id in ADMINS:
            try:
                context.bot.send_message(
                    chat_id=admin_id,
                    text=f"🚨 **Ошибка бота**\n\n"
                         f"Пользователь: {update.effective_user.id if update else 'Unknown'}\n"
                         f"Ошибка: {str(context.error)[:200]}"
                )
            except:
                pass
    except:
        pass

def main():
    """Запуск бота"""
    try:
        # Проверка токена
        if not BOT_TOKEN:
            logger.error("BOT_TOKEN not set in .env file")
            return
        
        # Загружаем список админов из БД
        admins = db.execute("SELECT user_id FROM users WHERE role = 'admin'", fetch_all=True)
        if admins:
            for admin in admins:
                if admin['user_id'] not in ADMINS:
                    ADMINS.append(admin['user_id'])
        
        logger.info(f"Loaded {len(ADMINS)} admins from database")
        
        # Создание приложения
        app = Application.builder().token(BOT_TOKEN).build()
        
        # ==================== ОСНОВНЫЕ КОМАНДЫ ====================
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("admin", admin_panel))
        
        # ==================== ОБРАБОТЧИКИ КНОПОК ====================
        # Навигация
        app.add_handler(CallbackQueryHandler(back_to_main, pattern="^back_to_main$"))
        
        # Профиль
        app.add_handler(CallbackQueryHandler(show_profile, pattern="^profile$"))
        app.add_handler(CallbackQueryHandler(set_phone_start, pattern="^set_phone$"))
        
        # Новая поездка
        app.add_handler(CallbackQueryHandler(new_trip, pattern="^new_trip$"))
        app.add_handler(CallbackQueryHandler(trip_select_from, pattern="^trip_select_from$"))
        app.add_handler(CallbackQueryHandler(trip_select_to, pattern="^trip_from_.*$"))
        app.add_handler(CallbackQueryHandler(trip_confirm, pattern="^trip_to_.*$"))
        
        # Принятие заказа
        app.add_handler(CallbackQueryHandler(accept_trip, pattern="^accept_trip_.*$"))
        app.add_handler(CallbackQueryHandler(start_trip, pattern="^start_trip_.*$"))
        app.add_handler(CallbackQueryHandler(complete_trip, pattern="^complete_trip_.*$"))
        app.add_handler(CallbackQueryHandler(start_waiting, pattern="^waiting_.*$"))
        
        # Отзывы
        app.add_handler(CallbackQueryHandler(leave_review, pattern="^review_.*$"))
        app.add_handler(CallbackQueryHandler(submit_review, pattern="^submit_review_.*$"))
        
        # Водители
        app.add_handler(CallbackQueryHandler(become_driver_start, pattern="^become_driver$"))
        app.add_handler(CallbackQueryHandler(driver_online, pattern="^driver_online$"))
        app.add_handler(CallbackQueryHandler(driver_offline, pattern="^driver_offline$"))
        app.add_handler(CallbackQueryHandler(my_trips_driver, pattern="^my_trips_driver$"))
        
        # Пассажиры
        app.add_handler(CallbackQueryHandler(my_trips_passenger, pattern="^my_trips_passenger$"))
        
        # Обучение
        app.add_handler(CallbackQueryHandler(training_start, pattern="^training_start$"))
        app.add_handler(CallbackQueryHandler(training_scenario, pattern="^training_.*$"))
        app.add_handler(CallbackQueryHandler(training_complete, pattern="^training_complete$"))
        app.add_handler(CallbackQueryHandler(skip_training, pattern="^skip_training$"))
        
        # SOS
        app.add_handler(CallbackQueryHandler(sos_alert, pattern="^sos$"))
        
        # Админка
        app.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
        app.add_handler(CallbackQueryHandler(admin_users, pattern="^admin_users$"))
        app.add_handler(CallbackQueryHandler(admin_drivers, pattern="^admin_drivers$"))
        app.add_handler(CallbackQueryHandler(admin_stats, pattern="^admin_stats$"))
        app.add_handler(CallbackQueryHandler(admin_settings, pattern="^admin_settings$"))
        app.add_handler(CallbackQueryHandler(admin_verify_driver, pattern="^verify_driver_.*$"))
        app.add_handler(CallbackQueryHandler(admin_reject_driver, pattern="^reject_driver_.*$"))
        app.add_handler(CallbackQueryHandler(admin_block_user, pattern="^block_user_.*$"))
        app.add_handler(CallbackQueryHandler(admin_unblock_user, pattern="^unblock_user_.*$"))
        
        # ==================== ОБРАБОТЧИКИ СООБЩЕНИЙ ====================
        # Регистрация водителя
        conv_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(become_driver_start, pattern="^become_driver$")],
            states={
                DRIVER_FULL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, driver_full_name)],
                DRIVER_CAR_MODEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, driver_car_model)],
                DRIVER_CAR_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, driver_car_number)],
                DRIVER_EXPERIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, driver_experience)],
            },
            fallbacks=[CommandHandler("start", start)],
        )
        app.add_handler(conv_handler)
        
        # Ввод телефона
        phone_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(set_phone_start, pattern="^set_phone$")],
            states={
                PHONE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_phone_complete)],
            },
            fallbacks=[CommandHandler("start", start)],
        )
        app.add_handler(phone_handler)
        
        # Отзывы
        review_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(leave_review, pattern="^review_.*$")],
            states={
                REVIEW_RATING: [CallbackQueryHandler(review_rating)],
                REVIEW_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, review_comment)],
            },
            fallbacks=[CommandHandler("start", start)],
        )
        app.add_handler(review_handler)
        
        # Обработчик текстовых сообщений (для непойманных)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # Глобальный обработчик ошибок
        app.add_error_handler(error_handler)
        
        logger.info(f"🚀 Бот запущен!")
        logger.info(f"👑 Главный админ: {MAIN_ADMIN}")
        logger.info(f"👥 Админы: {ADMINS}")
        logger.info(f"📊 База данных: {db.db_path}")
        
        # Запуск
        app.run_polling()
        
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        raise

# Заглушки для функций, которые будут определены в handlers.py
async def back_to_main(update, context): pass
async def show_profile(update, context): pass
async def set_phone_start(update, context): pass
async def set_phone_complete(update, context): pass
async def new_trip(update, context): pass
async def trip_select_from(update, context): pass
async def trip_select_to(update, context): pass
async def trip_confirm(update, context): pass
async def accept_trip(update, context): pass
async def start_trip(update, context): pass
async def complete_trip(update, context): pass
async def start_waiting(update, context): pass
async def leave_review(update, context): pass
async def submit_review(update, context): pass
async def review_rating(update, context): pass
async def review_comment(update, context): pass
async def become_driver_start(update, context): pass
async def driver_full_name(update, context): pass
async def driver_car_model(update, context): pass
async def driver_car_number(update, context): pass
async def driver_experience(update, context): pass
async def driver_online(update, context): pass
async def driver_offline(update, context): pass
async def my_trips_driver(update, context): pass
async def my_trips_passenger(update, context): pass
async def training_start(update, context): pass
async def training_scenario(update, context): pass
async def training_complete(update, context): pass
async def skip_training(update, context): pass
async def sos_alert(update, context): pass
async def admin_panel(update, context): pass
async def admin_users(update, context): pass
async def admin_drivers(update, context): pass
async def admin_stats(update, context): pass
async def admin_settings(update, context): pass
async def admin_verify_driver(update, context): pass
async def admin_reject_driver(update, context): pass
async def admin_block_user(update, context): pass
async def admin_unblock_user(update, context): pass
async def handle_message(update, context): pass

if __name__ == '__main__':
    main()