# analytics_routes.py
import sqlite3
import csv
import io
import json
import uuid
from flask import Blueprint, jsonify, request, Response, session
from helpers import DB_PATH
from auth import login_required

analytics_routes = Blueprint('analytics_routes', __name__)

# -----------------------------
# Helpers
# -----------------------------

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

# columns we accept from imported files (we ignore id/user_id even if present)
IMPORT_ALLOWED = [
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

MAX_IMPORT_FILE_BYTES = 10 * 1024 * 1024
MAX_IMPORT_ROWS = 25000

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

def _import_limit_error(kind):
    if kind == "size":
        return jsonify({
            "status": "error",
            "message": f"Import file is too large. Maximum size is {MAX_IMPORT_FILE_BYTES // (1024 * 1024)} MB."
        }), 400
    return jsonify({
        "status": "error",
        "message": f"Import contains too many rows/items. Maximum is {MAX_IMPORT_ROWS}."
    }), 400

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

def _read_import_file(file):
    size = _uploaded_file_size(file)
    if size is not None and size > MAX_IMPORT_FILE_BYTES:
        return None, _import_limit_error("size")

    raw = file.read(MAX_IMPORT_FILE_BYTES + 1)
    if len(raw) > MAX_IMPORT_FILE_BYTES:
        return None, _import_limit_error("size")

    try:
        return raw.decode("utf-8"), None
    except Exception as e:
        return None, (jsonify({"status": "error", "message": f"File decode failed: {str(e)}"}), 400)

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

def _require_admin_json():
    if not _is_admin():
        return jsonify({"status": "error", "message": "Not authorized"}), 403
    return None

# -----------------------------
# Per-user analytics
# -----------------------------

@analytics_routes.route('/', methods=['GET'])
@login_required(roles=["admin", "user"])
def analytics():
    user_id = session.get("user_id")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Only real chat messages (skip session header rows)
    where_clause = """
        user_id=?
        AND user_message IS NOT NULL AND TRIM(user_message) <> ''
        AND bot_response IS NOT NULL AND TRIM(bot_response) <> ''
        AND model_used IS NOT NULL AND TRIM(model_used) <> ''
    """

    # Total requests per model
    c.execute(f"""
        SELECT model_used, COUNT(*)
        FROM chats
        WHERE {where_clause}
        GROUP BY model_used
    """, (user_id,))
    total_requests = [{"model": row[0], "total_requests": row[1]} for row in c.fetchall()]

    # Avg response time per model
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
        "tokens_sum": tokens_sum
    })


# -----------------------------
# Admin User Analytics
# -----------------------------

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

# -----------------------------
# Per-user export/import/delete (locked)
# -----------------------------

@analytics_routes.route('/export_chats', methods=['GET'])
@login_required(roles=["admin", "user"])
def export_chats():
    user_id = session.get("user_id")
    fmt = request.args.get("format", "csv").lower().strip()

    conn = sqlite3.connect(DB_PATH)
    rows, columns = _export_query(conn, user_id=user_id)
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
        resp = jsonify(_rows_to_json(rows, columns))
        resp.headers["Content-Disposition"] = "attachment; filename=chats_export.json"
        return resp

    if fmt == "markdown":
        md = _rows_to_markdown(rows, columns)
        resp = Response(md, mimetype="text/markdown")
        resp.headers["Content-Disposition"] = "attachment; filename=chats_export.md"
        return resp

    return jsonify({"status": "error", "message": "Unsupported export format"}), 400


