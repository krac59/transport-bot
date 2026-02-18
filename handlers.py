import logging
from uuid import uuid4
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database import db
from utils import (
    encrypt_phone, decrypt_phone, validate_phone, format_phone,
    calculate_price, calculate_waiting_charge, format_phone_for_display,
    is_admin, format_datetime, sanitize_input
)
from config import ADMINS, CAR_CLASSES, CITIES, SANDBOX_SCENARIOS

logger = logging.getLogger(__name__)

# ==================== СОСТОЯНИЯ ====================
(
    PHONE_INPUT,
    DRIVER_FULL_NAME,
    DRIVER_CAR_MODEL,
    DRIVER_CAR_NUMBER,
    DRIVER_EXPERIENCE,
    REVIEW_RATING,
    REVIEW_COMMENT
) = range(7)

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

async def safe_edit_message(query, text, reply_markup=None, parse_mode=None):
    """Безопасное редактирование сообщения"""
    try:
        await query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    except Exception as e:
        logger.error(f"Failed to edit message: {e}")
        await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)

def get_main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Получить клавиатуру главного меню"""
    user = db.get_user(user_id)
    role = user['role'] if user else 'passenger'
    
    keyboard = [
        [InlineKeyboardButton("🚗 Новая поездка", callback_data="new_trip")],
        [InlineKeyboardButton("👤 Профиль", callback_data="profile")],
    ]
    
    # Для пассажиров - история поездок
    if role == 'passenger':
        keyboard.append([InlineKeyboardButton("📋 Мои поездки", callback_data="my_trips_passenger")])
    
    # Для водителей
    if role == 'driver':
        driver = db.get_driver(user_id)
        if driver and driver['verified']:
            status = "🟢 Я на линии" if driver['online_status'] else "🔴 Я офлайн"
            keyboard.append([InlineKeyboardButton(status, callback_data="driver_online" if not driver['online_status'] else "driver_offline")])
            keyboard.append([InlineKeyboardButton("📊 Мои поездки", callback_data="my_trips_driver")])
    
    # Стать водителем
    if role == 'passenger':
        keyboard.append([InlineKeyboardButton("🚀 Стать водителем", callback_data="become_driver")])
    
    # Песочница (если не прошёл обучение)
    if not user or not user['training_completed']:
        keyboard.append([InlineKeyboardButton("🎓 Песочница", callback_data="training_start")])
    
    # SOS для всех
    keyboard.append([InlineKeyboardButton("🆘 SOS", callback_data="sos")])
    
    # Админка
    if is_admin(user_id, ADMINS):
        keyboard.append([InlineKeyboardButton("⚙️ Админ панель", callback_data="admin_panel")])
    
    return InlineKeyboardMarkup(keyboard)

# ==================== ОСНОВНЫЕ ОБРАБОТЧИКИ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    
    try:
        # Проверяем существование пользователя
        existing = db.get_user(user.id)
        
        if not existing:
            # Новый пользователь
            db.execute(
                """INSERT INTO users 
                   (user_id, username, first_name, last_name, registration_date, last_active) 
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user.id, user.username, user.first_name, user.last_name,
                 datetime.now().isoformat(), datetime.now().isoformat())
            )
            
            # Логируем регистрацию
            db.log_action(user.id, "register", "New user registered")
            
            # Предлагаем обучение
            await show_training_offer(update, context)
        else:
            # Обновляем last_active
            db.execute(
                "UPDATE users SET last_active = ? WHERE user_id = ?",
                (datetime.now().isoformat(), user.id)
            )
            
            # Проверяем блокировку
            if existing['is_blocked']:
                await update.message.reply_text(
                    "❌ Ваш аккаунт заблокирован.\n"
                    "Для разблокировки свяжитесь с поддержкой."
                )
                return
            
            # Проверяем, прошел ли обучение
            if not existing['training_completed']:
                await show_training_offer(update, context)
            else:
                await show_main_menu(update, context)
    
    except Exception as e:
        logger.error(f"Error in start: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте позже.")

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать главное меню"""
    user = update.effective_user
    
    try:
        keyboard = get_main_keyboard(user.id)
        
        welcome_text = (
            f"👋 **Главное меню**\n\n"
            f"Добро пожаловать, {user.first_name}!\n"
            f"Выберите действие:"
        )
        
        if isinstance(update, Update) and update.callback_query:
            await safe_edit_message(
                update.callback_query,
                welcome_text,
                keyboard,
                ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text(
                welcome_text,
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN
            )
    
    except Exception as e:
        logger.error(f"Error in show_main_menu: {e}")

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вернуться в главное меню"""
    query = update.callback_query
    await query.answer()
    await show_main_menu(update, context)

