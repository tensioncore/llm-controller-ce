"""MySQL persistence boundary for authenticated per-user preferences."""

from db_mysql import exec as mysql_exec
from db_mysql import query_one


MAX_SYSTEM_INSTRUCTIONS_CHARS = 20000
MAX_SYSTEM_INSTRUCTIONS_BYTES = 60000


class UserPreferencesUnavailableError(RuntimeError):
    """Controlled failure used when preference persistence is unavailable."""


def normalize_system_instructions(value):
    """Normalize a preference value for storage and model use."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("System Instructions must be text.")

    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > MAX_SYSTEM_INSTRUCTIONS_CHARS:
        raise ValueError(
            f"System Instructions cannot exceed {MAX_SYSTEM_INSTRUCTIONS_CHARS:,} characters."
        )
    if len(normalized.encode("utf-8")) > MAX_SYSTEM_INSTRUCTIONS_BYTES:
        raise ValueError("System Instructions are too large to save.")
    return normalized


def get_user_system_instructions(user_id):
    """Return normalized System Instructions owned by one authenticated user."""
    user_id = int(user_id)
    if user_id < 1:
        raise ValueError("Invalid user preference owner.")

    try:
        row = query_one(
            """
            SELECT system_instructions
              FROM llm_user_preferences
             WHERE user_id=%s
             LIMIT 1
            """,
            (user_id,),
            dictionary=True,
        )
    except Exception as exc:
        raise UserPreferencesUnavailableError(
            "System Instructions could not be loaded."
        ) from exc
    if not row:
        return None
    return normalize_system_instructions(row.get("system_instructions"))


def set_user_system_instructions(user_id, value):
    """Upsert nonblank instructions, or clear an existing row to NULL."""
    user_id = int(user_id)
    if user_id < 1:
        raise ValueError("Invalid user preference owner.")

    normalized = normalize_system_instructions(value)
    if normalized is None:
        mysql_exec(
            """
            UPDATE llm_user_preferences
               SET system_instructions=NULL,
                   updated_at=CURRENT_TIMESTAMP
             WHERE user_id=%s
            """,
            (user_id,),
        )
        return None

    mysql_exec(
        """
        INSERT INTO llm_user_preferences (user_id, system_instructions)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE
          system_instructions=VALUES(system_instructions),
          updated_at=CURRENT_TIMESTAMP
        """,
        (user_id, normalized),
    )
    return normalized