@analytics_routes.route('/import_chats', methods=['POST'])
@login_required(roles=["admin", "user"])
def import_chats():
    user_id = session.get("user_id")
    conflict = (request.form.get("conflict_resolution", "append") or "append").lower().strip()
    file = request.files.get("import_file")
    if not file:
        return jsonify({"status": "error", "message": "No file provided"}), 400

    filename = (file.filename or "").lower()
    content, limit_response = _read_import_file(file)
    if limit_response:
        return limit_response

    imported = []
    try:
        if filename.endswith(".json"):
            imported = json.loads(content)
        elif filename.endswith(".csv"):
            f = io.StringIO(content)
            reader = csv.DictReader(f)
            imported = []
            for row_number, row in enumerate(reader, start=1):
                if row_number > MAX_IMPORT_ROWS:
                    return _import_limit_error("rows")
                imported.append(row)
        else:
            return jsonify({"status": "error", "message": "Unsupported file format (must be .json or .csv)"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"File parse error: {str(e)}"}), 400

    if isinstance(imported, list) and len(imported) > MAX_IMPORT_ROWS:
        return _import_limit_error("rows")

    if not isinstance(imported, list) or not imported:
        return jsonify({"status": "error", "message": "No chats to import."}), 400

    # Normalize: keep only allowed keys; FORCE user_id=current user
    normalized = []
    for chat in imported:
        if not isinstance(chat, dict):
            continue
        row = {}
        for k in IMPORT_ALLOWED:
            if k in chat:
                row[k] = chat.get(k)
        # required-ish keys
        sid = (row.get("session_id") or "").strip()
        if not sid:
            # skip rows without session_id
            continue
        row["session_id"] = sid
        row["_import_row_id"] = _as_int(chat.get("id"), None)
        normalized.append(row)

    if not normalized:
        return jsonify({"status": "error", "message": "No valid chat rows found in file."}), 400

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        if conflict == "overwrite":
            # Delete only THIS user's sessions that appear in the import
            sessions = sorted({(r.get("session_id") or "").strip() for r in normalized if r.get("session_id")})
            for sid in sessions:
                c.execute("DELETE FROM chats WHERE user_id=? AND session_id=?", (user_id, sid))

        # Insert rows (ignore incoming id/user_id), then remap source_row_id only
        # when the imported source row exists in this same import batch.
        imported_row_id_map = {}
        pending_source_links = []
        for row in normalized:
            # Try to keep types sane
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
            has_turn_content = bool((user_message or "").strip() or (bot_response or "").strip())
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
                    user_id,
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
                imported_row_id_map[import_row_id] = new_row_id
            if src_row_id is not None:
                pending_source_links.append((new_row_id, src_row_id))

        for local_row_id, imported_source_row_id in pending_source_links:
            remapped_source_row_id = imported_row_id_map.get(imported_source_row_id)
            if remapped_source_row_id is None:
                continue
            c.execute(
                "UPDATE chats SET source_row_id=? WHERE id=? AND user_id=?",
                (remapped_source_row_id, local_row_id, user_id),
            )

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({"status": "error", "message": f"Database error: {str(e)}"}), 500

    conn.close()
    return jsonify({"status": "success", "imported": len(normalized)})


@analytics_routes.route('/delete_all_chats', methods=['POST'])
@login_required(roles=["admin", "user"])
def delete_all_chats():
    # Users only delete their own chats.
    user_id = session.get("user_id")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM chats WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "success", "message": "All chats deleted"})


# -----------------------------
# Admin-only: users list + per-user/all export + delete
# -----------------------------

@analytics_routes.route('/admin_chat_users', methods=['GET'])
@login_required(roles=["admin"])
def admin_chat_users():
    # list users present in SQLite chat DB (fast + no MySQL dependency)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT user_id,
               COUNT(*) as row_count,
               COUNT(DISTINCT session_id) as session_count,
               MAX(timestamp) as last_ts
          FROM chats
      GROUP BY user_id
      ORDER BY last_ts DESC
    """)
    users = [{
        "user_id": row[0],
        "rows": row[1],
        "sessions": row[2],
        "last_timestamp": row[3],
    } for row in c.fetchall()]
    conn.close()
    return jsonify({"users": users})


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
    rows, columns = _export_query(conn, user_id=target_user_id)
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
        resp = jsonify(_rows_to_json(rows, columns))
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
    c.execute("DELETE FROM chats")
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return jsonify({"status": "success", "deleted_rows": deleted})