async def show_training_offer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Предложение обучения"""
    keyboard = [
        [InlineKeyboardButton("🎓 Пройти обучение", callback_data="training_start")],
        [InlineKeyboardButton("⏱ Пропустить", callback_data="skip_training")]
    ]
    
    text = (
        "🎓 **Добро пожаловать!**\n\n"
        "Чтобы пользоваться сервисом, рекомендуем пройти "
        "быстрое обучение в песочнице:\n\n"
        "✅ Как создать заказ\n"
        "✅ Как принять заказ\n"
        "✅ Как действовать в нештатных ситуациях\n\n"
        "Это займёт всего 2-3 минуты."
    )
    
    if isinstance(update, Update) and update.callback_query:
        await safe_edit_message(
            update.callback_query,
            text,
            InlineKeyboardMarkup(keyboard),
            ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

async def skip_training(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пропустить обучение"""
    query = update.callback_query
    await query.answer()
    
    db.execute(
        "UPDATE users SET training_completed = 1 WHERE user_id = ?",
        (query.from_user.id,)
    )
    
    await show_main_menu(update, context)

# ==================== ПРОФИЛЬ И ТЕЛЕФОН ====================

async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать профиль"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user = db.get_user(user_id)
    
    if not user:
        await safe_edit_message(query, "❌ Профиль не найден")
        return
    
    # Получаем информацию о водителе если есть
    driver = db.get_driver(user_id)
    
    # Расшифровываем телефон для показа владельцу
    phone_display = "❌ Не указан"
    if user['phone']:
        decrypted = decrypt_phone(user['phone'])
        phone_display = format_phone(decrypted) if decrypted else "❌ Ошибка"
    
    text = (
        f"👤 **Профиль**\n\n"
        f"**ID:** `{user['user_id']}`\n"
        f"**Имя:** {user['first_name']} {user['last_name'] or ''}\n"
        f"**Username:** @{user['username'] or 'не указан'}\n"
        f"**Телефон:** {phone_display}\n"
        f"**Роль:** {'Водитель' if user['role'] == 'driver' else 'Пассажир'}\n"
        f"**Рейтинг:** ⭐ {user['rating']:.1f}\n"
        f"**Поездок:** {user['trips_count']}\n"
        f"**Регистрация:** {format_datetime(user['registration_date'])}\n"
    )
    
    if driver:
        text += f"\n🚗 **Данные водителя:**\n"
        text += f"• Авто: {driver['car_model']} {driver['car_number']}\n"
        text += f"• Стаж: {driver['experience']} лет\n"
        text += f"• Статус: {'✅ Верифицирован' if driver['verified'] else '⏳ На модерации'}\n"
    
    keyboard = [
        [InlineKeyboardButton("📱 Указать телефон", callback_data="set_phone")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]
    ]
    
    await safe_edit_message(query, text, InlineKeyboardMarkup(keyboard), ParseMode.MARKDOWN)

async def set_phone_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало ввода телефона"""
    query = update.callback_query
    await query.answer()
    
    await safe_edit_message(
        query,
        "📱 **Введите номер телефона**\n\n"
        "Форматы:\n"
        "• `+79991234567`\n"
        "• `89991234567`\n\n"
        "Номер будет виден только участникам поездки."
    )
    
    return PHONE_INPUT

async def set_phone_complete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение телефона"""
    user_id = update.effective_user.id
    phone = update.message.text.strip()
    
    if not validate_phone(phone):
        await update.message.reply_text(
            "❌ Неверный формат. Используйте:\n"
            "• `+79991234567`\n"
            "• `89991234567`",
            parse_mode=ParseMode.MARKDOWN
        )
        return PHONE_INPUT
    
    # Шифруем и сохраняем
    encrypted = encrypt_phone(phone)
    db.execute(
        "UPDATE users SET phone = ? WHERE user_id = ?",
        (encrypted, user_id)
    )
    
    db.log_action(user_id, "set_phone", "Phone number updated")
    
    await update.message.reply_text(
        "✅ **Телефон сохранён!**\n\n"
        "Он будет показываться только участникам ваших поездок.",
        parse_mode=ParseMode.MARKDOWN
    )
    
    await show_main_menu(update, context)
    return ConversationHandler.END

# ==================== НОВАЯ ПОЕЗДКА ====================

async def new_trip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало создания поездки"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("📍 Выбрать города", callback_data="trip_select_from")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]
    ]
    
    await safe_edit_message(
        query,
        "🚗 **Новая поездка**\n\nВыберите города отправления и прибытия:",
        InlineKeyboardMarkup(keyboard),
        ParseMode.MARKDOWN
    )

