from flask import Blueprint, request, jsonify, session, g
from forms import RenameSessionForm
import sqlite3
import requests
import time
import datetime
import threading
import json
import uuid
import os
import re
import model_routes
from extensions import socketio
from helpers import DB_PATH
from app_settings import get_setting
from auth import DEFAULT_SESSION_VERSION, _coerce_session_version, login_required, validate_session_user
from db_mysql import mysql_conn
from flask_socketio import disconnect, join_room, leave_room
from werkzeug.datastructures import MultiDict

chat_routes = Blueprint('chat_routes', __name__)
SOCKET_NAMESPACE = "/"
SOCKET_PRUNE_VALIDATION_INTERVAL_SECONDS = 5.0
MAX_NEW_USER_MESSAGE_CHARS = 20000
MAX_CONTEXT_ACTIVE_TURNS = 40
MAX_MODEL_CONTEXT_CHARS = 200000
MESSAGE_TOO_LARGE_ERROR = "This message is too large. Please shorten it or attach it as a file."
MODEL_CONTEXT_TRUNCATION_NOTICE = "[Attachment context truncated: additional file content was omitted to keep this message within the safe model context limit.]"
_socket_clients = {}
_socket_clients_lock = threading.RLock()

def _socket_auth_error_payload():
    return {
        "status": "error",
        "code": "auth_required",
        "message": "Your session has expired. Reload the page and sign in again.",
        "reload_required": True,
    }

