import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime
from config import DATABASE_PATH, DEFAULT_SETTINGS

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.db_path = DATABASE_PATH
        self.init_db()
    
    @contextmanager
    def get_connection(self):
        """Контекстный менеджер для соединения с БД"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            if conn:
                conn.close()
    
    def init_db(self):
        """Инициализация таблиц"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Пользователи
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        first_name TEXT,
                        last_name TEXT,
                        phone TEXT,
                        role TEXT DEFAULT 'passenger',
                        is_blocked INTEGER DEFAULT 0,
                        registration_date TEXT,
                        last_active TEXT,
                        rating REAL DEFAULT 5.0,
                        trips_count INTEGER DEFAULT 0,
                        training_completed INTEGER DEFAULT 0,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Водители
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS drivers (
                        user_id INTEGER PRIMARY KEY,
                        full_name TEXT NOT NULL,
                        phone TEXT,
                        car_model TEXT NOT NULL,
                        car_number TEXT NOT NULL,
                        car_class TEXT DEFAULT 'economy',
                        experience INTEGER DEFAULT 0,
                        verified INTEGER DEFAULT 0,
                        verified_by INTEGER,
                        verified_at TEXT,
                        online_status INTEGER DEFAULT 0,
                        rejection_reason TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
                    )
                ''')
                
                # Поездки
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS trips (
                        trip_id TEXT PRIMARY KEY,
                        passenger_id INTEGER NOT NULL,
                        driver_id INTEGER,
                        from_city TEXT NOT NULL,
                        to_city TEXT NOT NULL,
                        price REAL NOT NULL,
                        car_class TEXT DEFAULT 'economy',
                        status TEXT DEFAULT 'searching',
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        accepted_at TEXT,
                        started_at TEXT,
                        completed_at TEXT,
                        cancelled_by INTEGER,
                        cancel_reason TEXT,
                        waiting_minutes INTEGER DEFAULT 0,
                        waiting_charge REAL DEFAULT 0,
                        final_price REAL,
                        is_test INTEGER DEFAULT 0,
                        FOREIGN KEY (passenger_id) REFERENCES users (user_id),
                        FOREIGN KEY (driver_id) REFERENCES users (user_id)
                    )
                ''')
                
                # Отзывы
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS reviews (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        trip_id TEXT NOT NULL,
                        from_user INTEGER NOT NULL,
                        to_user INTEGER NOT NULL,
                        rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
                        comment TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (trip_id) REFERENCES trips (trip_id),
                        FOREIGN KEY (from_user) REFERENCES users (user_id),
                        FOREIGN KEY (to_user) REFERENCES users (user_id)
                    )
                ''')
                
                # Настройки
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_by INTEGER
                    )
                ''')
                
                # Логи действий
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        action TEXT,
                        details TEXT,
                        ip TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Индексы для скорости
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_trips_status ON trips(status)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_trips_passenger ON trips(passenger_id)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_trips_driver ON trips(driver_id)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_drivers_verified ON drivers(verified)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_drivers_online ON drivers(online_status)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)')
                
                # Настройки по умолчанию
                for key, value in DEFAULT_SETTINGS.items():
                    cursor.execute(
                        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                        (key, value)
                    )
                
                logger.info("Database initialized successfully")
                
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise
    
    def execute(self, query, params=(), fetch_one=False, fetch_all=False):
        """Выполнение запроса с автоматическим закрытием соединения"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                
                if fetch_one:
                    return cursor.fetchone()
                elif fetch_all:
                    return cursor.fetchall()
                else:
                    return cursor.lastrowid
        except Exception as e:
            logger.error(f"Query failed: {query[:100]}... Error: {e}")
            return None if fetch_one or fetch_all else -1
    
    def get_user(self, user_id):
        """Получить пользователя по ID"""
        return self.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,),
            fetch_one=True
        )
    
    def get_driver(self, user_id):
        """Получить водителя по ID"""
        return self.execute(
            "SELECT * FROM drivers WHERE user_id = ?",
            (user_id,),
            fetch_one=True
        )
    
    def get_active_trip_for_user(self, user_id):
        """Получить активную поездку пользователя"""
        return self.execute(
            """SELECT * FROM trips 
               WHERE (passenger_id = ? OR driver_id = ?) 
               AND status IN ('accepted', 'started')
               ORDER BY created_at DESC LIMIT 1""",
            (user_id, user_id),
            fetch_one=True
        )
    
    def get_setting(self, key):
        """Получить настройку"""
        result = self.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
            fetch_one=True
        )
        return result['value'] if result else None
    
    def update_setting(self, key, value, updated_by):
        """Обновить настройку"""
        self.execute(
            "UPDATE settings SET value = ?, updated_at = ?, updated_by = ? WHERE key = ?",
            (value, datetime.now().isoformat(), updated_by, key)
        )
    
    def log_action(self, user_id, action, details=None):
        """Запись действия в лог"""
        self.execute(
            "INSERT INTO logs (user_id, action, details, created_at) VALUES (?, ?, ?, ?)",
            (user_id, action, details, datetime.now().isoformat())
        )

# Глобальный экземпляр БД
db = Database()