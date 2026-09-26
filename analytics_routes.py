import sqlite3
import csv
import io
import json
import os
from pathlib import Path
import tempfile
import uuid
import threading
from flask import Blueprint, jsonify, request, Response, session
from helpers import DB_PATH
from auth import get_db, login_required
from runtime_config import get_chat_import_max_mib

analytics_routes = Blueprint('analytics_routes', __name__)

CHAT_INSERT_COLS = [
    "user_id",
    "session_id",
    "session_name",
    "timestamp",
    "user_message",
    "bot_response",
    "thoughts",
    "tps",
    "response_time",
    "total_tokens",
    "prompt_eval_tps",
    "model_used",
    "prompt_id",
    "turn_id",
    "prompt_version",
    "response_version",
    "active_in_chat",
    "source_row_id",
    "attachment_context",
]

# Chat columns accepted from imported files. Source id/user_id are handled
# separately for relationship remapping and role-aware ownership.
IMPORT_ALLOWED = [column for column in CHAT_INSERT_COLS if column != "user_id"]

SQLITE_IMPORT_EXTENSIONS = (".sqlite-backup", ".sqlite3", ".sqlite", ".db")
OWNERSHIP_PRESERVE = "preserve_original_users"
OWNERSHIP_ASSIGN = "assign_to_me"
VALID_CONFLICT_MODES = {"append", "overwrite"}
VALID_OWNERSHIP_MODES = {OWNERSHIP_PRESERVE, OWNERSHIP_ASSIGN}
MAX_IMPORT_SESSION_ID_CHARS = 128
SQLITE_HEADER = b"SQLite format 3\x00"
JSON_BACKUP_FORMAT = "llm-controller-ce-chat-backup"
JSON_BACKUP_VERSION = 1
_csv_import_lock = threading.Lock()


