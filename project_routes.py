import sqlite3
import time

from flask import Blueprint, current_app, jsonify, request, session

from auth import login_required
from helpers import DB_PATH


project_routes = Blueprint("project_routes", __name__)

MAX_PROJECT_NAME_CHARS = 100
MAX_PROJECT_INSTRUCTIONS_CHARS = 20000
MAX_PROJECT_INSTRUCTIONS_BYTES = 60000
MAX_SESSION_ID_CHARS = 128
MEMORY_MODES = {"off", "project", "personal_project"}


class ProjectInstructionsUnavailableError(RuntimeError):
    """Controlled failure used when Project metadata cannot be read."""


def _current_user_id():
    user_id = int(session.get("user_id") or 0)
    if user_id < 1:
        raise ValueError("Invalid project owner.")
    return user_id


def _positive_int(value, field_name):
    if isinstance(value, bool):
        raise ValueError(f"{field_name} is invalid.")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        raise ValueError(f"{field_name} is invalid.")
    if parsed < 1:
        raise ValueError(f"{field_name} is invalid.")
    return parsed


def _normalize_project_name(value):
    if not isinstance(value, str):
        raise ValueError("Project name is required.")
    normalized = value.strip()
    if not normalized:
        raise ValueError("Project name is required.")
    if len(normalized) > MAX_PROJECT_NAME_CHARS:
        raise ValueError(
            f"Project name cannot exceed {MAX_PROJECT_NAME_CHARS} characters."
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError("Project name contains unsupported characters.")
    return normalized


def _normalize_project_instructions(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Project Instructions must be text.")
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > MAX_PROJECT_INSTRUCTIONS_CHARS:
        raise ValueError(
            f"Project Instructions cannot exceed {MAX_PROJECT_INSTRUCTIONS_CHARS:,} characters."
        )
    if len(normalized.encode("utf-8")) > MAX_PROJECT_INSTRUCTIONS_BYTES:
        raise ValueError("Project Instructions are too large to save.")
    return normalized


def _normalize_session_id(value):
    if not isinstance(value, str):
        raise ValueError("Conversation is required.")
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_SESSION_ID_CHARS:
        raise ValueError("Conversation is invalid.")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError("Conversation is invalid.")
    return normalized


def _normalize_memory_mode(value):
    if not isinstance(value, str) or value not in MEMORY_MODES:
        raise ValueError("Choose a valid Project Memory mode.")
    return value


def get_owned_project_memory_mode(user_id, project_id):
    """Validate ownership before exposing Project Memory or its controls."""
    user_id = _positive_int(user_id, "Project owner")
    project_id = _positive_int(project_id, "Project")
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT memory_mode FROM projects WHERE id=? AND user_id=?",
            (project_id, user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Project not found.")
        return _normalize_memory_mode(row[0])
    finally:
        conn.close()


def get_owned_session_project_id(user_id, session_id):
    """Resolve Memory scope from an owned conversation; reject broken associations."""
    user_id = _positive_int(user_id, "Project owner")
    session_id = _normalize_session_id(session_id)
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """
            SELECT metadata.project_id, owned_project.id
              FROM chats AS owned_chat
              LEFT JOIN chat_sessions AS metadata
                ON metadata.user_id=owned_chat.user_id
               AND metadata.session_id=owned_chat.session_id
              LEFT JOIN projects AS owned_project
                ON owned_project.id=metadata.project_id
               AND owned_project.user_id=owned_chat.user_id
             WHERE owned_chat.user_id=? AND owned_chat.session_id=?
             LIMIT 1
            """, (user_id, session_id),
        ).fetchone()
        if row is None or (row[0] is not None and row[1] is None):
            raise ValueError("Owned conversation scope is unavailable")
        return row[1]
    finally:
        conn.close()


def get_session_project_instructions(user_id, session_id):
    """Return current owned Project Instructions for one conversation."""
    try:
        user_id = int(user_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid Project owner.") from exc
    if user_id < 1:
        raise ValueError("Invalid Project owner.")
    session_id = _normalize_session_id(session_id)

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            """
            SELECT owned_project.instructions
              FROM chat_sessions AS metadata
              JOIN projects AS owned_project
                ON owned_project.id=metadata.project_id
               AND owned_project.user_id=metadata.user_id
             WHERE metadata.user_id=?
               AND metadata.session_id=?
               AND EXISTS (
                    SELECT 1
                      FROM chats AS owned_chat
                     WHERE owned_chat.user_id=metadata.user_id
                       AND owned_chat.session_id=metadata.session_id
               )
             LIMIT 1
            """,
            (user_id, session_id),
        ).fetchone()
    except sqlite3.Error as exc:
        raise ProjectInstructionsUnavailableError(
            "Project Instructions could not be loaded."
        ) from exc
    finally:
        if conn is not None:
            conn.close()

    if row is None:
        return None
    return _normalize_project_instructions(row[0])


def _project_payload(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "instructions": row["instructions"],
        "memory_mode": row["memory_mode"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "conversation_count": row["conversation_count"],
    }


@project_routes.route("/list", methods=["GET"])
@login_required()
def list_projects():
    user_id = _current_user_id()
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT p.id,
                   p.name,
                   p.instructions,
                   p.memory_mode,
                   p.created_at,
                   p.updated_at,
                   COUNT(cs.session_id) AS conversation_count
              FROM projects AS p
         LEFT JOIN chat_sessions AS cs
                ON cs.user_id=p.user_id
               AND cs.project_id=p.id
             WHERE p.user_id=?
          GROUP BY p.id, p.name, p.instructions, p.memory_mode, p.created_at, p.updated_at
          ORDER BY p.updated_at DESC, p.id DESC
            """,
            (user_id,),
        ).fetchall()
        return jsonify({
            "status": "success",
            "projects": [_project_payload(row) for row in rows],
        })
    except sqlite3.Error:
        current_app.logger.exception("Could not list Projects for user %s", user_id)
        return jsonify({"status": "error", "message": "Projects could not be loaded."}), 500
    finally:
        if conn is not None:
            conn.close()


@project_routes.route("/create", methods=["POST"])
@login_required()
def create_project():
    user_id = _current_user_id()
    data = request.get_json(silent=True) or {}
    try:
        name = _normalize_project_name(data.get("name"))
        instructions = _normalize_project_instructions(data.get("instructions"))
        memory_mode = _normalize_memory_mode(data.get("memory_mode", "project"))
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    now = int(time.time())
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """
            INSERT INTO projects (user_id, name, instructions, memory_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, name, instructions, memory_mode, now, now),
        )
        project_id = cursor.lastrowid
        conn.commit()
        row = conn.execute(
            """
            SELECT id, name, instructions, memory_mode, created_at, updated_at,
                   0 AS conversation_count
              FROM projects
             WHERE id=? AND user_id=?
            """,
            (project_id, user_id),
        ).fetchone()
        return jsonify({"status": "success", "project": _project_payload(row)}), 201
    except sqlite3.Error:
        if conn is not None:
            conn.rollback()
        current_app.logger.exception("Could not create Project for user %s", user_id)
        return jsonify({"status": "error", "message": "Project could not be created."}), 500
    finally:
        if conn is not None:
            conn.close()


@project_routes.route("/update", methods=["POST"])
@login_required()
def update_project():
    user_id = _current_user_id()
    data = request.get_json(silent=True) or {}
    try:
        project_id = _positive_int(data.get("project_id"), "Project")
        name = _normalize_project_name(data.get("name"))
        instructions = _normalize_project_instructions(data.get("instructions"))
        memory_mode = _normalize_memory_mode(data["memory_mode"]) if "memory_mode" in data else None
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        existing = conn.execute(
            "SELECT id FROM projects WHERE id=? AND user_id=?",
            (project_id, user_id),
        ).fetchone()
        if existing is None:
            return jsonify({"status": "error", "message": "Project not found."}), 404

        conn.execute(
            """
            UPDATE projects
               SET name=?, instructions=?, memory_mode=COALESCE(?, memory_mode), updated_at=?
             WHERE id=? AND user_id=?
            """,
            (name, instructions, memory_mode, int(time.time()), project_id, user_id),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT p.id,
                   p.name,
                   p.instructions,
                   p.memory_mode,
                   p.created_at,
                   p.updated_at,
                   COUNT(cs.session_id) AS conversation_count
              FROM projects AS p
         LEFT JOIN chat_sessions AS cs
                ON cs.user_id=p.user_id
               AND cs.project_id=p.id
             WHERE p.id=? AND p.user_id=?
          GROUP BY p.id, p.name, p.instructions, p.memory_mode, p.created_at, p.updated_at
            """,
            (project_id, user_id),
        ).fetchone()
        return jsonify({"status": "success", "project": _project_payload(row)})
    except sqlite3.Error:
        if conn is not None:
            conn.rollback()
        current_app.logger.exception(
            "Could not update Project %s for user %s", project_id, user_id
        )
        return jsonify({"status": "error", "message": "Project could not be updated."}), 500
    finally:
        if conn is not None:
            conn.close()


@project_routes.route("/<int:project_id>/memory", methods=["GET"])
@login_required()
def list_project_memory(project_id):
    import memory_service

    user_id = _current_user_id()
    try:
        mode = get_owned_project_memory_mode(user_id, project_id)
    except LookupError:
        return jsonify({"status": "error", "message": "Project not found."}), 404
    except Exception as exc:
        memory_service._log(f"Project Memory browser unavailable ({memory_service._exception_detail(exc)}).", "WARN")
        return jsonify({"status": "success", "state": "unavailable", "items": []})
    payload = memory_service.browse({"user_id": str(user_id), "agent_id": f"project-{project_id}"})
    response = jsonify({"status": "success", "memory_mode": mode, **payload})
    response.headers["Cache-Control"] = "no-store"
    return response


@project_routes.route("/<int:project_id>/memory/manage", methods=["POST"])
@login_required()
def manage_project_memory(project_id):
    import memory_service

    # The application CSRFProtect covers this authenticated browser POST.
    payload, status = memory_service.mutate(
        _current_user_id(), request.get_json(silent=True), project_id=project_id,
    )
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response, status


@project_routes.route("/delete", methods=["POST"])
@login_required()
def delete_project():
    import memory_service

    user_id = _current_user_id()
    data = request.get_json(silent=True) or {}
    try:
        project_id = _positive_int(data.get("project_id"), "Project")
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    conn = None
    try:
        # Clear while ownership exists, and hold the Memory lock through commit
        # so a queued capture cannot recreate the destroyed Project namespace.
        try:
            get_owned_project_memory_mode(user_id, project_id)
        except LookupError:
            return jsonify({"status": "error", "message": "Project not found."}), 404
        with memory_service.project_deletion(user_id, project_id):
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                """
                UPDATE chat_sessions
                   SET project_id=NULL
                 WHERE user_id=? AND project_id=?
                """,
                (user_id, project_id),
            )
            conn.execute(
                "DELETE FROM projects WHERE id=? AND user_id=?",
                (project_id, user_id),
            )
            conn.commit()
        return jsonify({"status": "success", "project_id": project_id})
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        memory_service._log(f"Project deletion failed ({memory_service._exception_detail(exc)}); Project deletion not completed.", "WARN")
        return jsonify({"status": "error", "message": "Project could not be deleted."}), 500
    finally:
        if conn is not None:
            conn.close()


@project_routes.route("/assign_session", methods=["POST"])
@login_required()
def assign_session():
    user_id = _current_user_id()
    data = request.get_json(silent=True) or {}
    try:
        session_id = _normalize_session_id(data.get("session_id"))
        raw_project_id = data.get("project_id")
        project_id = (
            None
            if raw_project_id is None or raw_project_id == ""
            else _positive_int(raw_project_id, "Project")
        )
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        owned_session = conn.execute(
            """
            SELECT 1
              FROM chats
             WHERE user_id=? AND session_id=?
             LIMIT 1
            """,
            (user_id, session_id),
        ).fetchone()
        if owned_session is None:
            return jsonify({"status": "error", "message": "Conversation not found."}), 404

        if project_id is not None:
            owned_project = conn.execute(
                "SELECT 1 FROM projects WHERE id=? AND user_id=?",
                (project_id, user_id),
            ).fetchone()
            if owned_project is None:
                return jsonify({"status": "error", "message": "Project not found."}), 404

        conn.execute(
            """
            INSERT INTO chat_sessions (user_id, session_id, project_id)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, session_id) DO UPDATE SET
                project_id=excluded.project_id
            """,
            (user_id, session_id, project_id),
        )
        conn.commit()
        return jsonify({
            "status": "success",
            "session_id": session_id,
            "project_id": project_id,
        })
    except sqlite3.Error:
        if conn is not None:
            conn.rollback()
        current_app.logger.exception(
            "Could not assign conversation %s for user %s", session_id, user_id
        )
        return jsonify({"status": "error", "message": "Conversation could not be moved."}), 500
    finally:
        if conn is not None:
            conn.close()
