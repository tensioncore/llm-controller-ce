# helpers.py
import os
import sqlite3
import requests
import uuid
import time
import datetime
import re
import threading
import secrets
from flask_socketio import SocketIO
import string
from app_settings import get_password_policy

def get_password_settings():
    return {
        "password_policy": get_password_policy()
    }

def generate_temp_password(settings):
    policy = settings["password_policy"]
    length = policy.get("min_length", 8)
    chars = ""
    required = []

    if policy.get("require_upper", False):
        chars += string.ascii_uppercase
        required.append(secrets.choice(string.ascii_uppercase))
    if policy.get("require_lower", False):
        chars += string.ascii_lowercase
        required.append(secrets.choice(string.ascii_lowercase))
    if policy.get("require_digit", False):
        chars += string.digits
        required.append(secrets.choice(string.digits))
    if policy.get("require_special", False):
        specials = "!@#$%^&*()-_+="
        chars += specials
        required.append(secrets.choice(specials))
    if not chars:
        chars = string.ascii_letters + string.digits

    # Fill up the rest
    password = required
    while len(password) < length:
        password.append(secrets.choice(chars))
    for i in range(len(password) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        password[i], password[j] = password[j], password[i]
    return ''.join(password)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "chats.sqlite")
SPLIT_GGUF_PATTERN = re.compile(r"^(.*)-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)

CHAT_REQUIRED_COLUMNS = {
    "user_id": "INTEGER",
    "session_id": "TEXT",
    "session_name": "TEXT",
    "timestamp": "INTEGER",
    "user_message": "TEXT",
    "bot_response": "TEXT",
    "thoughts": "TEXT",
    "tps": "REAL",
    "response_time": "REAL",
    "total_tokens": "INTEGER",
    "prompt_eval_tps": "REAL",
    "model_used": "TEXT",
    "prompt_id": "TEXT DEFAULT NULL",
    "turn_id": "TEXT",
    "prompt_version": "INTEGER DEFAULT 1",
    "response_version": "INTEGER DEFAULT 1",
    "active_in_chat": "INTEGER DEFAULT 1",
    "source_row_id": "INTEGER DEFAULT NULL",
    "attachment_context": "TEXT DEFAULT NULL",
}

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,                  -- NEW: chat ownership scope
            session_id TEXT,
            session_name TEXT,
            timestamp INTEGER,
            user_message TEXT,
            bot_response TEXT,
            thoughts TEXT,
            tps REAL,
            response_time REAL,
            total_tokens INTEGER,
            prompt_eval_tps REAL,
            model_used TEXT,
            prompt_id TEXT DEFAULT NULL,
            turn_id TEXT,
            prompt_version INTEGER DEFAULT 1,
            response_version INTEGER DEFAULT 1,
            active_in_chat INTEGER DEFAULT 1,
            source_row_id INTEGER DEFAULT NULL,
            attachment_context TEXT DEFAULT NULL
        )
    ''')
    c.execute("PRAGMA table_info(chats)")
    existing_columns = {row[1] for row in c.fetchall()}
    for column_name, column_definition in CHAT_REQUIRED_COLUMNS.items():
        if column_name not in existing_columns:
            c.execute(f"ALTER TABLE chats ADD COLUMN {column_name} {column_definition}")

    c.execute("UPDATE chats SET prompt_version=1 WHERE prompt_version IS NULL")
    c.execute("UPDATE chats SET response_version=1 WHERE response_version IS NULL")
    c.execute("UPDATE chats SET active_in_chat=1 WHERE active_in_chat IS NULL")

    c.execute("""
        SELECT id
          FROM chats
         WHERE (user_message IS NOT NULL OR bot_response IS NOT NULL)
           AND (prompt_id IS NULL OR TRIM(prompt_id)='')
    """)
    for (row_id,) in c.fetchall():
        c.execute("UPDATE chats SET prompt_id=? WHERE id=?", (str(uuid.uuid4()), row_id))

    c.execute("""
        SELECT id
          FROM chats
         WHERE (user_message IS NOT NULL OR bot_response IS NOT NULL)
           AND (turn_id IS NULL OR TRIM(turn_id)='')
    """)
    for (row_id,) in c.fetchall():
        c.execute("UPDATE chats SET turn_id=? WHERE id=?", (str(uuid.uuid4()), row_id))

    # Helpful for performance once you have multiple users
    c.execute('CREATE INDEX IF NOT EXISTS idx_chats_user_session_time ON chats(user_id, session_id, timestamp)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_chats_user_session_active ON chats(user_id, session_id, active_in_chat, id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_chats_turn_variant ON chats(user_id, session_id, turn_id, prompt_version, response_version)')
    conn.commit()
    conn.close()

def find_models(base_folder):
    models = []
    if not base_folder or not os.path.isdir(base_folder):
        return models

    split_groups = {}

    for root, dirs, files in os.walk(base_folder):
        for filename in files:
            if not filename.lower().endswith('.gguf'):
                continue

            rel = os.path.relpath(os.path.join(root, filename), base_folder)
            rel = rel.replace("/", "\\")
            match = SPLIT_GGUF_PATTERN.match(filename)

            if match:
                base_name = match.group(1)
                shard_index = int(match.group(2))
                shard_count = int(match.group(3))
                group_key = (os.path.normcase(os.path.normpath(root)), base_name.lower(), shard_count)
                group = split_groups.setdefault(group_key, {
                    "base_name": base_name,
                    "shard_count": shard_count,
                    "parts": {},
                })
                group["parts"][shard_index] = rel
                continue

            models.append({
                "name": os.path.splitext(filename)[0],
                "value": rel,
            })

    for group in split_groups.values():
        shard_count = group["shard_count"]
        parts = group["parts"]
        if 1 not in parts or len(parts) != shard_count or any(index not in parts for index in range(1, shard_count + 1)):
            continue

        models.append({
            "name": group["base_name"],
            "value": parts[1],
        })

    models.sort(key=lambda model: (str(model.get("name") or "").lower(), str(model.get("value") or "").lower()))
    return models