class ImportValidationError(ValueError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code

def _as_int(v, default=None):
    try:
        return int(v)
    except Exception:
        return default

def _as_float(v, default=None):
    try:
        return float(v)
    except Exception:
        return default

def _import_error(message, status_code=400):
    return jsonify({"status": "error", "message": message}), status_code


def _import_limit_error(max_bytes):
    return _import_error(
        f"Import file is too large. Maximum size is {max_bytes // (1024 * 1024)} MiB."
    )

def _uploaded_file_size(file):
    try:
        size = int(file.content_length or 0)
        if size > 0:
            return size
    except Exception:
        pass

    stream = getattr(file, "stream", None)
    if stream is None:
        return None

    try:
        pos = stream.tell()
        stream.seek(0, io.SEEK_END)
        size = stream.tell()
        stream.seek(pos, io.SEEK_SET)
        return int(size)
    except Exception:
        return None

def _read_import_file(file, max_bytes):
    size = _uploaded_file_size(file)
    if size is not None and size > max_bytes:
        return None, _import_limit_error(max_bytes)

    raw = file.read(max_bytes + 1)
    if len(raw) > max_bytes:
        return None, _import_limit_error(max_bytes)

    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, _import_error("Import file must contain valid UTF-8 text.")


def _is_sqlite_filename(filename):
    return any(filename.endswith(extension) for extension in SQLITE_IMPORT_EXTENSIONS)


def _validate_session_id(value, row_label="Import row"):
    if not isinstance(value, str):
        raise ImportValidationError(f"{row_label} has an invalid session ID.")
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_IMPORT_SESSION_ID_CHARS:
        raise ImportValidationError(f"{row_label} has an invalid session ID.")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ImportValidationError(f"{row_label} has an invalid session ID.")
    return normalized


def _positive_source_id(value, field_name, row_label):
    if isinstance(value, bool):
        raise ImportValidationError(f"{row_label} has an invalid {field_name}.")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        raise ImportValidationError(f"{row_label} has an invalid {field_name}.")
    if parsed < 1:
        raise ImportValidationError(f"{row_label} has an invalid {field_name}.")
    return parsed


def _optional_text(value, field_name, row_label):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ImportValidationError(f"{row_label} has invalid {field_name}; text is required.")
    return value


def _sqlite_table_names(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def _sqlite_table_columns(conn, table_name):
    if table_name not in {"chats", "projects", "chat_sessions"}:
        raise ImportValidationError("SQLite backup contains an unsupported structure.")
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()}


def _sqlite_rows(conn, table_name, columns, order_by):
    selected = ", ".join(f'"{column}"' for column in columns)
    query = f'SELECT {selected} FROM "{table_name}" ORDER BY "{order_by}" ASC'
    cursor = conn.execute(query)
    rows = cursor.fetchall()
    return [dict(row) for row in rows]


def _extract_sqlite_source(temp_path):
    source_uri = Path(temp_path).resolve().as_uri() + "?mode=ro&immutable=1"
    conn = None
    try:
        conn = sqlite3.connect(source_uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA trusted_schema=OFF")
        quick_check = conn.execute("PRAGMA quick_check(1)").fetchone()
        if not quick_check or str(quick_check[0]).lower() != "ok":
            raise ImportValidationError("SQLite backup is invalid or unreadable.")

        tables = _sqlite_table_names(conn)
        if "chats" not in tables:
            raise ImportValidationError("SQLite backup does not contain a chats table.")

        chat_columns = _sqlite_table_columns(conn, "chats")
        if not {"id", "session_id", "user_message", "bot_response"}.issubset(chat_columns):
            raise ImportValidationError("SQLite backup has an incompatible chats table.")
        supported_chat_columns = [
            column
            for column in ["id", "user_id", *IMPORT_ALLOWED]
            if column in chat_columns
        ]
        chats = _sqlite_rows(conn, "chats", supported_chat_columns, "id")

        has_projects = "projects" in tables
        has_chat_sessions = "chat_sessions" in tables
        if has_projects != has_chat_sessions:
            raise ImportValidationError(
                "SQLite backup contains incomplete Project metadata."
            )

        projects = []
        chat_sessions = []
        has_project_metadata = has_projects and has_chat_sessions
        if has_project_metadata:
            required_project_columns = {
                "id", "user_id", "name", "instructions", "created_at", "updated_at"
            }
            required_session_columns = {"user_id", "session_id", "project_id"}
            project_columns = _sqlite_table_columns(conn, "projects")
            session_columns = _sqlite_table_columns(conn, "chat_sessions")
            if not required_project_columns.issubset(project_columns):
                raise ImportValidationError(
                    "SQLite backup has an incompatible projects table."
                )
            if not required_session_columns.issubset(session_columns):
                raise ImportValidationError(
                    "SQLite backup has an incompatible chat_sessions table."
                )
            projects = _sqlite_rows(
                conn,
                "projects",
                ["id", "user_id", "name", "instructions", "created_at", "updated_at"],
                "id",
            )
            chat_sessions = _sqlite_rows(
                conn,
                "chat_sessions",
                ["user_id", "session_id", "project_id"],
                "session_id",
            )

        return {
            "chats": chats,
            "projects": projects,
            "chat_sessions": chat_sessions,
            "has_project_metadata": has_project_metadata,
            "is_sqlite": True,
        }
    except ImportValidationError:
        raise
    except sqlite3.Error as exc:
        raise ImportValidationError("SQLite backup is invalid or unreadable.") from exc
    finally:
        if conn is not None:
            conn.close()


def _read_sqlite_import(file, max_bytes):
    size = _uploaded_file_size(file)
    if size is not None and size > max_bytes:
        return None, _import_limit_error(max_bytes)

    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(prefix="llm-controller-chat-import-", suffix=".sqlite")
        total = 0
        header = b""
        with os.fdopen(fd, "wb") as temp_file:
            while True:
                chunk = file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    return None, _import_limit_error(max_bytes)
                if len(header) < len(SQLITE_HEADER):
                    header += chunk[:len(SQLITE_HEADER) - len(header)]
                temp_file.write(chunk)

        if header != SQLITE_HEADER:
            return None, _import_error("Selected file is not a valid SQLite database.")
        return _extract_sqlite_source(temp_path), None
    except ImportValidationError as exc:
        return None, _import_error(str(exc), exc.status_code)
    except OSError:
        return None, _import_error("SQLite backup could not be read safely.", 500)
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass


def _parse_import_source(file, filename, is_admin):
    max_bytes = get_chat_import_max_mib() * 1024 * 1024
    if _is_sqlite_filename(filename):
        if not is_admin:
            return None, _import_error("SQLite backup import is available to administrators only.", 403)
        return _read_sqlite_import(file, max_bytes)

    if not (filename.endswith(".json") or filename.endswith(".csv")):
        formats = ".json or .csv" if not is_admin else ".json, .csv, or a supported SQLite backup"
        return None, _import_error(f"Unsupported file format. Select {formats}.")

    content, error_response = _read_import_file(file, max_bytes)
    if error_response:
        return None, error_response

    try:
        if filename.endswith(".json"):
            rows = json.loads(content)
            if isinstance(rows, dict):
                if rows.get("format") != JSON_BACKUP_FORMAT:
                    return None, _import_error("Unsupported JSON backup format.")
                version = rows.get("version")
                if type(version) is not int or version != JSON_BACKUP_VERSION:
                    return None, _import_error("Unsupported JSON backup version.")
                for collection in ("chats", "projects", "chat_sessions"):
                    if not isinstance(rows.get(collection), list):
                        return None, _import_error(f"JSON backup requires a {collection} array.")
                if not rows["chats"] and not rows["projects"]:
                    return None, _import_error("Backup contains no chats or Projects.")
                return {
                    "chats": rows["chats"],
                    "projects": rows["projects"],
                    "chat_sessions": rows["chat_sessions"],
                    "has_project_metadata": True,
                    "is_sqlite": False,
                    "is_json_backup": True,
                }, None
        else:
            # csv's field limit is process-wide; keep concurrent imports from changing it mid-parse.
            with _csv_import_lock:
                previous_limit = csv.field_size_limit(max_bytes)
                try:
                    reader = csv.DictReader(io.StringIO(content))
                    rows = list(reader)
                finally:
                    csv.field_size_limit(previous_limit)
    except (csv.Error, json.JSONDecodeError, UnicodeError):
        return None, _import_error("Import file could not be parsed as valid JSON or CSV.")

    return {
        "chats": rows,
        "projects": [],
        "chat_sessions": [],
        "has_project_metadata": False,
        "is_sqlite": False,
    }, None

def _export_query(conn, user_id=None):
    """
    Returns (rows, columns) for export.
    If user_id is None: export all rows.
    """
    c = conn.cursor()
    if user_id is None:
        c.execute("SELECT * FROM chats ORDER BY id ASC")
    else:
        c.execute("SELECT * FROM chats WHERE user_id=? ORDER BY id ASC", (user_id,))
    rows = c.fetchall()
    columns = [desc[0] for desc in c.description]
    return rows, columns

def _rows_to_json(rows, columns):
    return [dict(zip(columns, row)) for row in rows]


def _export_json_backup(conn, user_id=None):
    # Keep chats, definitions, and membership in the same read snapshot.
    conn.execute("BEGIN")
    rows, columns = _export_query(conn, user_id)
    scope = " WHERE user_id=?" if user_id is not None else ""
    params = (user_id,) if user_id is not None else ()
    cursor = conn.execute(
        "SELECT id, user_id, name, instructions, created_at, updated_at FROM projects"
        + scope + " ORDER BY id ASC",
        params,
    )
    projects = _rows_to_json(cursor.fetchall(), [column[0] for column in cursor.description])
    scope = " WHERE c.user_id=?" if user_id is not None else ""
    cursor = conn.execute(
        """
        SELECT DISTINCT c.user_id, c.session_id, cs.project_id
          FROM chats AS c
          LEFT JOIN chat_sessions AS cs
            ON cs.user_id=c.user_id AND cs.session_id=c.session_id
        """ + scope + " ORDER BY c.user_id, c.session_id",
        params,
    )
    chat_sessions = _rows_to_json(cursor.fetchall(), [column[0] for column in cursor.description])
    return {
        "format": JSON_BACKUP_FORMAT,
        "version": JSON_BACKUP_VERSION,
        "chats": _rows_to_json(rows, columns),
        "projects": projects,
        "chat_sessions": chat_sessions,
    }


MARKDOWN_EXPORT_FORMAT = "<!-- llm-controller-markdown-export: escaped-table-v1 -->"

def _markdown_table_cell(value):
    if value is None:
        return ""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("|", "\\|")
    )

def _rows_to_markdown(rows, columns):
    md = MARKDOWN_EXPORT_FORMAT + "\n"
    md += "|" + "|".join(columns) + "|\n"
    md += "|" + "|".join(["---"] * len(columns)) + "|\n"
    for row in rows:
        md += "|" + "|".join(_markdown_table_cell(item) for item in row) + "|\n"
    return md

def _role():
    return (session.get("role") or "").lower().strip()

def _is_admin():
    return _role() == "admin"

@analytics_routes.route('/', methods=['GET'])
@login_required(roles=["admin", "user"])
def analytics():
    user_id = session.get("user_id")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Only real chat messages (skip session header rows)
    where_clause = """
        user_id=?
        AND (
            (user_message IS NOT NULL AND TRIM(user_message) <> '')
            OR (attachment_context IS NOT NULL AND TRIM(attachment_context) <> '')
        )
        AND bot_response IS NOT NULL AND TRIM(bot_response) <> ''
        AND model_used IS NOT NULL AND TRIM(model_used) <> ''
    """

    c.execute(f"""
        SELECT model_used, COUNT(*)
        FROM chats
        WHERE {where_clause}
        GROUP BY model_used
    """, (user_id,))
    total_requests = [{"model": row[0], "total_requests": row[1]} for row in c.fetchall()]

    c.execute(f"""
        SELECT model_used, AVG(COALESCE(response_time, 0))
        FROM chats
        WHERE {where_clause}
        GROUP BY model_used
    """, (user_id,))
    avg_response = [{"model": row[0], "avg_response_time": row[1]} for row in c.fetchall()]

    # TPS metrics per model (COALESCE so null TPS doesn't destroy the group)
    c.execute(f"""
        SELECT model_used,
               MIN(COALESCE(tps, 0)),
               MAX(COALESCE(tps, 0)),
               AVG(COALESCE(tps, 0))
        FROM chats
        WHERE {where_clause}
        GROUP BY model_used
    """, (user_id,))
    tps_metrics = [{"model": row[0], "min_tps": row[1], "max_tps": row[2], "avg_tps": row[3]} for row in c.fetchall()]

    # Token totals per model (COALESCE so null tokens count as 0, not null)
    c.execute(f"""
        SELECT model_used, SUM(COALESCE(total_tokens, 0))
        FROM chats
        WHERE {where_clause}
        GROUP BY model_used
    """, (user_id,))
    tokens_sum = [{"model": row[0], "total_tokens": row[1]} for row in c.fetchall()]

    conn.close()
    return jsonify({
        "total_requests": total_requests,
        "avg_response": avg_response,
        "tps_metrics": tps_metrics,
        "tokens_sum": tokens_sum,
    })


@analytics_routes.route('/api', methods=['GET'])
@login_required(roles=["admin"])
def api_analytics():
    per_page = 25
    page = max(1, _as_int(request.args.get("page"), 1))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        SELECT
            COUNT(*) AS api_requests,
            SUM(CASE WHEN endpoint = '/v1/chat/completions' THEN 1 ELSE 0 END) AS completion_requests,
            SUM(CASE
                WHEN LOWER(TRIM(status)) <> 'completed'
                  OR COALESCE(http_status, 0) >= 400
                THEN 1 ELSE 0
            END) AS errors,
            SUM(total_tokens) AS recorded_tokens,
            AVG(CASE
                WHEN endpoint = '/v1/chat/completions'
                 AND LOWER(TRIM(status)) = 'completed'
                 AND COALESCE(http_status, 0) < 400
                 AND duration IS NOT NULL
                THEN duration
            END) AS avg_completion_time
        FROM request_events
        WHERE source = 'api'
    """)
    summary_row = c.fetchone()
    total_requests = int(summary_row[0] or 0) if summary_row else 0
    total_pages = max(1, (total_requests + per_page - 1) // per_page)
    page = min(page, total_pages)

    c.execute("""
        SELECT
            timestamp,
            endpoint,
            requested_model,
            active_model,
            status,
            http_status,
            duration,
            streaming,
            total_tokens
        FROM request_events
        WHERE source = 'api'
        ORDER BY timestamp DESC, id DESC
        LIMIT ? OFFSET ?
    """, (per_page, (page - 1) * per_page))
    recent_rows = c.fetchall()
    conn.close()

    recorded_tokens = summary_row[3] if summary_row else None
    avg_completion_time = summary_row[4] if summary_row else None
    return jsonify({
        "summary": {
            "api_requests": total_requests,
            "completion_requests": int(summary_row[1] or 0) if summary_row else 0,
            "errors": int(summary_row[2] or 0) if summary_row else 0,
            "recorded_tokens": int(recorded_tokens) if recorded_tokens is not None else None,
            "avg_completion_time": float(avg_completion_time) if avg_completion_time is not None else None,
        },
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total_requests,
            "total_pages": total_pages,
        },
        "recent": [
            {
                "timestamp": row[0],
                "endpoint": row[1],
                "requested_model": row[2],
                "active_model": row[3],
                "status": row[4],
                "http_status": row[5],
                "duration": row[6],
                "streaming": bool(row[7]),
                "total_tokens": row[8],
            }
            for row in recent_rows
        ],
    })


@analytics_routes.route('/admin_user_token_totals', methods=['GET'])
@login_required(roles=["admin"])
def admin_user_token_totals():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        SELECT
            user_id,
            COALESCE(SUM(COALESCE(total_tokens, 0)), 0) AS total_tokens,
            COUNT(*) AS rows,
            COUNT(DISTINCT session_id) AS sessions,
            MAX(timestamp) AS last_ts
        FROM chats
        GROUP BY user_id
        ORDER BY total_tokens DESC
    """)

    out = []
    for user_id, total_tokens, rows, sessions, last_ts in c.fetchall():
        out.append({
            "user_id": user_id,
            "total_tokens": int(total_tokens or 0),
            "rows": int(rows or 0),
            "sessions": int(sessions or 0),
            "last_timestamp": last_ts
        })

    conn.close()
    return jsonify({"status": "success", "users": out})