async def trip_select_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор города отправления"""
    query = update.callback_query
    await query.answer()
    
    # Группируем по регионам
    regions = {}
    for city in CITIES:
        if city['region'] not in regions:
            regions[city['region']] = []
        regions[city['region']].append(city['name'])
    
    keyboard = []
    for region, cities in regions.items():
        keyboard.append([InlineKeyboardButton(f"📍 {region}", callback_data="ignore")])
        for city in sorted(cities):
            keyboard.append([InlineKeyboardButton(
                f"  🏙 {city}",
                callback_data=f"trip_from_{city}"
            )])
    
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="new_trip")])
    
    await safe_edit_message(
        query,
        "Выберите город **отправления**:",
        InlineKeyboardMarkup(keyboard),
        ParseMode.MARKDOWN
    )

async def trip_select_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор города прибытия"""
    query = update.callback_query
    await query.answer()
    
    from_city = query.data.replace("trip_from_", "")
    context.user_data['trip_from'] = from_city
    
    # Получаем все города кроме выбранного
    other_cities = [c['name'] for c in CITIES if c['name'] != from_city]
    
    keyboard = []
    for city in sorted(other_cities):
        keyboard.append([InlineKeyboardButton(
            f"🏁 {city}",
            callback_data=f"trip_to_{city}"
        )])
    
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="trip_select_from")])
    
    await safe_edit_message(
        query,
        f"📍 **Откуда:** {from_city}\n\n"
        f"Выберите город **прибытия**:",
        InlineKeyboardMarkup(keyboard),
        ParseMode.MARKDOWN
    )
async def trip_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение и создание поездки"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    to_city = query.data.replace("trip_to_", "")
    from_city = context.user_data.get('trip_from')
    
    if not from_city or not to_city:
        await safe_edit_message(
            query,
            "❌ Ошибка: не выбран маршрут. Начните заново.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Новая поездка", callback_data="new_trip")
            ]])
        )
        return
    
    # Проверяем, есть ли у пользователя телефон
    user = db.get_user(user_id)
    if not user or not user['phone']:
        keyboard = [
            [InlineKeyboardButton("📱 Указать телефон", callback_data="set_phone")],
            [InlineKeyboardButton("◀️ Назад", callback_data="new_trip")]
        ]
        await safe_edit_message(
            query,
            "❌ **Для заказа нужен телефон**\n\n"
            "Укажите номер телефона, чтобы водитель мог с вами связаться.",
            InlineKeyboardMarkup(keyboard),
            ParseMode.MARKDOWN
        )
        return
    
    # Рассчитываем цену
    price, distance = calculate_price(from_city, to_city)
    
    # Создаем поездку
    trip_id = str(uuid4())
    db.execute(
        """INSERT INTO trips 
           (trip_id, passenger_id, from_city, to_city, price, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (trip_id, user_id, from_city, to_city, price, 'searching', datetime.now().isoformat())
    )
    
    db.log_action(user_id, "create_trip", f"{from_city}→{to_city} price:{price}")
    
    # Сохраняем trip_id в контекст
    context.user_data['current_trip'] = trip_id
    
    # Показываем ожидание
    keyboard = [
        [InlineKeyboardButton("🔄 Проверить статус", callback_data=f"check_trip_{trip_id}")],
        [InlineKeyboardButton("❌ Отменить заказ", callback_data=f"cancel_trip_{trip_id}")]
    ]
    
    await safe_edit_message(
        query,
        f"✅ **Заказ создан!**\n\n"
        f"📍 Маршрут: {from_city} → {to_city}\n"
        f"💰 Цена: {price} ₽\n"
        f"📏 Расстояние: {distance} км\n\n"
        f"⏳ Ищем водителя... Это займёт несколько минут.\n\n"
        f"Мы уведомим вас, когда водитель найдётся.",
        InlineKeyboardMarkup(keyboard),
        ParseMode.MARKDOWN
    )
    
    # Ищем свободных водителей
    await notify_drivers_about_trip(context, trip_id, from_city, to_city, price)

async def notify_drivers_about_trip(context: ContextTypes.DEFAULT_TYPE, trip_id: str, from_city: str, to_city: str, price: int):
    """Уведомление водителей о новом заказе"""
    # Получаем свободных водителей
    drivers = db.execute(
        """SELECT d.user_id, u.first_name 
           FROM drivers d
           JOIN users u ON d.user_id = u.user_id
           WHERE d.verified = 1 AND d.online_status = 1
           LIMIT 10""",
        fetch_all=True
    )
    
    if not drivers:
        logger.info(f"No drivers available for trip {trip_id}")
        return
    
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🚗 Принять заказ", callback_data=f"accept_trip_{trip_id}")
    ]])
    
    for driver in drivers:
        try:
            await context.bot.send_message(
                chat_id=driver['user_id'],
                text=f"🔔 **Новый заказ!**\n\n"
                     f"📍 {from_city} → {to_city}\n"
                     f"💰 Цена: {price} ₽\n\n"
                     f"Нажмите кнопку ниже, чтобы принять заказ.",
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to notify driver {driver['user_id']}: {e}")

# ==================== УПРАВЛЕНИЕ ПОЕЗДКОЙ ====================

async def accept_trip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Водитель принимает заказ"""
    query = update.callback_query
    await query.answer()
    
    driver_id = query.from_user.id
    trip_id = query.data.replace("accept_trip_", "")
    
    # Проверяем, что поездка ещё в поиске
    trip = db.execute(
        "SELECT * FROM trips WHERE trip_id = ? AND status = 'searching'",
        (trip_id,),
        fetch_one=True
    )
    
    if not trip:
        await safe_edit_message(
            query,
            "❌ Этот заказ уже недоступен.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ В меню", callback_data="back_to_main")
            ]])
        )
        return
    
    # Проверяем, что водитель верифицирован
    driver = db.get_driver(driver_id)
    if not driver or not driver['verified']:
        await safe_edit_message(
            query,
            "❌ Вы не можете принимать заказы.\n"
            "Дождитесь верификации или свяжитесь с администратором."
        )
        return
    
    # Обновляем поездку
    db.execute(
        """UPDATE trips 
           SET driver_id = ?, status = ?, accepted_at = ? 
           WHERE trip_id = ?""",
        (driver_id, 'accepted', datetime.now().isoformat(), trip_id)
    )
    
    db.log_action(driver_id, "accept_trip", f"Trip {trip_id}")
    
    # Уведомляем пассажира
    passenger_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🚗 Начать поездку", callback_data=f"start_trip_{trip_id}")
    ]])
    
    try:
        # Расшифровываем телефон для пассажира
        driver_phone = "Не указан"
        if driver['phone']:
            driver_phone = format_phone(decrypt_phone(driver['phone']))
        
        await context.bot.send_message(
            chat_id=trip['passenger_id'],
            text=f"✅ **Водитель найден!**\n\n"
                 f"🚗 Водитель: {driver['full_name']}\n"
                 f"📞 Телефон: {driver_phone}\n"
                 f"🚘 Авто: {driver['car_model']} {driver['car_number']}\n"
                 f"⭐ Рейтинг: {db.get_user(driver_id)['rating']:.1f}\n\n"
                 f"Скоро водитель будет на месте.",
            reply_markup=passenger_keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Failed to notify passenger: {e}")
    
    # Показываем водителю информацию о пассажире
    passenger = db.get_user(trip['passenger_id'])
    passenger_phone = "Не указан"
    if passenger and passenger['phone']:
        passenger_phone = format_phone(decrypt_phone(passenger['phone']))
    
    driver_trip_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚗 Начать поездку", callback_data=f"start_trip_{trip_id}")],
        [InlineKeyboardButton("📞 Позвонить пассажиру", callback_data=f"call_passenger_{trip_id}")]
    ])
    
    await safe_edit_message(
        query,
        f"✅ **Заказ принят!**\n\n"
        f"📍 Маршрут: {trip['from_city']} → {trip['to_city']}\n"
        f"💰 Цена: {trip['price']} ₽\n\n"
        f"👤 Пассажир: {passenger['first_name']}\n"
        f"📞 Телефон: {passenger_phone}\n\n"
        f"Выезжайте на место подачи.",
        driver_trip_keyboard,
        ParseMode.MARKDOWN
    )

