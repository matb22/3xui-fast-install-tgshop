import sqlite3
import os

DB_PATH = "data/bot_database.db"
def init_db():
    # Создаем папку data, если её ещё нет
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            username TEXT,
            client_uuid TEXT,
            client_email TEXT,
            expiry_time INTEGER,
            notified_expired INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def add_or_update_user(tg_id, username, client_uuid, client_email, expiry_time):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (tg_id, username, client_uuid, client_email, expiry_time, notified_expired)
        VALUES (?, ?, ?, ?, ?, 0)
        ON CONFLICT(tg_id) DO UPDATE SET
            username=excluded.username,
            client_uuid=excluded.client_uuid,
            client_email=excluded.client_email,
            expiry_time=excluded.expiry_time,
            notified_expired=0
    """, (tg_id, username, client_uuid, client_email, expiry_time))
    conn.commit()
    conn.close()

def get_user(tg_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT client_uuid, client_email, expiry_time FROM users WHERE tg_id = ?", (tg_id,))
    row = cursor.fetchone()
    conn.close()
    return row

def get_stats():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(tg_id) FROM users")
    total = cursor.fetchone()[0]
    import time
    now = int(time.time() * 1000)
    cursor.execute("SELECT COUNT(tg_id) FROM users WHERE expiry_time > ?", (now,))
    active = cursor.fetchone()[0]
    conn.close()
    return total, active

def get_users_to_notify(one_day_ms):
    import time
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = int(time.time() * 1000)
    target_time = now + one_day_ms
    # Ищем тех, у кого подписка кончается менее чем через сутки, и мы их еще не предупреждали
    cursor.execute("""
        SELECT tg_id, client_email, expiry_time FROM users 
        WHERE expiry_time > ? AND expiry_time <= ? AND notified_expired = 0
    """, (now, target_time))
    rows = cursor.fetchall()
    conn.close()
    return rows

def set_notified(tg_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET notified_expired = 1 WHERE tg_id = ?", (tg_id,))
    conn.commit()
    conn.close()