@analytics_routes.route('/export_chats', methods=['GET'])
@login_required(roles=["admin", "user"])
def export_chats():
    user_id = session.get("user_id")
    fmt = request.args.get("format", "csv").lower().strip()

    conn = sqlite3.connect(DB_PATH)
    try:
        if fmt == "json":
            backup = _export_json_backup(conn, user_id)
        else:
            rows, columns = _export_query(conn, user_id=user_id)
    finally:
        conn.close()

    if fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)
        writer.writerows(rows)
        resp = Response(output.getvalue(), mimetype="text/csv")
        resp.headers["Content-Disposition"] = "attachment; filename=chats_export.csv"
        return resp

    if fmt == "json":
        resp = jsonify(backup)
        resp.headers["Content-Disposition"] = "attachment; filename=chats_export.json"
        return resp

    if fmt == "markdown":
        md = _rows_to_markdown(rows, columns)
        resp = Response(md, mimetype="text/markdown")
        resp.headers["Content-Disposition"] = "attachment; filename=chats_export.md"
        return resp

    return jsonify({"status": "error", "message": "Unsupported export format"}), 400


def _resolve_import_ownership_mode(is_admin):
    submitted = (request.form.get("ownership_mode") or "").strip().lower()
    if not is_admin:
        if submitted and submitted != OWNERSHIP_ASSIGN:
            raise ImportValidationError(
                "Preserving source ownership is available to administrators only.",
                403,
            )
        return OWNERSHIP_ASSIGN

    ownership_mode = submitted or OWNERSHIP_PRESERVE
    if ownership_mode not in VALID_OWNERSHIP_MODES:
        raise ImportValidationError("Invalid import ownership mode.")
    return ownership_mode