async def start_trip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало поездки"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    trip_id = query.data.replace("start_trip_", "")
    
    # Получаем поездку
    trip = db.execute(
        "SELECT * FROM trips WHERE trip_id = ? AND status = 'accepted'",
        (trip_id,),
        fetch_one=True
    )
    
    if not trip:
        await safe_edit_message(
            query,
            "❌ Нельзя начать эту поездку.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ В меню", callback_data="back_to_main")
            ]])
        )
        return
    
    # Обновляем статус
    db.execute(
        "UPDATE trips SET status = ?, started_at = ? WHERE trip_id = ?",
        ('started', datetime.now().isoformat(), trip_id)
    )
    
    # Создаем клавиатуру с таксометром
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏱ Начать ожидание", callback_data=f"waiting_start_{trip_id}")],
        [InlineKeyboardButton("✅ Завершить поездку", callback_data=f"complete_trip_{trip_id}")]
    ])
    
    # Уведомляем второго участника
    other_id = trip['passenger_id'] if user_id == trip['driver_id'] else trip['driver_id']
    try:
        await context.bot.send_message(
            chat_id=other_id,
            text="🚗 **Поездка началась!**\n\nПриятного пути!",
            parse_mode=ParseMode.MARKDOWN
        )
    except:
        pass
    
    await safe_edit_message(
        query,
        "🚗 **Поездка началась!**\n\n"
        "Используйте таксометр при ожидании.\n"
        "По окончании нажмите 'Завершить поездку'.",
        keyboard,
        ParseMode.MARKDOWN
    )