def _close_socket_auth_db():
    db = g.pop('db', None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass

def _current_socket_sid():
    try:
        return getattr(request, "sid", None)
    except Exception:
        return None

def _copy_socket_state(state):
    if not state:
        return None
    return {
        "sid": state.get("sid"),
        "user_id": state.get("user_id"),
        "session_version": state.get("session_version"),
        "rooms": set(state.get("rooms") or set()),
        "last_validated_at": state.get("last_validated_at"),
        "auth_invalidated": bool(state.get("auth_invalidated")),
    }

def _register_current_socket(user):
    sid = _current_socket_sid()
    if not sid or not user:
        return

    user_id = int(user["id"])
    user_room = f"user_{user_id}"
    session_version = _coerce_session_version(
        user.get("session_version"),
        DEFAULT_SESSION_VERSION,
    )

    with _socket_clients_lock:
        _socket_clients[sid] = {
            "sid": sid,
            "user_id": user_id,
            "session_version": session_version,
            "rooms": {user_room},
            "last_validated_at": time.monotonic(),
            "auth_invalidated": False,
        }

def _mark_current_socket_validated(user):
    sid = _current_socket_sid()
    if not sid or not user:
        return

    session_version = _coerce_session_version(
        user.get("session_version"),
        DEFAULT_SESSION_VERSION,
    )
    with _socket_clients_lock:
        state = _socket_clients.get(sid)
        if state is not None:
            state["user_id"] = int(user["id"])
            state["session_version"] = session_version
            state["last_validated_at"] = time.monotonic()
            state["auth_invalidated"] = False

def _mark_socket_validated(sid):
    if not sid:
        return
    with _socket_clients_lock:
        state = _socket_clients.get(sid)
        if state is not None:
            state["last_validated_at"] = time.monotonic()
            state["auth_invalidated"] = False

def _track_current_socket_room(room):
    sid = _current_socket_sid()
    if not sid or not room:
        return
    with _socket_clients_lock:
        state = _socket_clients.get(sid)
        if state is not None:
            state.setdefault("rooms", set()).add(room)

def _untrack_current_socket_room(room):
    sid = _current_socket_sid()
    if not sid or not room:
        return
    with _socket_clients_lock:
        state = _socket_clients.get(sid)
        if state is not None:
            state.setdefault("rooms", set()).discard(room)

def _pop_socket_state(sid):
    if not sid:
        return None
    with _socket_clients_lock:
        state = _copy_socket_state(_socket_clients.pop(sid, None))
    if state is not None:
        state["sid"] = sid
    return state

def _drop_current_socket_rooms():
    state = _pop_socket_state(_current_socket_sid())
    if not state:
        return

    _stop_active_generation(
        state.get("user_id"),
        socket_sid=state.get("sid"),
        auth_invalidated=True,
    )

    for room in state["rooms"]:
        try:
            leave_room(room)
        except Exception:
            pass

def _disconnect_registered_socket(sid, state=None, stop_generation=True):
    state = _copy_socket_state(state) or _pop_socket_state(sid)
    if not sid:
        return

    with _socket_clients_lock:
        _socket_clients.pop(sid, None)

    if stop_generation and state:
        _stop_active_generation(
            state.get("user_id"),
            socket_sid=sid,
            auth_invalidated=True,
        )

    try:
        socketio.server.disconnect(sid, namespace=SOCKET_NAMESPACE)
    except Exception:
        pass

    for room in (state or {}).get("rooms", set()):
        try:
            socketio.server.leave_room(sid, room, namespace=SOCKET_NAMESPACE)
        except Exception:
            pass

def _socket_state_is_valid(state):
    if not state:
        return False

    user_id = state.get("user_id")
    session_version = _coerce_session_version(state.get("session_version"))
    if not user_id or session_version is None:
        return False

    db = None
    cursor = None
    try:
        db = mysql_conn()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT is_active, must_change_pw, email_confirmed_at, session_version
            FROM llm_users
            WHERE id=%s
            LIMIT 1
            """,
            (user_id,),
        )
        user = cursor.fetchone()
        if (
            not user
            or int(user.get("is_active") or 0) != 1
            or user.get("email_confirmed_at") is None
            or bool(user.get("must_change_pw"))
        ):
            return False

        db_session_version = _coerce_session_version(
            user.get("session_version"),
            DEFAULT_SESSION_VERSION,
        )
        return db_session_version == session_version
    except Exception as e:
        print("Socket session validation failed:", e)
        return False
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if db is not None:
            try:
                db.close()
            except Exception:
                pass

def _require_socket_user(disconnect_on_failure=True):
    try:
        user = validate_session_user(clear_on_failure=True)
    finally:
        _close_socket_auth_db()
    if not user or session.get("must_change_pw"):
        _drop_current_socket_rooms()
        if disconnect_on_failure:
            try:
                disconnect()
            except Exception:
                pass
        return None
    _mark_current_socket_validated(user)
    return user

# --------------------------------------------------------------------------- #
#  helper: auto-title                                                         #
# --------------------------------------------------------------------------- #

def _clean_title(raw: str) -> str:
    """
    Keep the thinking model, but ignore <think>...</think> entirely.
    Prefer the text AFTER the last </think>. If none, strip think tags if present.
    """
    if raw is None:
        return ""

    s = str(raw).strip()
    low = s.lower()

    # If it starts with <think> and never closes, it's think-only -> no usable title
    if low.startswith("<think>") and "</think>" not in low:
        return ""

    # If we have a proper </think>, keep ONLY what's after the LAST closing tag
    if "</think>" in low:
        idx = low.rfind("</think>")
        s = s[idx + len("</think>"):].strip()

    # If it starts with <think> but never closes, strip that "thinking blob"
    low = s.lower()
    if low.startswith("<think>"):
        s2 = re.sub(r"^<think>\s*", "", s, flags=re.IGNORECASE).strip()
        m = re.search(r"\n\s*\n", s2)
        if m:
            s = s2[m.end():].strip()
        else:
            s = s2.split("\n", 1)[-1].strip() if "\n" in s2 else ""

    # Remove any remaining embedded think blocks (just in case)
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.IGNORECASE | re.DOTALL).strip()

    # Remove common prefixes
    s = re.sub(r"^(title\s*:\s*)", "", s, flags=re.IGNORECASE).strip()

    # Strip surrounding quotes/backticks
    s = s.strip(" \t\r\n\"'`“”‘’")

    # Keep only first line
    if "\n" in s:
        s = s.split("\n", 1)[0].strip()

    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()

    # Remove trailing punctuation
    s = re.sub(r"[.!?;:,]+$", "", s).strip()

    # Enforce sidebar length
    if len(s) > 50:
        s = s[:47].rstrip() + "..."

    return s


def _has_default_timestamp_title(title: str) -> bool:
    if not title:
        return False
    return re.match(r"^\d{2}:\d{2}:\d{2} \d{2}-\d{2}-\d{4}$", title.strip()) is not None


def auto_generate_chat_title(user_id: int, session_id: str):
    """
    Classic behavior (like your old GPT-4 era version):
    - Generate title after the first assistant response is saved.
    - Use title model first, fallback to the main model port.
    - Use a simple proven prompt.
    """
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # First assistant response (scoped to user)
        c.execute("""
            SELECT bot_response
              FROM chats
             WHERE user_id=?
               AND session_id=?
               AND bot_response IS NOT NULL
          ORDER BY id ASC
             LIMIT 1
        """, (user_id, session_id))
        row = c.fetchone()
        if not row:
            return

        first_response = (row[0] or "").strip()
        if not first_response:
            return

        # If already titled (not default), skip (scoped to user)
        c.execute("SELECT MIN(session_name) FROM chats WHERE user_id=? AND session_id=?", (user_id, session_id))
        cur_row = c.fetchone()
        current_name = (cur_row[0] or "").strip() if cur_row else ""

        if not _has_default_timestamp_title(current_name):
            return

        title_prompt = (
            "Generate a short, concise title for this chat interaction. "
            "Do not provide any labels or punctuation like quote marks, "
            "just respond with the title:\n\n"
            + first_response[:800]
        )

        def call_title(port, retries=6, delay=0.5, timeout=30):
            last_err = None
            for _ in range(retries):
                try:
                    r = requests.post(
                        f"http://127.0.0.1:{port}/v1/chat/completions",
                        json={
                            "model": "llama",
                            "messages": [{"role": "user", "content": title_prompt}],
                            "stream": False,
                            "max_tokens": 250,
                            "temperature": 0.4,
                            "top_p": 0.9
                        },
                        timeout=timeout
                    )
                    if r.status_code == 503:
                        time.sleep(delay)
                        continue
                    r.raise_for_status()

                    j = r.json()
                    choice = (j.get("choices") or [{}])[0] or {}

                    raw = ""
                    msg = choice.get("message")
                    if isinstance(msg, dict):
                        raw = msg.get("content") or ""

                    # fallback: some servers return "text" instead of chat message
                    if not raw:
                        raw = choice.get("text") or ""

                    # last fallback: sometimes the whole response has "content"/"response"
                    if not raw:
                        raw = j.get("content") or j.get("response") or ""

                    title = _clean_title(raw)
                    if title:
                        return title

                    last_err = "empty title after cleaning"
                except Exception as e:
                    last_err = str(e)
                    time.sleep(delay)
            return None

        title = call_title(model_routes.get_llama_title_port()) or call_title(
            model_routes.get_llama_main_port(),
            retries=2,
            delay=0.3,
            timeout=30,
        )

        if not title:
            print(f"[WARN] auto-title produced no title for user_id={user_id} session_id={session_id}")
            return

        # Update all rows so MIN(session_name) resolves to the title (scoped to user)
        c.execute("UPDATE chats SET session_name=? WHERE user_id=? AND session_id=?", (title, user_id, session_id))
        conn.commit()

        # emit ONLY to this user
        prune_stale_socket_rooms(user_id)
        socketio.emit(
            "update_session_name",
            {"session_id": session_id, "session_name": title},
            room=f"user_{user_id}"
        )
        print(f"[INFO] auto-title set: user_id={user_id} {session_id} -> {title}")

    except Exception as e:
        print(f"[ERROR] auto_generate_chat_title failed for user_id={user_id} session_id={session_id}: {e}")
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


active_generations = {}
active_generations_lock = threading.Lock()

SUPPORTED_ATTACHMENT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".html", ".css", ".json", ".xml",
    ".yaml", ".yml", ".csv", ".log", ".ini", ".cfg", ".bat", ".ps1", ".sh",
    ".sql", ".php", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs",
}
MAX_ATTACHMENTS = 8
MAX_ATTACHMENT_FILE_BYTES = 1048576
MAX_ATTACHMENT_TOTAL_BYTES = 4194304
ATTACHMENT_CHUNK_MAX_LINES = 200
ATTACHMENT_CHUNK_OVERLAP_LINES = 20
ATTACHMENT_CONTEXT_CHAR_LIMIT = 120000


def _get_attachment_setting_int(key, default):
    try:
        value = get_setting(key, default=default, cast=int)
        value = int(value)
        if value < 0:
            raise ValueError()
        return value
    except Exception:
        return int(default)


def _refresh_attachment_limits():
    global MAX_ATTACHMENTS
    global MAX_ATTACHMENT_FILE_BYTES
    global MAX_ATTACHMENT_TOTAL_BYTES
    global ATTACHMENT_CONTEXT_CHAR_LIMIT
    global ATTACHMENT_CHUNK_MAX_LINES
    global ATTACHMENT_CHUNK_OVERLAP_LINES

    MAX_ATTACHMENTS = max(1, _get_attachment_setting_int("llm.attachments.max_files", 8))
    MAX_ATTACHMENT_FILE_BYTES = max(1, _get_attachment_setting_int("llm.attachments.max_file_bytes", 1048576))
    MAX_ATTACHMENT_TOTAL_BYTES = max(1, _get_attachment_setting_int("llm.attachments.max_total_bytes", 4194304))
    ATTACHMENT_CONTEXT_CHAR_LIMIT = max(1, _get_attachment_setting_int("llm.attachments.max_context_chars", 120000))
    ATTACHMENT_CHUNK_MAX_LINES = max(1, _get_attachment_setting_int("llm.attachments.chunk_max_lines", 200))
    ATTACHMENT_CHUNK_OVERLAP_LINES = max(0, _get_attachment_setting_int("llm.attachments.chunk_overlap_lines", 20))


_refresh_attachment_limits()


def _chat_stream_room(user_room, session_id=None):
    session_key = (session_id or "").strip()
    if not session_key:
        return user_room
    return f"{user_room}:session:{session_key}"


def _user_id_from_room(user_room):
    try:
        value = str(user_room or "")
        if not value.startswith("user_"):
            return None
        return int(value.split("_", 1)[1])
    except Exception:
        return None


def prune_stale_socket_rooms(user_id, session_id=None, force=False):
    try:
        user_id = int(user_id)
    except Exception:
        return

    user_room = f"user_{user_id}"
    target_rooms = {user_room}
    if session_id:
        target_rooms.add(_chat_stream_room(user_room, session_id))

    candidates = []
    with _socket_clients_lock:
        for sid, state in _socket_clients.items():
            if int(state.get("user_id") or 0) != user_id:
                continue
            rooms = set(state.get("rooms") or set())
            if rooms.intersection(target_rooms):
                candidates.append((sid, _copy_socket_state(state)))

    now = time.monotonic()
    for sid, state in candidates:
        if state.get("auth_invalidated"):
            _disconnect_registered_socket(sid, state)
            continue

        last_validated_at = state.get("last_validated_at") or 0
        if (
            not force
            and last_validated_at
            and now - float(last_validated_at) < SOCKET_PRUNE_VALIDATION_INTERVAL_SECONDS
        ):
            continue

        if _socket_state_is_valid(state):
            _mark_socket_validated(sid)
        else:
            _disconnect_registered_socket(sid, state)


def _emit_chat_error(user_room, action, message, message_id=None, reset_state=False, session_id=None):
    user_id = _user_id_from_room(user_room)
    if user_id is not None:
        prune_stale_socket_rooms(user_id, session_id=session_id)

    payload = {
        "action": action,
        "message": message,
        "reset_state": reset_state,
    }
    if message_id:
        payload["message_id"] = message_id
    if session_id:
        payload["session_id"] = session_id
    socketio.emit("chat_error", payload, room=_chat_stream_room(user_room, session_id))


def _is_benchmark_running():
    try:
        import benchmark_routes as benchmark_module

        bench_lock = getattr(benchmark_module, "_bench_lock", None)
        bench_state = getattr(benchmark_module, "_bench_state", None)
        if bench_lock is None or not isinstance(bench_state, dict):
            return False

        with bench_lock:
            return bool(bench_state.get("running"))
    except Exception:
        return False


def _close_generation_response(response):
    if not response:
        return
    try:
        response.close()
    except Exception:
        pass
    raw = getattr(response, "raw", None)
    if raw is not None:
        try:
            raw.close()
        except Exception:
            pass


def _claim_active_generation(user_id, session_id, message_id, socket_sid=None, session_version=None):
    state = {
        "user_id": user_id,
        "session_id": session_id,
        "message_id": message_id,
        "socket_sid": socket_sid,
        "session_version": session_version,
        "stop_event": threading.Event(),
        "stop_requested": False,
        "auth_invalidated": False,
        "response": None,
    }
    with active_generations_lock:
        if active_generations.get(user_id):
            return None
        active_generations[user_id] = state
    return state


def _clear_active_generation(user_id, message_id=None):
    with active_generations_lock:
        current = active_generations.get(user_id)
        if not current:
            return
        if message_id and current.get("message_id") != message_id:
            return
        active_generations.pop(user_id, None)


def _stop_active_generation(
    user_id,
    message_id=None,
    session_id=None,
    socket_sid=None,
    auth_invalidated=False,
):
    if not user_id:
        return None

    with active_generations_lock:
        current = active_generations.get(user_id)
        if not current:
            return None
        if message_id and current.get("message_id") != message_id:
            return None
        if session_id and current.get("session_id") and current.get("session_id") != session_id:
            return None
        if socket_sid and current.get("socket_sid") and current.get("socket_sid") != socket_sid:
            return None
        current["stop_requested"] = True
        if auth_invalidated:
            current["auth_invalidated"] = True
        current["stop_event"].set()
        response = current.get("response")

    _close_generation_response(response)
    return current


def _current_generation_socket_state():
    sid = _current_socket_sid()
    if not sid:
        return None
    with _socket_clients_lock:
        return _copy_socket_state(_socket_clients.get(sid))


def _generation_auth_still_valid(state):
    if not state or state.get("auth_invalidated"):
        return False

    socket_state = {
        "sid": state.get("socket_sid"),
        "user_id": state.get("user_id"),
        "session_version": state.get("session_version"),
    }
    if _socket_state_is_valid(socket_state):
        _mark_socket_validated(state.get("socket_sid"))
        return True

    state["auth_invalidated"] = True
    state["stop_requested"] = True
    state["stop_event"].set()
    _close_generation_response(state.get("response"))
    if state.get("socket_sid"):
        _disconnect_registered_socket(
            state.get("socket_sid"),
            {
                "user_id": state.get("user_id"),
                "session_version": state.get("session_version"),
                "rooms": {f"user_{state.get('user_id')}", _chat_stream_room(f"user_{state.get('user_id')}", state.get("session_id"))},
                "auth_invalidated": True,
            },
            stop_generation=False,
        )
    return False


def _history_rows_to_messages(history_rows):
    messages = []
    for row in history_rows:
        if row[0]:
            attachment_context = row[2] if len(row) > 2 else None
            messages.append({"role": "user", "content": _compose_user_message(row[0], attachment_context)})
        if row[1]:
            messages.append({"role": "assistant", "content": row[1]})
    return messages


def _compose_user_message(user_message, attachment_context=None):
    base_message = (user_message or "").strip()
    if not attachment_context:
        return base_message

    attachment_block = (
        "Attached file context follows. Use it only when relevant to the user's request.\n\n"
        f"{attachment_context}"
    ).strip()
    return "\n\n".join(part for part in (base_message, attachment_block) if part)


def _message_content_chars(messages):
    return sum(len(str((message or {}).get("content") or "")) for message in (messages or []))


def _compose_current_model_message(user_message, attachment_context=None):
    composed = _compose_user_message(user_message, attachment_context)
    if len(composed) <= MAX_MODEL_CONTEXT_CHARS:
        return composed

    if attachment_context:
        base_message = _compose_user_message(user_message)
        wrapper_prefix = "Attached file context follows. Use it only when relevant to the user's request.\n\n"
        reserved = len(base_message) + len(wrapper_prefix) + len(MODEL_CONTEXT_TRUNCATION_NOTICE) + 4
        available_attachment_chars = MAX_MODEL_CONTEXT_CHARS - reserved
        if available_attachment_chars > 0:
            trimmed_attachment_context = (
                str(attachment_context)[:available_attachment_chars].rstrip()
                + "\n\n"
                + MODEL_CONTEXT_TRUNCATION_NOTICE
            )
            return _compose_user_message(user_message, trimmed_attachment_context)

    return composed[:MAX_MODEL_CONTEXT_CHARS]


def _build_limited_model_context(history_rows, user_message, attachment_context=None):
    prior_rows = list(history_rows or [])[-max(0, MAX_CONTEXT_ACTIVE_TURNS - 1):]
    current_message = {
        "role": "user",
        "content": _compose_current_model_message(user_message, attachment_context),
    }

    while True:
        messages = _history_rows_to_messages(prior_rows)
        messages.append(current_message)
        if _message_content_chars(messages) <= MAX_MODEL_CONTEXT_CHARS or not prior_rows:
            return messages
        prior_rows = prior_rows[1:]


def _attachment_extension(filename):
    return os.path.splitext((filename or "").strip().lower())[1]


def _sanitize_attachment_name(filename):
    safe_name = os.path.basename((filename or "").strip())
    return safe_name[:255]


def _chunk_attachment_text(text):
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines:
        return []

    step = max(1, ATTACHMENT_CHUNK_MAX_LINES - ATTACHMENT_CHUNK_OVERLAP_LINES)
    chunks = []
    start = 0
    total_lines = len(lines)

    while start < total_lines:
        end = min(total_lines, start + ATTACHMENT_CHUNK_MAX_LINES)
        chunk_lines = lines[start:end]
        chunks.append((start + 1, end, "\n".join(chunk_lines).strip("\n")))
        if end >= total_lines:
            break
        start += step

    return chunks


def _build_attachment_context(raw_attachments):
    attachments = raw_attachments or []
    if not attachments:
        return None
    _refresh_attachment_limits()
    if not isinstance(attachments, list):
        raise ValueError("Invalid attachment payload.")
    if len(attachments) > MAX_ATTACHMENTS:
        raise ValueError(f"You can attach up to {MAX_ATTACHMENTS} files per message.")

    total_bytes = 0
    built_sections = []
    truncated = False

    for index, attachment in enumerate(attachments, start=1):
        if not isinstance(attachment, dict):
            raise ValueError("Invalid attachment payload.")

        filename = _sanitize_attachment_name(attachment.get("name") or "")
        if not filename:
            raise ValueError("Each attachment needs a valid filename.")

        extension = _attachment_extension(filename)
        if extension not in SUPPORTED_ATTACHMENT_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {filename}")

        content = attachment.get("content")
        if not isinstance(content, str):
            raise ValueError(f"Could not read {filename} as text.")
        if not content:
            raise ValueError(f"{filename} is empty.")
        if "\x00" in content:
            raise ValueError(f"{filename} looks like a binary file and can't be used here.")

        declared_size = attachment.get("size")
        try:
            file_bytes = int(declared_size) if declared_size is not None else len(content.encode("utf-8"))
        except Exception:
            file_bytes = len(content.encode("utf-8"))

        if file_bytes > MAX_ATTACHMENT_FILE_BYTES:
            raise ValueError(f"{filename} exceeds the per-file limit of {MAX_ATTACHMENT_FILE_BYTES // 1024} KB.")

        total_bytes += file_bytes
        if total_bytes > MAX_ATTACHMENT_TOTAL_BYTES:
            raise ValueError(f"Total attachment size exceeds the {MAX_ATTACHMENT_TOTAL_BYTES // (1024 * 1024)} MB limit.")

        chunks = _chunk_attachment_text(content)
        if not chunks:
            raise ValueError(f"{filename} is empty.")

        total_chunks = len(chunks)
        for chunk_index, (line_start, line_end, chunk_text) in enumerate(chunks, start=1):
            section = (
                f"[Attached file: {filename}]\n"
                f"[File type: {extension or 'text'}]\n"
                f"[Attachment {index}/{len(attachments)}]\n"
                f"[Chunk {chunk_index}/{total_chunks}]\n"
                f"[Lines {line_start}-{line_end}]\n"
                f"{chunk_text}"
            ).strip()

            projected_length = sum(len(part) for part in built_sections) + len(section) + 4
            if projected_length > ATTACHMENT_CONTEXT_CHAR_LIMIT:
                truncated = True
                break

            built_sections.append(section)

        if truncated:
            break

    if not built_sections:
        raise ValueError("Attachment content exceeded the safe context limit.")

    if truncated:
        built_sections.append("[Attachment context truncated: additional file content was omitted to stay within the context limit.]")

    return "\n\n".join(built_sections)


def _extract_attachment_display_names(attachment_context):
    if not attachment_context or not isinstance(attachment_context, str):
        return []

    seen = set()
    names = []
    for match in re.findall(r"^\[Attached file:\s*(.+?)\]$", attachment_context, flags=re.MULTILINE):
        filename = _sanitize_attachment_name(match)
        if not filename or filename in seen:
            continue
        seen.add(filename)
        names.append(filename)
    return names


def _emit_receive_message(user_room, session_id, message_id, **extra):
    user_id = _user_id_from_room(user_room)
    if user_id is not None:
        prune_stale_socket_rooms(user_id, session_id=session_id)

    payload = {
        "session_id": session_id,
        "message_id": message_id,
    }
    payload.update(extra)
    socketio.emit("receive_message", payload, room=_chat_stream_room(user_room, session_id))


def _default_session_name():
    return datetime.datetime.now().strftime("%H:%M:%S %m-%d-%Y")


def _get_active_history_rows(cursor, user_id, session_id, before_id=None):
    query = """
        SELECT user_message, bot_response, attachment_context
          FROM chats
         WHERE user_id=?
           AND session_id=?
           AND active_in_chat=1
           AND turn_id IS NOT NULL
    """
    params = [user_id, session_id]
    if before_id is not None:
        query += " AND id < ?"
        params.append(before_id)
    query += " ORDER BY id ASC"
    cursor.execute(query, params)
    return cursor.fetchall()


def _get_active_turn_row(cursor, user_id, session_id, prompt_id):
    cursor.execute(
        """
        SELECT id, turn_id, prompt_id, user_message, prompt_version, response_version, active_in_chat, attachment_context
          FROM chats
         WHERE user_id=? AND session_id=? AND prompt_id=?
         LIMIT 1
        """,
        (user_id, session_id, prompt_id),
    )
    return cursor.fetchone()


def _get_latest_active_turn_row(cursor, user_id, session_id):
    cursor.execute(
        """
        SELECT id, turn_id, prompt_id, user_message, prompt_version, response_version
          FROM chats
         WHERE user_id=?
           AND session_id=?
           AND active_in_chat=1
           AND turn_id IS NOT NULL
           AND user_message IS NOT NULL
           AND TRIM(user_message) <> ''
      ORDER BY id DESC
         LIMIT 1
        """,
        (user_id, session_id),
    )
    return cursor.fetchone()


def _get_variant_navigation(cursor, user_id, session_id, turn_id, active_prompt_id):
    cursor.execute(
        """
        SELECT prompt_id, prompt_version, response_version
          FROM chats
         WHERE user_id=?
           AND session_id=?
           AND turn_id=?
           AND prompt_id IS NOT NULL
      ORDER BY prompt_version ASC, response_version ASC, id ASC
        """,
        (user_id, session_id, turn_id),
    )
    rows = cursor.fetchall()
    if not rows:
        return None

    prompt_ids = [row[0] for row in rows if row[0]]
    if not prompt_ids:
        return None

    try:
        idx = prompt_ids.index(active_prompt_id)
    except ValueError:
        return None

    return {
        "variant_count": len(prompt_ids),
        "variant_position": idx + 1,
        "prev_variant_prompt_id": prompt_ids[idx - 1] if idx > 0 else None,
        "next_variant_prompt_id": prompt_ids[idx + 1] if idx + 1 < len(prompt_ids) else None,
    }


def _persist_chat_turn(
    user_id,
    session_id,
    user_message,
    final_answer,
    reasoning,
    overall_tps,
    elapsed_time,
    tokens_count,
    prompt_eval_tps,
    message_id,
    replace_prompt_id=None,
    edit_prompt_id=None,
    attachment_context=None,
):
    timestamp = int(time.time())
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    try:
        c.execute("SELECT 1 FROM chats WHERE user_id=? AND session_id=? LIMIT 1", (user_id, session_id))
        if not c.fetchone():
            c.execute(
                """
                INSERT INTO chats
                (user_id, session_id, session_name, timestamp, model_used, active_in_chat)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (user_id, session_id, _default_session_name(), timestamp, model_routes.current_main_model_used),
            )

        if replace_prompt_id:
            c.execute(
                """
                SELECT id, turn_id, prompt_version, attachment_context
                  FROM chats
                 WHERE user_id=? AND session_id=? AND prompt_id=?
                 LIMIT 1
                """,
                (user_id, session_id, replace_prompt_id),
            )
            source_row = c.fetchone()
            if not source_row or not source_row[1]:
                conn.rollback()
                return False, False

            source_row_id, turn_id, prompt_version, stored_attachment_context = source_row
            attachment_to_store = attachment_context if attachment_context is not None else stored_attachment_context
            c.execute(
                """
                SELECT COALESCE(MAX(response_version), 0)
                  FROM chats
                 WHERE user_id=?
                   AND session_id=?
                   AND turn_id=?
                   AND prompt_version=?
                """,
                (user_id, session_id, turn_id, prompt_version),
            )
            next_response_version = (c.fetchone()[0] or 0) + 1

            c.execute(
                """
                UPDATE chats
                   SET active_in_chat=0
                 WHERE user_id=? AND session_id=? AND turn_id=?
                """,
                (user_id, session_id, turn_id),
            )
            c.execute(
                """
                INSERT INTO chats
                (user_id, session_id, timestamp, user_message, bot_response, thoughts,
                 tps, response_time, total_tokens, prompt_eval_tps,
                 model_used, prompt_id, turn_id, prompt_version,
                 response_version, active_in_chat, source_row_id, attachment_context)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    user_id,
                    session_id,
                    timestamp,
                    user_message,
                    final_answer.strip(),
                    reasoning.strip(),
                    overall_tps,
                    round(elapsed_time, 2),
                    tokens_count,
                    prompt_eval_tps,
                    model_routes.current_main_model_used,
                    message_id,
                    turn_id,
                    prompt_version,
                    next_response_version,
                    source_row_id,
                    attachment_to_store,
                ),
            )
            conn.commit()
            return True, False

        if edit_prompt_id:
            c.execute(
                """
                SELECT id, turn_id, attachment_context
                  FROM chats
                 WHERE user_id=? AND session_id=? AND prompt_id=?
                 LIMIT 1
                """,
                (user_id, session_id, edit_prompt_id),
            )
            source_row = c.fetchone()
            if not source_row or not source_row[1]:
                conn.rollback()
                return False, False

            source_row_id, turn_id, stored_attachment_context = source_row
            attachment_to_store = attachment_context if attachment_context is not None else stored_attachment_context
            c.execute(
                """
                SELECT COALESCE(MAX(prompt_version), 0)
                  FROM chats
                 WHERE user_id=?
                   AND session_id=?
                   AND turn_id=?
                """,
                (user_id, session_id, turn_id),
            )
            next_prompt_version = (c.fetchone()[0] or 0) + 1

            c.execute(
                """
                UPDATE chats
                   SET active_in_chat=0
                 WHERE user_id=? AND session_id=? AND turn_id=?
                """,
                (user_id, session_id, turn_id),
            )
            c.execute(
                """
                INSERT INTO chats
                (user_id, session_id, timestamp, user_message, bot_response, thoughts,
                 tps, response_time, total_tokens, prompt_eval_tps,
                 model_used, prompt_id, turn_id, prompt_version,
                 response_version, active_in_chat, source_row_id, attachment_context)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
                """,
                (
                    user_id,
                    session_id,
                    timestamp,
                    user_message,
                    final_answer.strip(),
                    reasoning.strip(),
                    overall_tps,
                    round(elapsed_time, 2),
                    tokens_count,
                    prompt_eval_tps,
                    model_routes.current_main_model_used,
                    message_id,
                    turn_id,
                    next_prompt_version,
                    source_row_id,
                    attachment_to_store,
                ),
            )
            conn.commit()
            return True, False

        c.execute(
            """
            SELECT COUNT(DISTINCT turn_id)
              FROM chats
             WHERE user_id=?
               AND session_id=?
               AND turn_id IS NOT NULL
            """,
            (user_id, session_id),
        )
        prior_turns = c.fetchone()[0] or 0
        turn_id = str(uuid.uuid4())

        c.execute(
            """
            INSERT INTO chats
            (user_id, session_id, timestamp, user_message, bot_response, thoughts,
             tps, response_time, total_tokens, prompt_eval_tps,
             model_used, prompt_id, turn_id, prompt_version,
             response_version, active_in_chat, source_row_id, attachment_context)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 1, NULL, ?)
            """,
            (
                user_id,
                session_id,
                timestamp,
                user_message,
                final_answer.strip(),
                reasoning.strip(),
                overall_tps,
                round(elapsed_time, 2),
                tokens_count,
                prompt_eval_tps,
                model_routes.current_main_model_used,
                message_id,
                turn_id,
                attachment_context,
            ),
        )
        conn.commit()
        return True, prior_turns == 0
    except Exception:
        conn.rollback()
        return False, False
    finally:
        conn.close()


def _stream_chat_reply(
    user_id,
    session_id,
    user_room,
    message_id,
    user_message,
    messages,
    replace_prompt_id=None,
    edit_prompt_id=None,
    attachment_context=None,
):
    action = "edit_prompt" if edit_prompt_id else ("regenerate" if replace_prompt_id else "send")
    stream_event_extra = {"action": action}
    source_prompt_id = (replace_prompt_id or "").strip() or None
    if source_prompt_id:
        stream_event_extra["source_prompt_id"] = source_prompt_id
    if _is_benchmark_running():
        error_message = "Benchmark is currently running. Chat is temporarily unavailable until it finishes."
        _emit_chat_error(
            user_room,
            action,
            error_message,
            message_id=message_id,
            reset_state=True,
            session_id=session_id,
        )
        return

    socket_state = _current_generation_socket_state()
    socket_sid = (socket_state or {}).get("sid") or _current_socket_sid()
    generation_session_version = _coerce_session_version(
        (socket_state or {}).get("session_version"),
        _coerce_session_version(session.get("session_version"), DEFAULT_SESSION_VERSION),
    )
    state = _claim_active_generation(
        user_id,
        session_id,
        message_id,
        socket_sid=socket_sid,
        session_version=generation_session_version,
    )
    if not state:
        _emit_chat_error(
            user_room,
            action,
            "Another response is already in progress.",
            message_id=message_id,
            reset_state=bool(replace_prompt_id or edit_prompt_id),
            session_id=session_id,
        )
        return

    try:
        buffer = ""
        reasoning = ""
        final_answer = ""
        in_thoughts = False

        tokens_count = 0
        prompt_tokens_count = 0
        PROMPT_TOKEN_LIMIT = 15

        prompt_start_time = None
        prompt_end_time = None
        start_time = time.time()
        stream_error = None
        response = None

        payload = {"model": "llama", "messages": messages, "stream": True}

        try:
            response = requests.post(
                model_routes.get_llama_main_completion_url(),
                json=payload,
                stream=True,
                timeout=600,
            )
            state["response"] = response
            if state["stop_event"].is_set():
                state["stop_requested"] = True
                _close_generation_response(response)
            elif response.status_code == 503:
                error_message = "Model unavailable (503). Please load a model first."
                if replace_prompt_id or edit_prompt_id:
                    _emit_chat_error(
                        user_room,
                        action,
                        error_message,
                        message_id=message_id,
                        reset_state=True,
                        session_id=session_id,
                    )
                else:
                    _emit_receive_message(
                        user_room,
                        session_id,
                        message_id,
                        **stream_event_extra,
                        bot_response=f"Error: {error_message}",
                        thoughts="",
                        streaming_done=True,
                        tps=0,
                        response_time=0,
                        total_tokens=0,
                        prompt_eval_tps=0,
                    )
                return
            else:
                response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response else "??"
            error_message = (
                "Model unavailable (503). Please load a model first."
                if code == 503 else
                f"HTTP error {code}"
            )
            if replace_prompt_id or edit_prompt_id:
                _emit_chat_error(
                    user_room,
                    action,
                    error_message,
                    message_id=message_id,
                    reset_state=True,
                    session_id=session_id,
                )
            else:
                _emit_receive_message(
                    user_room,
                    session_id,
                    message_id,
                    **stream_event_extra,
                    bot_response=f"Error: {error_message}",
                    thoughts="",
                    streaming_done=True,
                    tps=0,
                    response_time=0,
                    total_tokens=0,
                    prompt_eval_tps=0,
                )
            return
        except Exception as e:
            error_message = str(e)
            if "Failed to establish a new connection" in error_message or "Max retries exceeded" in error_message:
                error_message = "No model loaded. Please load a model before chatting."

            if replace_prompt_id or edit_prompt_id:
                _emit_chat_error(
                    user_room,
                    action,
                    error_message,
                    message_id=message_id,
                    reset_state=True,
                    session_id=session_id,
                )
            else:
                _emit_receive_message(
                    user_room,
                    session_id,
                    message_id,
                    bot_response=f"Error: {error_message}",
                    thoughts="",
                    streaming_done=True,
                    tps=0,
                    response_time=0,
                    total_tokens=0,
                    prompt_eval_tps=0,
                )
            return

        try:
            for line in response.iter_lines():
                if state["stop_event"].is_set():
                    state["stop_requested"] = True
                    break
                if not line:
                    continue

                decoded_line = line.decode("utf-8").replace("data:", "").strip()
                if decoded_line == "[DONE]":
                    break

                try:
                    chunk_json = json.loads(decoded_line)
                except json.JSONDecodeError:
                    continue

                delta = chunk_json.get("choices", [{}])[0].get("delta", {})
                delta_text = delta.get("content")
                delta_reasoning = delta.get("reasoning_content")

                delta_text = delta_text if isinstance(delta_text, str) else ""
                delta_reasoning = delta_reasoning if isinstance(delta_reasoning, str) else ""
                if not delta_text and not delta_reasoning:
                    continue

                emitted_answer_delta = ""
                emitted_thoughts_delta = ""

                reasoning_tokens_in_chunk = 0
                answer_tokens_in_chunk = 0

                if delta_reasoning:
                    reasoning_tokens_in_chunk = max(1, len(delta_reasoning.split()))
                    tokens_count += reasoning_tokens_in_chunk
                    if prompt_start_time is None:
                        prompt_start_time = time.time()
                    prompt_end_time = time.time()
                    prompt_tokens_count += reasoning_tokens_in_chunk
                    reasoning += delta_reasoning
                    emitted_thoughts_delta = delta_reasoning

                if delta_text:
                    answer_tokens_in_chunk = max(1, len(delta_text.split()))
                    tokens_count += answer_tokens_in_chunk
                    buffer += delta_text

                    if "<think>" in buffer.lower():
                        if prompt_start_time is None:
                            prompt_start_time = time.time()
                        parts = buffer.split("<think>", 1)
                        final_answer += parts[0]
                        buffer = parts[1]
                        in_thoughts = True

                    if "</think>" in buffer.lower():
                        if prompt_end_time is None:
                            prompt_end_time = time.time()
                        parts = buffer.split("</think>", 1)
                        reasoning += parts[0]
                        emitted_thoughts_delta += parts[0]
                        buffer = parts[1]
                        in_thoughts = False

                    if in_thoughts:
                        prompt_tokens_count += answer_tokens_in_chunk
                        prompt_end_time = time.time()
                        reasoning += buffer
                        emitted_thoughts_delta += buffer
                        buffer = ""
                    else:
                        if prompt_start_time is None:
                            prompt_start_time = start_time
                        if prompt_end_time is None and tokens_count >= PROMPT_TOKEN_LIMIT:
                            prompt_end_time = time.time()
                            prompt_tokens_count = PROMPT_TOKEN_LIMIT

                        final_answer += buffer
                        emitted_answer_delta = buffer
                        buffer = ""

                _emit_receive_message(
                    user_room,
                    session_id,
                    message_id,
                    **stream_event_extra,
                    delta=emitted_answer_delta,
                    thoughts_delta=emitted_thoughts_delta,
                    streaming_done=False,
                )

            if state["stop_event"].is_set():
                state["stop_requested"] = True
        except Exception as e:
            if state["stop_event"].is_set():
                state["stop_requested"] = True
            else:
                stream_error = str(e)
        finally:
            _close_generation_response(response)
            state["response"] = None

        elapsed_time = time.time() - start_time
        overall_tps = round(tokens_count / elapsed_time, 2) if elapsed_time > 0 else 0
        prompt_eval_tps = 0
        if prompt_start_time and prompt_end_time:
            prompt_elapsed = prompt_end_time - prompt_start_time
            prompt_eval_tps = round(prompt_tokens_count / prompt_elapsed, 2) if prompt_elapsed > 0 else 0

        if state.get("auth_invalidated") or not _generation_auth_still_valid(state):
            return

        if stream_error:
            if replace_prompt_id or edit_prompt_id:
                _emit_chat_error(
                    user_room,
                    action,
                    stream_error,
                    message_id=message_id,
                    reset_state=True,
                    session_id=session_id,
                )
            else:
                fallback_text = final_answer.strip() or f"Error: {stream_error}"
                _emit_receive_message(
                    user_room,
                    session_id,
                    message_id,
                    **stream_event_extra,
                    bot_response=fallback_text,
                    thoughts=reasoning.strip(),
                    tps=overall_tps,
                    response_time=round(elapsed_time, 2),
                    total_tokens=tokens_count,
                    prompt_eval_tps=prompt_eval_tps,
                    streaming_done=True,
                )
            return

        if tokens_count == 0 and not state["stop_requested"]:
            error_message = (
                "Error: your input exceeded the model's context window. "
                "Please shorten your prompt or trim previous messages."
            )
            if replace_prompt_id or edit_prompt_id:
                _emit_chat_error(
                    user_room,
                    action,
                    error_message,
                    message_id=message_id,
                    reset_state=True,
                    session_id=session_id,
                )
            else:
                _emit_receive_message(
                    user_room,
                    session_id,
                    message_id,
                    **stream_event_extra,
                    bot_response=error_message,
                    thoughts="",
                    tps=0,
                    response_time=round(elapsed_time, 2),
                    total_tokens=0,
                    prompt_eval_tps=0,
                    streaming_done=True,
                )
            return

        _emit_receive_message(
            user_room,
            session_id,
            message_id,
            **stream_event_extra,
            bot_response=final_answer.strip(),
            thoughts=reasoning.strip(),
            tps=overall_tps,
            response_time=round(elapsed_time, 2),
            total_tokens=tokens_count,
            prompt_eval_tps=prompt_eval_tps,
            streaming_done=True,
            stopped=bool(state["stop_requested"]),
        )

        skip_empty_stopped_write = (
            bool(state["stop_requested"]) and
            tokens_count == 0 and
            not final_answer.strip() and
            not reasoning.strip()
        )
        if skip_empty_stopped_write:
            return

        saved_ok, first_user_message = _persist_chat_turn(
            user_id,
            session_id,
            user_message,
            final_answer,
            reasoning,
            overall_tps,
            elapsed_time,
            tokens_count,
            prompt_eval_tps,
            message_id,
            replace_prompt_id=replace_prompt_id,
            edit_prompt_id=edit_prompt_id,
            attachment_context=attachment_context,
        )
        if not saved_ok:
            _emit_chat_error(
                user_room,
                action,
                "Could not save the chat response.",
                message_id=message_id,
                reset_state=bool(replace_prompt_id or edit_prompt_id),
                session_id=session_id,
            )
            return

        if first_user_message:
            threading.Thread(
                target=auto_generate_chat_title,
                args=(user_id, session_id),
                daemon=True
            ).start()
    finally:
        _clear_active_generation(user_id, message_id)


@socketio.on('connect')
def on_connect():
    user = _require_socket_user(disconnect_on_failure=False)
    if not user:
        return False
    _register_current_socket(user)
    join_room(f"user_{user['id']}")


@socketio.on('disconnect')
def on_disconnect():
    _pop_socket_state(_current_socket_sid())


@socketio.on('join_chat_session')
def handle_join_chat_session(data):
    data = data or {}
    user = _require_socket_user()
    if not user:
        return _socket_auth_error_payload()
    user_id = user['id']

    session_id = (data.get("session_id") or "").strip()
    if not session_id:
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT 1 FROM chats WHERE user_id=? AND session_id=? LIMIT 1",
        (user_id, session_id),
    )
    exists = c.fetchone() is not None
    conn.close()

    if not exists:
        return

    user_room = f"user_{user_id}"
    room = _chat_stream_room(user_room, session_id)
    join_room(room)
    _track_current_socket_room(room)


@socketio.on('leave_chat_session')
def handle_leave_chat_session(data):
    data = data or {}
    user = _require_socket_user()
    if not user:
        return _socket_auth_error_payload()
    user_id = user['id']

    session_id = (data.get("session_id") or "").strip()
    if not session_id:
        return

    user_room = f"user_{user_id}"
    room = _chat_stream_room(user_room, session_id)
    leave_room(room)
    _untrack_current_socket_room(room)


@socketio.on('send_message')
def handle_message(data):
    data = data or {}
    user = _require_socket_user()
    if not user:
        return _socket_auth_error_payload()
    user_id = user['id']

    user_room = f"user_{user_id}"
    session_id = (data.get("session_id") or "").strip()
    if not session_id:
        return

    user_message = (data.get("message") or "").strip()
    if not user_message:
        return

    message_id = data.get("message_id") or data.get("prompt_id") or str(uuid.uuid4())
    edit_prompt_id = (data.get("edit_prompt_id") or "").strip() or None
    if len(user_message) > MAX_NEW_USER_MESSAGE_CHARS:
        if edit_prompt_id:
            _emit_chat_error(
                user_room,
                "edit_prompt",
                MESSAGE_TOO_LARGE_ERROR,
                message_id=message_id,
                reset_state=True,
                session_id=session_id,
            )
        else:
            _emit_receive_message(
                user_room,
                session_id,
                message_id,
                bot_response=MESSAGE_TOO_LARGE_ERROR,
                thoughts="",
                streaming_done=True,
                tps=0,
                response_time=0,
                total_tokens=0,
                prompt_eval_tps=0,
            )
        return

    try:
        incoming_attachment_context = _build_attachment_context(data.get("attachments"))
    except ValueError as e:
        if edit_prompt_id:
            _emit_chat_error(
                user_room,
                "edit_prompt",
                str(e),
                message_id=message_id,
                reset_state=True,
                session_id=session_id,
            )
        else:
            _emit_receive_message(
                user_room,
                session_id,
                message_id,
                bot_response=f"Error: {str(e)}",
                thoughts="",
                streaming_done=True,
                tps=0,
                response_time=0,
                total_tokens=0,
                prompt_eval_tps=0,
            )
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    current_attachment_context = incoming_attachment_context
    if edit_prompt_id:
        target_row = _get_active_turn_row(c, user_id, session_id, edit_prompt_id)
        latest_row = _get_latest_active_turn_row(c, user_id, session_id)

        if not target_row or not target_row[1] or not target_row[6]:
            conn.close()
            _emit_chat_error(
                user_room,
                "edit_prompt",
                "The selected prompt can't be edited.",
                message_id=message_id,
                reset_state=True,
                session_id=session_id,
            )
            return

        if not latest_row or latest_row[1] != target_row[1]:
            conn.close()
            _emit_chat_error(
                user_room,
                "edit_prompt",
                "Only the latest prompt can be edited right now.",
                message_id=message_id,
                reset_state=True,
                session_id=session_id,
            )
            return

        if current_attachment_context is None:
            current_attachment_context = target_row[7]
        history_rows = _get_active_history_rows(c, user_id, session_id, before_id=target_row[0])
    else:
        history_rows = _get_active_history_rows(c, user_id, session_id)
    conn.close()

    messages = _build_limited_model_context(history_rows, user_message, current_attachment_context)
    _stream_chat_reply(
        user_id,
        session_id,
        user_room,
        message_id,
        user_message,
        messages,
        edit_prompt_id=edit_prompt_id,
        attachment_context=current_attachment_context,
    )


@socketio.on('stop_generation')
def handle_stop_generation(data):
    data = data or {}
    user = _require_socket_user()
    if not user:
        return _socket_auth_error_payload()
    user_id = user['id']

    message_id = (data.get("message_id") or data.get("prompt_id") or "").strip() or None
    current = _stop_active_generation(user_id, message_id=message_id)
    if not current:
        return {"status": "idle", "message": "No active response to stop."}

    return {
        "status": "stopping",
        "message_id": current.get("message_id"),
    }


@socketio.on('regenerate_message')
def handle_regenerate_message(data):
    data = data or {}
    user = _require_socket_user()
    if not user:
        return _socket_auth_error_payload()
    user_id = user['id']

    user_room = f"user_{user_id}"
    session_id = (data.get("session_id") or "").strip()
    source_prompt_id = (data.get("prompt_id") or "").strip()
    message_id = (data.get("message_id") or "").strip() or str(uuid.uuid4())
    if not session_id or not source_prompt_id:
        _emit_chat_error(
            user_room,
            "regenerate",
            "Could not find the assistant response to regenerate.",
            message_id=message_id or None,
            reset_state=True,
            session_id=session_id,
        )
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    target_row = _get_active_turn_row(c, user_id, session_id, source_prompt_id)
    latest_row = _get_latest_active_turn_row(c, user_id, session_id)

    if not target_row or not (target_row[3] or "").strip() or not target_row[6]:
        conn.close()
        _emit_chat_error(
            user_room,
            "regenerate",
            "The selected assistant response can't be regenerated.",
            message_id=message_id,
            reset_state=True,
            session_id=session_id,
        )
        return

    if not latest_row or latest_row[1] != target_row[1]:
        conn.close()
        _emit_chat_error(
            user_room,
            "regenerate",
            "Only the latest assistant response can be regenerated right now.",
            message_id=message_id,
            reset_state=True,
            session_id=session_id,
        )
        return

    history_rows = _get_active_history_rows(c, user_id, session_id, before_id=target_row[0])
    conn.close()

    user_message = (target_row[3] or "").strip()
    attachment_context = target_row[7]
    messages = _build_limited_model_context(history_rows, user_message, attachment_context)
    _stream_chat_reply(
        user_id,
        session_id,
        user_room,
        message_id,
        user_message,
        messages,
        replace_prompt_id=source_prompt_id,
        attachment_context=attachment_context,
    )


@chat_routes.route('/get_sessions', methods=['GET'])
@login_required()
def get_sessions():
    user_id = session.get("user_id")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT session_id,
               MIN(session_name) AS session_name,
               MAX(timestamp)    AS last_ts
          FROM chats
         WHERE user_id=?
      GROUP BY session_id
      ORDER BY last_ts DESC
    """, (user_id,))
    sessions = [{"session_id": row[0], "session_name": row[1], "timestamp": row[2]} for row in c.fetchall()]
    conn.close()
    return jsonify({"sessions": sessions})