def _normalize_import_chats(source_rows, ownership_mode, current_user_id, *, project_backup=False):
    if not isinstance(source_rows, list) or (not source_rows and not project_backup):
        raise ImportValidationError("No chats to import.")

    normalized = []
    required_user_ids = set()
    seen_import_ids = set()
    text_fields = {
        "session_name",
        "user_message",
        "bot_response",
        "thoughts",
        "model_used",
        "prompt_id",
        "turn_id",
        "attachment_context",
    }

    for row_number, chat in enumerate(source_rows, start=1):
        row_label = f"Import row {row_number}"
        if not isinstance(chat, dict):
            raise ImportValidationError(f"{row_label} is not a valid chat record.")

        session_id = _validate_session_id(chat.get("session_id"), row_label)
        source_user_id = None
        raw_source_user_id = chat.get("user_id")
        if project_backup:
            raw_source_user_id = _positive_source_id(raw_source_user_id, "source user ID", row_label)
        if ownership_mode == OWNERSHIP_PRESERVE:
            try:
                source_user_id = _positive_source_id(
                    raw_source_user_id,
                    "source user ID",
                    row_label,
                )
            except ImportValidationError as exc:
                raise ImportValidationError(
                    f"{exc} Use Assign to my account if reassignment is intentional."
                ) from exc
            target_user_id = source_user_id
            required_user_ids.add(source_user_id)
        else:
            parsed_source_user_id = _as_int(raw_source_user_id, None)
            source_user_id = parsed_source_user_id if parsed_source_user_id and parsed_source_user_id > 0 else None
            target_user_id = current_user_id

        row = {"session_id": session_id}
        for key in IMPORT_ALLOWED:
            if key == "session_id" or key not in chat:
                continue
            value = chat.get(key)
            row[key] = _optional_text(value, key, row_label) if key in text_fields else value

        import_row_id = None
        if chat.get("id") not in (None, ""):
            import_row_id = _positive_source_id(chat.get("id"), "source row ID", row_label)
            import_id_key = (target_user_id, import_row_id)
            if import_id_key in seen_import_ids:
                raise ImportValidationError(
                    f"{row_label} duplicates a source row ID for the same target owner."
                )
            seen_import_ids.add(import_id_key)

        if row.get("source_row_id") not in (None, ""):
            row["source_row_id"] = _positive_source_id(
                row.get("source_row_id"),
                "source relationship ID",
                row_label,
            )

        row["_import_row_id"] = import_row_id
        row["_source_user_id"] = source_user_id
        row["_target_user_id"] = target_user_id
        normalized.append(row)

    return normalized, required_user_ids