async def start_waiting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать отсчёт ожидания"""
    query = update.callback_query
    await query.answer()
    
    trip_id = query.data.replace("waiting_start_", "")
    
    # Сохраняем время начала ожидания
    context.user_data[f'waiting_start_{trip_id}'] = datetime.now()
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏱ Остановить ожидание", callback_data=f"waiting_stop_{trip_id}")],
        [InlineKeyboardButton("✅ Завершить поездку", callback_data=f"complete_trip_{trip_id}")]
    ])
    
    await safe_edit_message(
        query,
        "⏱ **Ожидание начато**\n\n"
        "• до 2 мин - бесплатно\n"
        "• 2-5 мин - 3₽/мин\n"
        "• 5-7 мин - 4₽/мин\n"
        "• >7 мин - 5₽/мин\n\n"
        "Нажмите 'Остановить ожидание', когда продолжите движение.",
        keyboard,
        ParseMode.MARKDOWN
    )

async def stop_waiting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Остановить отсчёт ожидания"""
    query = update.callback_query
    await query.answer()
    
    trip_id = query.data.replace("waiting_stop_", "")
    start_time = context.user_data.get(f'waiting_start_{trip_id}')
    
    if start_time:
        minutes = int((datetime.now() - start_time).total_seconds() / 60)
        charge = calculate_waiting_charge(minutes)
        
        # Сохраняем в БД
        db.execute(
            "UPDATE trips SET waiting_minutes = ?, waiting_charge = ? WHERE trip_id = ?",
            (minutes, charge, trip_id)
        )
        
        # Удаляем из контекста
        del context.user_data[f'waiting_start_{trip_id}']
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Завершить поездку", callback_data=f"complete_trip_{trip_id}")]
        ])
        
        await safe_edit_message(
            query,
            f"⏱ **Ожидание остановлено**\n\n"
            f"Время ожидания: {minutes} мин\n"
            f"Доплата: +{charge} ₽\n\n"
            f"Можете продолжать поездку.",
            keyboard,
            ParseMode.MARKDOWN
        )
    else:
        await safe_edit_message(query, "Ошибка: время ожидания не найдено")

async def complete_trip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершение поездки"""
    query = update.callback_query
    await query.answer()
    
    trip_id = query.data.replace("complete_trip_", "")
    
    # Получаем поездку
    trip = db.execute(
        "SELECT * FROM trips WHERE trip_id = ? AND status = 'started'",
        (trip_id,),
        fetch_one=True
    )
    
    if not trip:
        trip = db.execute(
            "SELECT * FROM trips WHERE trip_id = ?",
            (trip_id,),
            fetch_one=True
        )
        if trip and trip['status'] == 'completed':
            await safe_edit_message(
                query,
                "✅ Эта поездка уже завершена."
            )
        else:
            await safe_edit_message(
                query,
                "❌ Нельзя завершить эту поездку."
            )
        return
    
    # Рассчитываем финальную цену
    final_price = trip['price'] + (trip['waiting_charge'] or 0)
    
    # Обновляем поездку
    db.execute(
        """UPDATE trips 
           SET status = ?, completed_at = ?, final_price = ? 
           WHERE trip_id = ?""",
        ('completed', datetime.now().isoformat(), final_price, trip_id)
    )
    
    # Обновляем статистику пользователей
    for uid in [trip['passenger_id'], trip['driver_id']]:
        db.execute(
            "UPDATE users SET trips_count = trips_count + 1 WHERE user_id = ?",
            (uid,)
        )
    
    db.log_action(query.from_user.id, "complete_trip", f"Trip {trip_id} final:{final_price}")
    
    # Предлагаем оставить отзыв
    review_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("⭐ Оставить отзыв", callback_data=f"review_{trip_id}")
    ]])
    
    # Уведомляем обоих участников
    for uid in [trip['passenger_id'], trip['driver_id']]:
        try:
            other_id = trip['driver_id'] if uid == trip['passenger_id'] else trip['passenger_id']
            other_user = db.get_user(other_id)
            
            await context.bot.send_message(
                chat_id=uid,
                text=f"✅ **Поездка завершена!**\n\n"
                     f"📍 Маршрут: {trip['from_city']} → {trip['to_city']}\n"
                     f"💰 Итоговая цена: {final_price} ₽\n"
                     f"⏱ Ожидание: {trip['waiting_minutes'] or 0} мин\n\n"
                     f"Спасибо, что пользуетесь нашим сервисом!",
                reply_markup=review_keyboard if uid == trip['passenger_id'] else None,
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass
    
    await safe_edit_message(
        query,
        f"✅ **Поездка завершена!**\n\n"
        f"📍 {trip['from_city']} → {trip['to_city']}\n"
        f"💰 Цена: {trip['price']} ₽\n"
        f"⏱ Доплата за ожидание: +{trip['waiting_charge'] or 0} ₽\n"
        f"💵 Итого: {final_price} ₽\n\n"
        f"Спасибо за поездку!",
        ParseMode.MARKDOWN
    )
# ==================== ОТЗЫВЫ ====================

async def leave_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать оставление отзыва"""
    query = update.callback_query
    await query.answer()
    
    trip_id = query.data.replace("review_", "")
    context.user_data['review_trip'] = trip_id
    
    keyboard = []
    for i in range(1, 6):
        stars = "⭐" * i
        keyboard.append([InlineKeyboardButton(
            f"{stars} - {i}", 
            callback_data=f"review_rating_{i}"
        )])
    
    keyboard.append([InlineKeyboardButton("◀️ Пропустить", callback_data="back_to_main")])
    
    await safe_edit_message(
        query,
        "⭐ **Оцените поездку**\n\n"
        "Оцените от 1 до 5 звёзд:",
        InlineKeyboardMarkup(keyboard),
        ParseMode.MARKDOWN
    )
    
    return REVIEW_RATING