@chat_routes.route('/get_chat/<session_id>', methods=['GET'])
@login_required()
def get_chat(session_id):
    user_id = session.get("user_id")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT id, timestamp, user_message, bot_response, thoughts, tps, response_time,
               total_tokens, prompt_eval_tps, model_used, prompt_id, turn_id,
               prompt_version, response_version, active_in_chat, source_row_id,
               attachment_context
          FROM chats
         WHERE user_id=? AND session_id=?
           AND active_in_chat=1
           AND turn_id IS NOT NULL
      ORDER BY id ASC
    """, (user_id, session_id))
    active_rows = c.fetchall()

    latest_turn_id = active_rows[-1][11] if active_rows else None
    variant_nav = None
    if latest_turn_id and active_rows[-1][10]:
        variant_nav = _get_variant_navigation(c, user_id, session_id, latest_turn_id, active_rows[-1][10])

    chat_history = []
    for row in active_rows:
        entry = {
            "timestamp": row[1],
            "user_message": row[2],
            "bot_response": row[3],
            "thoughts": row[4],
            "tps": row[5],
            "response_time": row[6],
            "total_tokens": row[7],
            "prompt_eval_tps": row[8],
            "model_used": os.path.basename(row[9]) if row[9] else "Unknown",
            "prompt_id": row[10],
            "turn_id": row[11],
            "prompt_version": row[12],
            "response_version": row[13],
            "active_in_chat": row[14],
            "source_row_id": row[15],
            "attachment_names": _extract_attachment_display_names(row[16]),
            "is_latest_turn": bool(row[11] and row[11] == latest_turn_id),
        }
        if entry["is_latest_turn"] and variant_nav:
            entry.update(variant_nav)
        chat_history.append(entry)
    conn.close()
    return jsonify({"chats": chat_history})


@chat_routes.route('/new_chat', methods=['POST'])
@login_required()
def new_chat():
    user_id = session.get("user_id")
    session_id = str(uuid.uuid4())
    default_name = _default_session_name()
    timestamp = int(time.time())
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO chats (user_id, session_id, session_name, timestamp, model_used, active_in_chat)
        VALUES (?, ?, ?, ?, ?, 0)
    ''', (user_id, session_id, default_name, timestamp, model_routes.current_main_model_used))
    conn.commit()
    conn.close()
    return jsonify({"status": "success", "session_id": session_id})


@chat_routes.route('/select_variant', methods=['POST'])
@login_required()
def select_variant():
    user_id = session.get("user_id")
    data = request.json or {}
    session_id = (data.get("session_id") or "").strip()
    prompt_id = (data.get("prompt_id") or "").strip()

    if not session_id or not prompt_id:
        return jsonify({"status": "error", "message": "session_id and prompt_id are required"}), 400

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        SELECT id, turn_id
          FROM chats
         WHERE user_id=? AND session_id=? AND prompt_id=?
         LIMIT 1
        """,
        (user_id, session_id, prompt_id),
    )
    target_row = c.fetchone()
    latest_row = _get_latest_active_turn_row(c, user_id, session_id)

    if not target_row or not target_row[1]:
        conn.close()
        return jsonify({"status": "error", "message": "Variant not found"}), 404

    if not latest_row or latest_row[1] != target_row[1]:
        conn.close()
        return jsonify({"status": "error", "message": "Only the latest turn can be paged right now"}), 400

    c.execute(
        """
        UPDATE chats
           SET active_in_chat=0
         WHERE user_id=? AND session_id=? AND turn_id=?
        """,
        (user_id, session_id, target_row[1]),
    )
    c.execute(
        """
        UPDATE chats
           SET active_in_chat=1
         WHERE user_id=? AND session_id=? AND id=?
        """,
        (user_id, session_id, target_row[0]),
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "success", "prompt_id": prompt_id})


