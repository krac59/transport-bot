import re
import base64
import logging
from datetime import datetime
from cryptography.fernet import Fernet
from config import ENCRYPTION_KEY, DISTANCES, CAR_CLASSES

logger = logging.getLogger(__name__)

# Инициализация шифрования
try:
    # Fernet требует ключ ровно 32 байта в base64
    key = base64.urlsafe_b64encode(ENCRYPTION_KEY.encode().ljust(32)[:32])
    cipher = Fernet(key)
except Exception as e:
    logger.error(f"Failed to initialize cipher: {e}")
    cipher = None

def encrypt_phone(phone: str) -> str:
    """Шифрование номера телефона"""
    if not phone or not cipher:
        return ""
    try:
        return cipher.encrypt(phone.encode()).decode()
    except Exception as e:
        logger.error(f"Encryption error: {e}")
        return ""

def decrypt_phone(encrypted: str) -> str:
    """Расшифровка номера телефона"""
    if not encrypted or not cipher:
        return ""
    try:
        return cipher.decrypt(encrypted.encode()).decode()
    except Exception as e:
        logger.error(f"Decryption error: {e}")
        return ""

def validate_phone(phone: str) -> bool:
    """Проверка формата телефона"""
    if not phone:
        return False
    # Убираем пробелы, дефисы, скобки
    cleaned = re.sub(r'[\s\-\(\)]', '', phone)
    # Проверяем форматы: +79991234567 или 89991234567
    pattern = r'^(\+7|8)[0-9]{10}$'
    return bool(re.match(pattern, cleaned))

def format_phone(phone: str) -> str:
    """Форматирование номера для отображения"""
    cleaned = re.sub(r'[\s\-\(\)]', '', phone)
    if len(cleaned) == 11:
        return f"+7 ({cleaned[1:4]}) {cleaned[4:7]}-{cleaned[7:9]}-{cleaned[9:11]}"
    return phone

def validate_email(email: str) -> bool:
    """Проверка email"""
    if not email:
        return False
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))

def calculate_price(from_city: str, to_city: str, car_class: str = 'economy') -> tuple:
    """Расчет стоимости поездки"""
    try:
        # Получаем расстояние
        distance = DISTANCES.get((from_city, to_city))
        if not distance:
            distance = DISTANCES.get((to_city, from_city), 400)
        
        # Базовая цена
        car_info = CAR_CLASSES.get(car_class, CAR_CLASSES['economy'])
        base_km = car_info['base_price']
        min_price = car_info['min_price']
        
        # Подача + километраж
        price = 60 + (distance * base_km)
        
        # Округляем до 50 рублей
        price = max(min_price, round(price / 50) * 50)
        
        return price, distance
    except Exception as e:
        logger.error(f"Price calculation error: {e}")
        return 500, 400  # Значение по умолчанию

def calculate_waiting_charge(minutes: int) -> int:
    """Расчет платы за ожидание"""
    if minutes <= 2:
        return 0
    elif minutes <= 5:
        return (minutes - 2) * 3
    elif minutes <= 7:
        return (3 * 3) + (minutes - 5) * 4
    else:
        return (3 * 3) + (2 * 4) + (minutes - 7) * 5

def format_phone_for_display(encrypted: str, user_id: int, trip_id: str = None, db=None) -> str:
    """Форматирование номера для показа (только участникам поездки)"""
    if not encrypted:
        return "❌ Не указан"
    
    # Проверяем, участник ли поездки
    if trip_id and db:
        trip = db.execute(
            "SELECT passenger_id, driver_id FROM trips WHERE trip_id = ?",
            (trip_id,),
            fetch_one=True
        )
        if trip and (user_id == trip['passenger_id'] or user_id == trip['driver_id']):
            phone = decrypt_phone(encrypted)
            return format_phone(phone) if phone else "❌ Ошибка расшифровки"
    
    return "🔒 Скрыт (доступен после подтверждения)"

def is_admin(user_id: int, admins_list: list) -> bool:
    """Проверка, является ли пользователь админом"""
    return user_id in admins_list

def format_datetime(dt_str: str) -> str:
    """Форматирование даты для отображения"""
    try:
        dt = datetime.fromisoformat(dt_str)
        return dt.strftime("%d.%m.%Y %H:%M")
    except:
        return dt_str

def sanitize_input(text: str) -> str:
    """Очистка ввода от потенциально опасных символов"""
    if not text:
        return ""
    # Удаляем управляющие символы
    return re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)