def _normalize_project_metadata(source, normalized_chats, ownership_mode, current_user_id):
    if not source.get("has_project_metadata"):
        return {}, {}, set()

    projects = {}
    required_user_ids = set()
    for row_number, project in enumerate(source.get("projects") or [], start=1):
        row_label = f"Source Project {row_number}"
        if not isinstance(project, dict):
            raise ImportValidationError(f"{row_label} is not a valid Project record.")
        if source.get("is_json_backup") and not {
            "id", "user_id", "name", "instructions", "created_at", "updated_at"
        }.issubset(project):
            raise ImportValidationError(f"{row_label} has incomplete Project metadata.")
        source_project_id = _positive_source_id(project.get("id"), "Project ID", row_label)
        source_user_id = _positive_source_id(project.get("user_id"), "owner ID", row_label)
        source_key = (source_user_id, source_project_id)
        if source_key in projects:
            raise ImportValidationError(f"{row_label} duplicates a Project ID.")

        name = project.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ImportValidationError(f"{row_label} has an invalid name.")
        instructions = project.get("instructions")
        if instructions is not None and not isinstance(instructions, str):
            raise ImportValidationError(f"{row_label} has invalid instructions.")
        if source.get("is_json_backup") and any(
            type(project.get(field)) is not int for field in ("created_at", "updated_at")
        ):
            raise ImportValidationError(f"{row_label} has invalid timestamps.")
        created_at = _as_int(project.get("created_at"), None)
        updated_at = _as_int(project.get("updated_at"), None)
        if created_at is None or updated_at is None:
            raise ImportValidationError(f"{row_label} has invalid timestamps.")

        target_user_id = source_user_id if ownership_mode == OWNERSHIP_PRESERVE else current_user_id
        if ownership_mode == OWNERSHIP_PRESERVE:
            required_user_ids.add(source_user_id)
        projects[source_key] = {
            "source_key": source_key,
            "source_user_id": source_user_id,
            "target_user_id": target_user_id,
            "name": name,
            "instructions": instructions,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    source_chat_pairs = set()
    target_pairs_by_source = {}
    for chat in normalized_chats:
        source_user_id = chat.get("_source_user_id")
        if source_user_id is None:
            raise ImportValidationError(
                "Project metadata cannot be matched because a chat has no valid source user ID."
            )
        source_pair = (source_user_id, chat["session_id"])
        target_pair = (chat["_target_user_id"], chat["session_id"])
        source_chat_pairs.add(source_pair)
        target_pairs_by_source[source_pair] = target_pair

    source_associations = {}
    for row_number, metadata in enumerate(source.get("chat_sessions") or [], start=1):
        row_label = f"Source conversation metadata row {row_number}"
        if not isinstance(metadata, dict):
            raise ImportValidationError(f"{row_label} is not a valid conversation record.")
        if source.get("is_json_backup") and "project_id" not in metadata:
            raise ImportValidationError(f"{row_label} is missing its Project reference.")
        source_user_id = _positive_source_id(metadata.get("user_id"), "owner ID", row_label)
        session_id = _validate_session_id(metadata.get("session_id"), row_label)
        source_pair = (source_user_id, session_id)
        if source_pair in source_associations:
            raise ImportValidationError(f"{row_label} duplicates conversation metadata.")

        raw_project_id = metadata.get("project_id")
        project_key = None
        if raw_project_id not in (None, ""):
            source_project_id = _positive_source_id(raw_project_id, "Project ID", row_label)
            project_key = (source_user_id, source_project_id)
            project = projects.get(project_key)
            if project is None:
                raise ImportValidationError(
                    f"{row_label} references a missing Project or mismatched Project owner."
                )
        if ownership_mode == OWNERSHIP_PRESERVE:
            required_user_ids.add(source_user_id)
        source_associations[source_pair] = project_key

    if source.get("is_json_backup") and set(source_associations) != source_chat_pairs:
        raise ImportValidationError("JSON backup must contain exactly one mapping for each conversation.")

    target_associations = {}
    for source_pair in source_chat_pairs:
        target_pair = target_pairs_by_source[source_pair]
        project_key = source_associations.get(source_pair)
        if target_pair in target_associations and target_associations[target_pair] != project_key:
            raise ImportValidationError(
                f"Imported conversation {target_pair[1]} has conflicting Project associations for target user {target_pair[0]}."
            )
        target_associations[target_pair] = project_key

    return projects, target_associations, required_user_ids


def _validate_source_users(user_ids):
    if not user_ids:
        return
    db = get_db()
    if db is None:
        raise ImportValidationError("Source users could not be verified.", 503)

    cursor = None
    try:
        cursor = db.cursor()
        ordered_ids = sorted(user_ids)
        existing_ids = set()
        for offset in range(0, len(ordered_ids), 500):
            batch = ordered_ids[offset:offset + 500]
            placeholders = ", ".join(["%s"] * len(batch))
            cursor.execute(
                f"SELECT id FROM llm_users WHERE id IN ({placeholders})",
                tuple(batch),
            )
            existing_ids.update(int(row[0]) for row in cursor.fetchall())
    except Exception as exc:
        raise ImportValidationError("Source users could not be verified.", 503) from exc
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass

    missing_ids = sorted(user_ids - existing_ids)
    if missing_ids:
        display_ids = ", ".join(str(user_id) for user_id in missing_ids[:10])
        suffix = "..." if len(missing_ids) > 10 else ""
        raise ImportValidationError(
            f"Source user no longer exists: {display_ids}{suffix}. No changes were made."
        )


def _preflight_project_conflicts(cursor, target_associations, projects, *, reuse_surviving=False):
    reused_project_ids = {}
    reused_destination_ids = {}
    if reuse_surviving:
        # Clear History removes associations, but leaves the owned Project records.
        # IDs alone are not identity; compare the definition and stable creation time.
        # Never update a surviving definition from an uploaded backup.
        for source_key, project in projects.items():
            if project["source_user_id"] != project["target_user_id"]:
                continue
            cursor.execute(
                "SELECT id, name, instructions, created_at FROM projects WHERE id=? AND user_id=?",
                (source_key[1], project["target_user_id"]),
            )
            existing = cursor.fetchone()
            if existing and tuple(existing[1:]) == (
                project["name"], project["instructions"], project["created_at"]
            ):
                reused_project_ids[source_key] = existing[0]
                reused_destination_ids[existing[0]] = source_key
    for (target_user_id, session_id), source_project_key in sorted(target_associations.items()):
        cursor.execute(
            "SELECT project_id FROM chat_sessions WHERE user_id=? AND session_id=?",
            (target_user_id, session_id),
        )
        metadata_row = cursor.fetchone()
        existing_project_id = metadata_row[0] if metadata_row else None
        if existing_project_id is None:
            continue
        if source_project_key is None:
            raise ImportValidationError(
                f"Conversation {session_id} for user {target_user_id} already has a conflicting Project association."
            )

        if reused_project_ids.get(source_project_key) == existing_project_id:
            continue

        source_project = projects[source_project_key]
        cursor.execute(
            """
            SELECT id, user_id, name, instructions, created_at, updated_at
              FROM projects
             WHERE id=?
            """,
            (existing_project_id,),
        )
        existing_project = cursor.fetchone()
        expected = (
            target_user_id,
            source_project["name"],
            source_project["instructions"],
            source_project["created_at"],
            source_project["updated_at"],
        )
        if not existing_project or tuple(existing_project[1:]) != expected:
            raise ImportValidationError(
                f"Conversation {session_id} for user {target_user_id} already has a conflicting Project association."
            )

        mapped_id = reused_project_ids.get(source_project_key)
        mapped_source = reused_destination_ids.get(existing_project_id)
        if (mapped_id is not None and mapped_id != existing_project_id) or (
            mapped_source is not None and mapped_source != source_project_key
        ):
            raise ImportValidationError(
                f"Conversation {session_id} for user {target_user_id} has ambiguous Project metadata."
            )
        reused_project_ids[source_project_key] = existing_project_id
        reused_destination_ids[existing_project_id] = source_project_key
    return reused_project_ids


def _insert_import_projects(cursor, projects, reused_project_ids):
    project_id_map = dict(reused_project_ids)
    for source_project_key, project in sorted(projects.items()):
        if source_project_key in project_id_map:
            continue
        cursor.execute(
            """
            INSERT INTO projects (user_id, name, instructions, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                project["target_user_id"],
                project["name"],
                project["instructions"],
                project["created_at"],
                project["updated_at"],
            ),
        )
        project_id_map[source_project_key] = cursor.lastrowid
    return project_id_map


@analytics_routes.route('/import_chats', methods=['POST'])
@login_required(roles=["admin", "user"])
def import_chats():
    user_id = _as_int(session.get("user_id"), None)
    is_admin = _is_admin()
    conflict = (request.form.get("conflict_resolution", "append") or "append").lower().strip()
    if conflict not in VALID_CONFLICT_MODES:
        return _import_error("Invalid conflict-resolution mode.")
    try:
        ownership_mode = _resolve_import_ownership_mode(is_admin)
    except ImportValidationError as exc:
        return _import_error(str(exc), exc.status_code)

    file = request.files.get("import_file")
    if not file:
        return _import_error("No file provided.")

    filename = (file.filename or "").lower()
    source, source_error = _parse_import_source(file, filename, is_admin)
    if source_error:
        return source_error

    try:
        normalized, required_user_ids = _normalize_import_chats(
            source.get("chats"),
            ownership_mode,
            user_id,
            project_backup=source.get("is_json_backup", False),
        )
        projects, target_associations, project_user_ids = _normalize_project_metadata(
            source,
            normalized,
            ownership_mode,
            user_id,
        )
        required_user_ids.update(project_user_ids)
        if ownership_mode == OWNERSHIP_PRESERVE:
            _validate_source_users(required_user_ids)
    except ImportValidationError as exc:
        return _import_error(str(exc), exc.status_code)

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        reused_project_ids = {}
        if source.get("has_project_metadata"):
            reused_project_ids = _preflight_project_conflicts(
                c, target_associations, projects,
                reuse_surviving=source.get("is_json_backup", False),
            )

        target_pairs = sorted({
            (row["_target_user_id"], row["session_id"])
            for row in normalized
        })
        if conflict == "overwrite":
            for target_user_id, session_id in target_pairs:
                c.execute(
                    "DELETE FROM chats WHERE user_id=? AND session_id=?",
                    (target_user_id, session_id),
                )

        project_id_map = {}
        if source.get("has_project_metadata"):
            project_id_map = _insert_import_projects(c, projects, reused_project_ids)

        imported_row_id_map = {}
        pending_source_links = []
        for row in normalized:
            target_user_id = row["_target_user_id"]
            ts = _as_int(row.get("timestamp"), None)
            tps = _as_float(row.get("tps"), None)
            rt  = _as_float(row.get("response_time"), None)
            tok = _as_int(row.get("total_tokens"), None)
            pet = _as_float(row.get("prompt_eval_tps"), None)
            pver = _as_int(row.get("prompt_version"), 1)
            rver = _as_int(row.get("response_version"), 1)
            src_row_id = _as_int(row.get("source_row_id"), None)
            attachment_context = row.get("attachment_context")

            user_message = row.get("user_message")
            bot_response = row.get("bot_response")
            has_turn_content = bool(
                (user_message or "").strip()
                or (bot_response or "").strip()
                or (attachment_context or "").strip()
            )
            prompt_id = (row.get("prompt_id") or "").strip() or None
            if has_turn_content and not prompt_id:
                prompt_id = str(uuid.uuid4())

            turn_id = (row.get("turn_id") or "").strip() or None
            if has_turn_content and not turn_id:
                turn_id = str(uuid.uuid4())

            active_in_chat = _as_int(row.get("active_in_chat"), 1 if has_turn_content else 0)

            c.execute(
                """
                INSERT INTO chats
                (user_id, session_id, session_name, timestamp,
                 user_message, bot_response, thoughts,
                 tps, response_time, total_tokens, prompt_eval_tps,
                 model_used, prompt_id, turn_id, prompt_version,
                 response_version, active_in_chat, source_row_id, attachment_context)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_user_id,
                    row.get("session_id"),
                    row.get("session_name"),
                    ts,
                    user_message,
                    bot_response,
                    row.get("thoughts"),
                    tps,
                    rt,
                    tok,
                    pet,
                    row.get("model_used"),
                    prompt_id,
                    turn_id,
                    pver,
                    rver,
                    active_in_chat,
                    None,
                    attachment_context,
                )
            )

            new_row_id = c.lastrowid
            import_row_id = _as_int(row.get("_import_row_id"), None)
            if import_row_id is not None:
                imported_row_id_map[(target_user_id, import_row_id)] = new_row_id
            if src_row_id is not None:
                pending_source_links.append((new_row_id, target_user_id, src_row_id))

        for local_row_id, target_user_id, imported_source_row_id in pending_source_links:
            remapped_source_row_id = imported_row_id_map.get(
                (target_user_id, imported_source_row_id)
            )
            if remapped_source_row_id is None:
                continue
            c.execute(
                "UPDATE chats SET source_row_id=? WHERE id=? AND user_id=?",
                (remapped_source_row_id, local_row_id, target_user_id),
            )

        if source.get("has_project_metadata"):
            for (target_user_id, session_id), source_project_key in sorted(target_associations.items()):
                destination_project_id = (
                    project_id_map[source_project_key]
                    if source_project_key is not None
                    else None
                )
                c.execute(
                    """
                    INSERT INTO chat_sessions (user_id, session_id, project_id)
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id, session_id) DO UPDATE SET
                        project_id=excluded.project_id
                    """,
                    (target_user_id, session_id, destination_project_id),
                )
        else:
            for target_user_id, session_id in target_pairs:
                c.execute(
                    """
                    INSERT OR IGNORE INTO chat_sessions (user_id, session_id, project_id)
                    VALUES (?, ?, NULL)
                    """,
                    (target_user_id, session_id),
                )

        conn.commit()
    except ImportValidationError as exc:
        if conn is not None:
            conn.rollback()
        return _import_error(str(exc), exc.status_code)
    except Exception:
        if conn is not None:
            conn.rollback()
        return _import_error("Chat history import could not be completed.", 500)
    finally:
        if conn is not None:
            conn.close()

    return jsonify({
        "status": "success",
        "imported": len(normalized),
        "ownership_mode": ownership_mode,
    })


