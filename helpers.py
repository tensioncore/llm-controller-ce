import os
import sqlite3
import uuid
import time
import math
import re
import secrets
import string
from app_settings import MAX_PASSWORD_BYTES, get_password_policy

def get_password_settings():
    return {
        "password_policy": get_password_policy()
    }

def generate_temp_password(settings):
    policy = settings["password_policy"]
    length = int(policy.get("min_length", 8))
    if not 6 <= length <= MAX_PASSWORD_BYTES:
        raise ValueError(f"Set the password policy minimum to 6–{MAX_PASSWORD_BYTES} characters before creating users.")
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
    c.execute('''
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            instructions TEXT NULL,
            created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)),
            updated_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER))
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_projects_user_updated ON projects(user_id, updated_at DESC, id DESC)')
    c.execute('''
        CREATE TABLE IF NOT EXISTS chat_sessions (
            user_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            project_id INTEGER NULL,
            PRIMARY KEY (user_id, session_id)
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_project ON chat_sessions(user_id, project_id)')
    c.execute('''
        INSERT OR IGNORE INTO chat_sessions (user_id, session_id, project_id)
        SELECT DISTINCT user_id, session_id, NULL
         FROM chats
         WHERE user_id IS NOT NULL
           AND user_id > 0
           AND session_id IS NOT NULL
           AND TRIM(session_id) <> ''
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS request_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            user_id INTEGER DEFAULT NULL,
            source TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            requested_model TEXT DEFAULT NULL,
            active_model TEXT DEFAULT NULL,
            status TEXT NOT NULL,
            http_status INTEGER DEFAULT NULL,
            duration REAL DEFAULT NULL,
            streaming INTEGER NOT NULL DEFAULT 0,
            prompt_tokens INTEGER DEFAULT NULL,
            completion_tokens INTEGER DEFAULT NULL,
            total_tokens INTEGER DEFAULT NULL
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_request_events_time ON request_events(timestamp DESC, id DESC)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_request_events_source_time ON request_events(source, timestamp DESC, id DESC)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_request_events_user_time ON request_events(user_id, timestamp DESC, id DESC)')
    conn.commit()
    conn.close()


def _request_event_text(value, max_length=255):
    if value is None:
        return None
    cleaned = "".join(ch for ch in str(value).strip() if ch >= " " and ch != "\x7f")
    return cleaned[:max_length] or None


def _request_event_integer(value, minimum=None):
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if minimum is not None and parsed < minimum:
        return None
    return parsed


def _request_event_float(value, minimum=None):
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    if minimum is not None and parsed < minimum:
        return None
    return parsed


def record_request_event(
    *,
    source,
    endpoint,
    status,
    user_id=None,
    requested_model=None,
    active_model=None,
    http_status=None,
    duration=None,
    streaming=False,
    prompt_tokens=None,
    completion_tokens=None,
    total_tokens=None,
    timestamp=None,
):
    """Best-effort persistence for request metadata only.

    Callers intentionally cannot supply prompt, response, image, credential, or
    authorization content through this contract.
    """
    source_value = _request_event_text(source, 32)
    endpoint_value = _request_event_text(endpoint, 255)
    status_value = _request_event_text(status, 64)
    if not source_value or not endpoint_value or not status_value:
        return False

    timestamp_value = _request_event_integer(timestamp)
    if timestamp_value is None:
        timestamp_value = int(time.time())

    values = (
        timestamp_value,
        _request_event_integer(user_id, minimum=1),
        source_value,
        endpoint_value,
        _request_event_text(requested_model, 255),
        _request_event_text(active_model, 255),
        status_value,
        _request_event_integer(http_status, minimum=100),
        _request_event_float(duration, minimum=0),
        1 if streaming else 0,
        _request_event_integer(prompt_tokens, minimum=0),
        _request_event_integer(completion_tokens, minimum=0),
        _request_event_integer(total_tokens, minimum=0),
    )

    try:
        with sqlite3.connect(DB_PATH, timeout=2) as conn:
            conn.execute(
                """
                INSERT INTO request_events (
                    timestamp, user_id, source, endpoint,
                    requested_model, active_model, status, http_status,
                    duration, streaming, prompt_tokens, completion_tokens,
                    total_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
        return True
    except Exception:
        return False

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