@chat_routes.route('/delete_session', methods=['POST'])
@login_required()
def delete_session():
    user_id = session.get("user_id")
    data = request.json or {}
    session_id = data.get("session_id", "").strip()

    uuid_pattern = re.compile(r'^[0-9a-fA-F\-]{36}$')
    if not uuid_pattern.match(session_id):
        return jsonify({"status": "error", "errors": {"session_id": "Invalid session_id"}}), 400

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM chats WHERE user_id=? AND session_id=?", (user_id, session_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted"})


@chat_routes.route('/rename_session', methods=['POST'])
@login_required()
def rename_session():
    if request.is_json:
        data = request.get_json(silent=True) or {}
        form = RenameSessionForm(
            formdata=MultiDict({
                "session_id": data.get("session_id", ""),
                "new_name": data.get("new_name", ""),
            }),
            meta={"csrf": False},
        )
    else:
        form = RenameSessionForm()

    if not form.validate_on_submit():
        return jsonify({"status":"error","errors":form.errors}), 400

    user_id = session.get("user_id")
    session_id = form.session_id.data
    new_name   = form.new_name.data.strip()

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "UPDATE chats SET session_name=? WHERE user_id=? AND session_id=?",
        (new_name, user_id, session_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"status":"renamed"})


@chat_routes.route('/debug_llama_response', methods=['GET', 'POST'])
@login_required()
def debug_llama_response():
    data = request.json or {}
    message = data.get('message', '').strip()
    if not message:
        return jsonify({"error": "Message required"}), 400
    if len(message) > 5000:
        return jsonify({"error": "Message too long (max 5000)"}), 400
    if _is_benchmark_running():
        return jsonify({
            "error": "Benchmark is currently running. Debug output is temporarily unavailable until it finishes."
        }), 409

    llama_url = model_routes.get_llama_main_completion_url()
    payload = {"model": "llama", "messages": [{"role": "user", "content": message}], "stream": True}

    try:
        response = requests.post(llama_url, json=payload, stream=True, timeout=600)
        response.raise_for_status()
    except Exception as e:
        return jsonify({"error": f"Error: {str(e)}"}), 500

    full_output = []
    for line in response.iter_lines():
        if line:
            decoded_line = line.decode('utf-8').strip()
            if decoded_line.startswith('data:'):
                json_chunk = decoded_line[len('data:'):].strip()
                if json_chunk == '[DONE]':
                    break
                try:
                    full_output.append(json.loads(json_chunk))
                except json.JSONDecodeError:
                    continue
    return jsonify({"full_response": full_output})