@analytics_routes.route('/delete_all_chats', methods=['POST'])
@login_required(roles=["admin", "user"])
def delete_all_chats():
    # Users only delete their own chats.
    user_id = session.get("user_id")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM chat_sessions WHERE user_id=?", (user_id,))
    c.execute("DELETE FROM chats WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "success", "message": "All chats deleted"})


@analytics_routes.route('/admin_export_chats', methods=['GET'])
@login_required(roles=["admin"])
def admin_export_chats():
    """
    Admin export:
      /analytics/admin_export_chats?format=csv
      /analytics/admin_export_chats?format=csv&user_id=123
    """
    fmt = request.args.get("format", "csv").lower().strip()
    user_id = request.args.get("user_id", "").strip()
    target_user_id = _as_int(user_id, None) if user_id else None

    conn = sqlite3.connect(DB_PATH)
    try:
        if fmt == "json":
            backup = _export_json_backup(conn, target_user_id)
        else:
            rows, columns = _export_query(conn, user_id=target_user_id)
    finally:
        conn.close()

    suffix = "all" if target_user_id is None else f"user_{target_user_id}"

    if fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)
        writer.writerows(rows)
        resp = Response(output.getvalue(), mimetype="text/csv")
        resp.headers["Content-Disposition"] = f"attachment; filename=chats_export_{suffix}.csv"
        return resp

    if fmt == "json":
        resp = jsonify(backup)
        resp.headers["Content-Disposition"] = f"attachment; filename=chats_export_{suffix}.json"
        return resp

    if fmt == "markdown":
        md = _rows_to_markdown(rows, columns)
        resp = Response(md, mimetype="text/markdown")
        resp.headers["Content-Disposition"] = f"attachment; filename=chats_export_{suffix}.md"
        return resp

    return jsonify({"status": "error", "message": "Unsupported export format"}), 400


@analytics_routes.route('/admin_delete_user_chats', methods=['POST'])
@login_required(roles=["admin"])
def admin_delete_user_chats():
    """
    Admin delete:
      { "user_id": 123 }
    """
    data = request.get_json(silent=True) or {}
    uid = _as_int(data.get("user_id"), None)
    if uid is None:
        return jsonify({"status": "error", "message": "user_id required"}), 400

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM chat_sessions WHERE user_id=?", (uid,))
    c.execute("DELETE FROM chats WHERE user_id=?", (uid,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return jsonify({"status": "success", "deleted_rows": deleted, "user_id": uid})


@analytics_routes.route('/admin_delete_all_chats', methods=['POST'])
@login_required(roles=["admin"])
def admin_delete_all_chats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM chat_sessions")
    c.execute("DELETE FROM chats")
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return jsonify({"status": "success", "deleted_rows": deleted})