async def review_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение оценки"""
    query = update.callback_query
    await query.answer()
    
    rating = int(query.data.replace("review_rating_", ""))
    context.user_data['review_rating'] = rating
    
    await safe_edit_message(
        query,
        f"⭐ Оценка: {rating}\n\n"
        f"📝 Напишите комментарий к отзыву "
        f"(или отправьте /skip чтобы пропустить):"
    )
    
    return REVIEW_COMMENT

async def review_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение отзыва с комментарием"""
    user_id = update.effective_user.id
    comment = update.message.text
    
    if comment == "/skip":
        comment = ""
    
    await save_review(context, user_id, comment)
    
    await update.message.reply_text(
        "✅ Спасибо за отзыв!",
        reply_markup=get_main_keyboard(user_id)
    )
    
    return ConversationHandler.END

async def save_review(context: ContextTypes.DEFAULT_TYPE, user_id: int, comment: str = ""):
    """Сохранение отзыва в БД"""
    trip_id = context.user_data.get('review_trip')
    rating = context.user_data.get('review_rating')
    
    if not trip_id or not rating:
        return
    
    # Получаем поездку
    trip = db.execute(
        "SELECT * FROM trips WHERE trip_id = ?",
        (trip_id,),
        fetch_one=True
    )
    
    if not trip:
        return
    
    # Определяем, кому оставляем отзыв
    to_user = trip['driver_id'] if user_id == trip['passenger_id'] else trip['passenger_id']
    
    # Сохраняем отзыв
    db.execute(
        """INSERT INTO reviews 
           (trip_id, from_user, to_user, rating, comment, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (trip_id, user_id, to_user, rating, comment, datetime.now().isoformat())
    )
    
    # Обновляем рейтинг пользователя
    avg_rating = db.execute(
        "SELECT AVG(rating) as avg FROM reviews WHERE to_user = ?",
        (to_user,),
        fetch_one=True
    )['avg']
    
    db.execute(
        "UPDATE users SET rating = ? WHERE user_id = ?",
        (avg_rating, to_user)
    )
    
    # Очищаем контекст
    context.user_data.pop('review_trip', None)
    context.user_data.pop('review_rating', None)

# ==================== РЕГИСТРАЦИЯ ВОДИТЕЛЯ ====================

async def become_driver_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало регистрации водителя"""
    query = update.callback_query
    await query.answer()
    
    # Проверяем, не водитель ли уже
    existing = db.get_driver(query.from_user.id)
    if existing:
        if existing['verified']:
            await safe_edit_message(
                query,
                "✅ Вы уже зарегистрированы как водитель!",
                InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ В меню", callback_data="back_to_main")
                ]])
            )
        else:
            await safe_edit_message(
                query,
                "⏳ Ваша заявка на рассмотрении.\n"
                "Ожидайте подтверждения администратора."
            )
        return
    
    # Показываем условия
    text = (
        "🚀 **Регистрация водителя**\n\n"
        "Для регистрации необходимо указать:\n\n"
        "1️⃣ **ФИО** (полностью)\n"
        "2️⃣ **Марка и модель авто**\n"
        "3️⃣ **Государственный номер**\n"
        "4️⃣ **Стаж вождения** (лет)\n\n"
        "⚠️ **Важно:**\n"
        "• Все данные будут проверены\n"
        "• После регистрации заявка отправляется на модерацию\n"
        "• Верификация может занять до 24 часов\n\n"
        "Готовы начать?"
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ Начать регистрацию", callback_data="driver_reg_start")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]
    ]
    
    await safe_edit_message(
        query,
        text,
        InlineKeyboardMarkup(keyboard),
        ParseMode.MARKDOWN
    )
    
    return DRIVER_FULL_NAME

async def driver_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение ФИО водителя"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await safe_edit_message(query, "📝 Введите ваше **ФИО полностью**:")
        return DRIVER_FULL_NAME
    
    full_name = sanitize_input(update.message.text)
    
    if len(full_name.split()) < 2:
        await update.message.reply_text(
            "❌ Введите фамилию и имя (минимум 2 слова)"
        )
        return DRIVER_FULL_NAME
    
    context.user_data['driver_full_name'] = full_name
    await update.message.reply_text("✅ ФИО сохранено!\n\n🚗 Введите **марку и модель** автомобиля:")
    return DRIVER_CAR_MODEL

async def driver_car_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение марки авто"""
    car_model = sanitize_input(update.message.text)
    
    if len(car_model) < 3:
        await update.message.reply_text("❌ Слишком короткое название")
        return DRIVER_CAR_MODEL
    
    context.user_data['driver_car_model'] = car_model
    await update.message.reply_text("✅ Модель сохранена!\n\n🔢 Введите **государственный номер** (например: А123ВВ777):")
    return DRIVER_CAR_NUMBER

async def driver_car_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение госномера"""
    car_number = sanitize_input(update.message.text).upper()
    
    # Простая валидация
    if len(car_number) < 6 or len(car_number) > 9:
        await update.message.reply_text(
            "❌ Неверный формат номера. Пример: А123ВВ777"
        )
        return DRIVER_CAR_NUMBER
    
    context.user_data['driver_car_number'] = car_number
    await update.message.reply_text("✅ Номер сохранён!\n\n📊 Введите **стаж вождения** (полных лет):")
    return DRIVER_EXPERIENCE

async def driver_experience(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение стажа и завершение регистрации"""
    try:
        experience = int(update.message.text)
        if experience < 0 or experience > 70:
            raise ValueError
    except:
        await update.message.reply_text("❌ Введите число от 0 до 70")
        return DRIVER_EXPERIENCE
    
    user_id = update.effective_user.id
    
    # Сохраняем данные
    db.execute(
        """INSERT INTO drivers 
           (user_id, full_name, car_model, car_number, experience, verified, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, 
         context.user_data['driver_full_name'],
         context.user_data['driver_car_model'],
         context.user_data['driver_car_number'],
         experience, 0, datetime.now().isoformat())
    )
    
    # Обновляем роль пользователя
    db.execute(
        "UPDATE users SET role = 'driver' WHERE user_id = ?",
        (user_id,)
    )
    
    db.log_action(user_id, "driver_registration", "Application submitted")
    
    # Уведомляем админов
    await notify_admins_about_new_driver(context, user_id)
    
    # Очищаем контекст
    context.user_data.clear()
    
    await update.message.reply_text(
        "✅ **Заявка отправлена!**\n\n"
        "Администратор проверит данные и свяжется с вами.\n"
        "Обычно это занимает не более 24 часов.\n\n"
        "После подтверждения вы сможете принимать заказы.",
        parse_mode=ParseMode.MARKDOWN
    )
    
    await show_main_menu(update, context)
    return ConversationHandler.END

async def notify_admins_about_new_driver(context: ContextTypes.DEFAULT_TYPE, driver_id: int):
    """Уведомление админов о новой заявке водителя"""
    driver = db.get_driver(driver_id)
    user = db.get_user(driver_id)
    
    if not driver or not user:
        return
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Принять", callback_data=f"verify_driver_{driver_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_driver_{driver_id}")
        ]
    ])
    
    for admin_id in ADMINS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"🚀 **Новая заявка водителя!**\n\n"
                     f"👤 {driver['full_name']}\n"
                     f"🆔 ID: {driver_id}\n"
                     f"📞 Телефон: {format_phone(decrypt_phone(user['phone'])) if user['phone'] else 'Не указан'}\n"
                     f"🚗 Авто: {driver['car_model']} {driver['car_number']}\n"
                     f"📊 Стаж: {driver['experience']} лет\n\n"
                     f"Время заявки: {format_datetime(driver['created_at'])}",
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")

# ==================== АДМИН ПАНЕЛЬ ====================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Панель администратора"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if not is_admin(user_id, ADMINS):
        await query.answer("❌ Доступ запрещен", show_alert=True)
        return
    
    # Статистика
    users_count = db.execute("SELECT COUNT(*) as c FROM users", fetch_one=True)['c']
    drivers_count = db.execute("SELECT COUNT(*) as c FROM drivers", fetch_one=True)['c']
    pending_drivers = db.execute("SELECT COUNT(*) as c FROM drivers WHERE verified = 0", fetch_one=True)['c']
    trips_today = db.execute(
        "SELECT COUNT(*) as c FROM trips WHERE date(created_at) = date('now')",
        fetch_one=True
    )['c']
    
    text = (
        "⚙️ **Админ панель**\n\n"
        f"👥 Пользователей: {users_count}\n"
        f"🚗 Водителей: {drivers_count} (⏳ {pending_drivers} новых)\n"
        f"📅 Поездок сегодня: {trips_today}\n\n"
        f"**Управление:**"
    )
    
    keyboard = [
        [InlineKeyboardButton("👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton("🚗 Заявки водителей", callback_data="admin_drivers_pending")],
        [InlineKeyboardButton("✅ Верифицированные", callback_data="admin_drivers_verified")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="admin_settings")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]
    ]
    
    await safe_edit_message(
        query,
        text,
        InlineKeyboardMarkup(keyboard),
        ParseMode.MARKDOWN
    )

async def admin_drivers_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список заявок водителей"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if not is_admin(user_id, ADMINS):
        await query.answer("❌ Доступ запрещен", show_alert=True)
        return
    
    pending = db.execute(
        """SELECT d.*, u.phone, u.first_name 
           FROM drivers d
           JOIN users u ON d.user_id = u.user_id
           WHERE d.verified = 0
           ORDER BY d.created_at DESC""",
        fetch_all=True
    )
    
    if not pending:
        await safe_edit_message(
            query,
            "📭 Нет новых заявок",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")
            ]])
        )
        return
    
    text = "🚗 **Новые заявки водителей:**\n\n"
    keyboard = []
    
    for driver in pending[:5]:  # По 5 за раз
        text += f"• {driver['full_name']}\n"
        text += f"  🆔 `{driver['user_id']}`\n"
        text += f"  🚗 {driver['car_model']} {driver['car_number']}\n"
        text += f"  📊 Стаж: {driver['experience']} лет\n"
        text += f"  🕐 {format_datetime(driver['created_at'])}\n\n"
        
        keyboard.append([
            InlineKeyboardButton(f"✅ {driver['full_name'][:15]}", callback_data=f"verify_driver_{driver['user_id']}"),
            InlineKeyboardButton(f"❌ Отклонить", callback_data=f"reject_driver_{driver['user_id']}")
        ])
    
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")])
    
    await safe_edit_message(
        query,
        text,
        InlineKeyboardMarkup(keyboard),
        ParseMode.MARKDOWN
    )

async def admin_verify_driver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение водителя"""
    query = update.callback_query
    admin_id = query.from_user.id
    
    if not is_admin(admin_id, ADMINS):
        await query.answer("❌ Доступ запрещен", show_alert=True)
        return
    
    driver_id = int(query.data.replace("verify_driver_", ""))
    
    # Обновляем статус
    db.execute(
        "UPDATE drivers SET verified = 1, verified_by = ?, verified_at = ? WHERE user_id = ?",
        (admin_id, datetime.now().isoformat(), driver_id)
    )
    
    db.log_action(admin_id, "verify_driver", f"Driver {driver_id} verified")
    
    # Уведомляем водителя
    try:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🚗 Выйти на линию", callback_data="driver_online")
        ]])
        
        await context.bot.send_message(
            chat_id=driver_id,
            text="✅ **Поздравляем!**\n\n"
                 "Ваша заявка одобрена. Теперь вы можете принимать заказы.\n\n"
                 "Нажмите кнопку 'Я на линии', чтобы начать получать заказы.",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Failed to notify driver {driver_id}: {e}")
    
    await query.answer("✅ Водитель подтверждён", show_alert=True)
    
    # Возвращаемся к списку
    await admin_drivers_pending(update, context)

async def admin_reject_driver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отклонение заявки водителя"""
    query = update.callback_query
    admin_id = query.from_user.id
    
    if not is_admin(admin_id, ADMINS):
        await query.answer("❌ Доступ запрещен", show_alert=True)
        return
    
    driver_id = int(query.data.replace("reject_driver_", ""))
    
    # Удаляем или помечаем отклонённым
    db.execute(
        "UPDATE drivers SET verified = -1, verified_by = ?, verified_at = ? WHERE user_id = ?",
        (admin_id, datetime.now().isoformat(), driver_id)
    )
    
    db.log_action(admin_id, "reject_driver", f"Driver {driver_id} rejected")
    
    # Уведомляем водителя
    try:
        await context.bot.send_message(
            chat_id=driver_id,
            text="❌ **Заявка отклонена**\n\n"
                 "К сожалению, ваша заявка не прошла проверку.\n"
                 "Свяжитесь с администратором для уточнения причин.",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Failed to notify driver {driver_id}: {e}")
    
    await query.answer("❌ Заявка отклонена", show_alert=True)
    
    # Возвращаемся к списку
    await admin_drivers_pending(update, context)

# ==================== СТАТИСТИКА ВОДИТЕЛЯ ====================

async def my_trips_driver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """История поездок водителя"""
    query = update.callback_query
    await query.answer()
    
    driver_id = query.from_user.id
    
    trips = db.execute(
        """SELECT t.*, u.first_name as passenger_name
           FROM trips t
           JOIN users u ON t.passenger_id = u.user_id
           WHERE t.driver_id = ? AND t.status = 'completed'
           ORDER BY t.completed_at DESC
           LIMIT 10""",
        (driver_id,),
        fetch_all=True
    )
    
    if not trips:
        await safe_edit_message(
            query,
            "📭 У вас пока нет завершённых поездок.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")
            ]])
        )
        return
    
    # Статистика
    total_earned = sum(t['final_price'] or t['price'] for t in trips)
    avg_rating = db.execute(
        "SELECT AVG(rating) as avg FROM reviews WHERE to_user = ?",
        (driver_id,),
        fetch_one=True
    )['avg'] or 0
    
    text = (
        f"📊 **Ваша статистика**\n\n"
        f"⭐ Рейтинг: {avg_rating:.1f}\n"
        f"💰 Всего заработано: {total_earned} ₽\n"
        f"🚗 Поездок: {len(trips)}\n\n"
        f"**Последние поездки:**\n\n"
    )
    
    for trip in trips:
        text += f"• {trip['from_city']} → {trip['to_city']}\n"
        text += f"  Пассажир: {trip['passenger_name']}\n"
        text += f"  Цена: {trip['final_price'] or trip['price']} ₽\n"
        text += f"  🕐 {format_datetime(trip['completed_at'])}\n\n"
    
    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]]
    
    await safe_edit_message(
        query,
        text,
        InlineKeyboardMarkup(keyboard),
        ParseMode.MARKDOWN
    )

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ ====================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    # Если не в диалоге - игнорируем
    if not context.user_data:
        await update.message.reply_text(
            "Используйте кнопки меню для навигации.",
            reply_markup=get_main_keyboard(update.effective_user.id)
        )