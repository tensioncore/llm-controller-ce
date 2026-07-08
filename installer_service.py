# installer_service.py
import os
import re

import mysql.connector
from werkzeug.security import generate_password_hash


INSTALL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "install")
SCHEMA_SQL_PATH = os.path.join(INSTALL_DIR, "schema.sql")
SEED_SQL_PATH = os.path.join(INSTALL_DIR, "seed.sql")

PASSWORD_POLICY_PRESETS = {
    "basic": {
        "min_length": 6,
        "require_upper": False,
        "require_lower": True,
        "require_digit": False,
        "require_special": False,
    },
    "moderate": {
        "min_length": 8,
        "require_upper": True,
        "require_lower": True,
        "require_digit": True,
        "require_special": False,
    },
    "strong": {
        "min_length": 12,
        "require_upper": True,
        "require_lower": True,
        "require_digit": True,
        "require_special": True,
    },
}

PASSWORD_POLICY_OPTIONS = [
    ("basic", "Basic"),
    ("moderate", "Moderate"),
    ("strong", "Strong"),
]

INSTALLER_REVIEW_FIELDS = [
    {
        "field": "llama_server_path",
        "key": "llm.llama_server_path",
        "label": "Llama-server path",
        "value_type": "string",
        "description": "Path to llama-server executable",
        "default": "llama-server/llama-server.exe",
    },
    {
        "field": "scan_directory",
        "key": "llm.scan_directory",
        "label": "Default scan folder",
        "value_type": "string",
        "description": "Base folder to scan for GGUF models",
        "default": "LLMs",
    },
    {
        "field": "n_gpu_layers",
        "key": "llm.defaults.n_gpu_layers",
        "label": "Default GPU layers",
        "value_type": "int",
        "description": "Default GPU layers",
        "default": 360,
        "minimum": 0,
        "maximum": 1000,
    },
    {
        "field": "n_cpu_threads",
        "key": "llm.defaults.n_cpu_threads",
        "label": "Default CPU threads",
        "value_type": "int",
        "description": "Default CPU threads",
        "default": 30,
        "minimum": 1,
        "maximum": 256,
    },
    {
        "field": "dual_gpu_split_threshold_gb",
        "key": "llama.dual_gpu_split_threshold_gb",
        "label": "GPU split threshold (GB)",
        "value_type": "int",
        "description": "Size threshold (GB) to enable dual-GPU tensor split",
        "default": 6,
        "minimum": 0,
        "maximum": 1024,
    },
    {
        "field": "llama_main_port",
        "key": "llama.main.port",
        "label": "Llama main port",
        "value_type": "int",
        "description": "Main llama-server port",
        "default": 8080,
        "minimum": 1,
        "maximum": 65535,
    },
    {
        "field": "llama_title_port",
        "key": "llama.title.port",
        "label": "Llama title port",
        "value_type": "int",
        "description": "Title llama-server port",
        "default": 8081,
        "minimum": 1,
        "maximum": 65535,
    },
]


class InstallerError(Exception):
    pass


def get_install_sql_paths():
    return {
        "schema": SCHEMA_SQL_PATH,
        "seed": SEED_SQL_PATH,
    }


def get_installer_review_defaults():
    defaults = {}
    for field in INSTALLER_REVIEW_FIELDS:
        defaults[field["field"]] = field["default"]
    return defaults


def connect_mysql_server(db_host: str, db_user: str, db_password: str, database: str = None, db_port: int = 3306):
    kwargs = {
        "host": db_host,
        "port": int(db_port or 3306),
        "user": db_user,
        "password": db_password,
        "connection_timeout": 10,
        "use_pure": True,
    }
    if database:
        kwargs["database"] = database
    return mysql.connector.connect(**kwargs)


def load_sql_file(path: str) -> str:
    if not os.path.isfile(path):
        raise InstallerError(f"Required SQL file is missing: {path}")
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def render_sql_for_database(sql_text: str, db_name: str) -> str:
    database_identifier = db_name.replace("`", "``")
    rendered = sql_text.lstrip("\ufeff")
    rendered = re.sub(
        r"CREATE DATABASE IF NOT EXISTS\s+`?[^`\s;]+`?",
        f"CREATE DATABASE IF NOT EXISTS `{database_identifier}`",
        rendered,
        count=1,
        flags=re.IGNORECASE,
    )
    rendered = re.sub(
        r"\bUSE\s+`?[^`\s;]+`?",
        f"USE `{database_identifier}`",
        rendered,
        count=1,
        flags=re.IGNORECASE,
    )
    return rendered


def split_sql_statements(sql_text: str):
    text = re.sub(r"/\*.*?\*/", "", sql_text, flags=re.DOTALL)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--") or stripped.startswith("#"):
            continue
        lines.append(line)
    text = "\n".join(lines)

    statements = []
    buffer = []
    in_single = False
    in_double = False
    escaped = False

    for ch in text:
        if escaped:
            buffer.append(ch)
            escaped = False
            continue

        if ch == "\\" and (in_single or in_double):
            buffer.append(ch)
            escaped = True
            continue

        if ch == "'" and not in_double:
            in_single = not in_single
            buffer.append(ch)
            continue

        if ch == '"' and not in_single:
            in_double = not in_double
            buffer.append(ch)
            continue

        if ch == ";" and not in_single and not in_double:
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            continue

        buffer.append(ch)

    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


def _split_sql_items(sql_text: str):
    items = []
    buffer = []
    in_single = False
    in_double = False
    escaped = False
    depth = 0

    for ch in sql_text:
        if escaped:
            buffer.append(ch)
            escaped = False
            continue

        if ch == "\\" and (in_single or in_double):
            buffer.append(ch)
            escaped = True
            continue

        if ch == "'" and not in_double:
            in_single = not in_single
            buffer.append(ch)
            continue

        if ch == '"' and not in_single:
            in_double = not in_double
            buffer.append(ch)
            continue

        if not in_single and not in_double:
            if ch == "(":
                depth += 1
            elif ch == ")" and depth > 0:
                depth -= 1
            elif ch == "," and depth == 0:
                item = "".join(buffer).strip()
                if item:
                    items.append(item)
                buffer = []
                continue

        buffer.append(ch)

    tail = "".join(buffer).strip()
    if tail:
        items.append(tail)
    return items


def _make_insert_ignore(statement: str) -> str:
    return re.sub(r"^INSERT\s+INTO\b", "INSERT IGNORE INTO", statement, count=1, flags=re.IGNORECASE)


def _make_create_table_if_not_exists(statement: str) -> str:
    if re.match(r"^\s*CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\b", statement, flags=re.IGNORECASE):
        return statement
    return re.sub(r"^\s*CREATE\s+TABLE\b", "CREATE TABLE IF NOT EXISTS", statement, count=1, flags=re.IGNORECASE)


def _extract_create_table_name(statement: str):
    match = re.search(r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+`([^`]+)`", statement, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _extract_alter_table_name(statement: str):
    match = re.match(r"ALTER\s+TABLE\s+`([^`]+)`\s+(.+)$", statement, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None, None
    return match.group(1), match.group(2).strip()


def _extract_parenthesized_body(statement: str):
    start = statement.find("(")
    if start < 0:
        return ""

    depth = 0
    in_single = False
    in_double = False
    escaped = False

    for index in range(start, len(statement)):
        ch = statement[index]

        if escaped:
            escaped = False
            continue

        if ch == "\\" and (in_single or in_double):
            escaped = True
            continue

        if ch == "'" and not in_double:
            in_single = not in_single
            continue

        if ch == '"' and not in_single:
            in_double = not in_double
            continue

        if in_single or in_double:
            continue

        if ch == "(":
            depth += 1
            continue

        if ch == ")":
            depth -= 1
            if depth == 0:
                return statement[start + 1:index]

    return ""


def _iter_create_table_columns(statement: str):
    body = _extract_parenthesized_body(statement)
    if not body:
        return []

    columns = []
    for item in _split_sql_items(body):
        stripped = item.strip()
        if not stripped.startswith("`"):
            continue
        match = re.match(r"`([^`]+)`\s+(.+)$", stripped, flags=re.DOTALL)
        if not match:
            continue
        columns.append((match.group(1), stripped))
    return columns


def _get_existing_columns(cursor, db_name: str, table_name: str):
    cursor.execute(
        """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
        """,
        (db_name, table_name),
    )
    return {row[0] for row in cursor.fetchall() or []}


def _get_existing_indexes(cursor, db_name: str, table_name: str):
    cursor.execute(
        """
        SELECT INDEX_NAME
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
        """,
        (db_name, table_name),
    )
    return {row[0] for row in cursor.fetchall() or []}


def _get_existing_foreign_keys(cursor, db_name: str, table_name: str):
    cursor.execute(
        """
        SELECT CONSTRAINT_NAME
        FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
        WHERE CONSTRAINT_SCHEMA=%s AND TABLE_NAME=%s AND CONSTRAINT_TYPE='FOREIGN KEY'
        """,
        (db_name, table_name),
    )
    return {row[0] for row in cursor.fetchall() or []}


def _quote_identifier(identifier: str) -> str:
    return f"`{str(identifier).replace('`', '``')}`"


def _is_duplicate_schema_error(exc: Exception) -> bool:
    if not isinstance(exc, mysql.connector.Error):
        return False
    return getattr(exc, "errno", None) in {1050, 1060, 1061, 1068, 1826}


def _execute_schema_statement(cursor, statement: str):
    try:
        cursor.execute(statement)
    except Exception as exc:
        if _is_duplicate_schema_error(exc):
            return
        raise


def _sync_create_table(cursor, statement: str, db_name: str):
    table_name = _extract_create_table_name(statement)
    _execute_schema_statement(cursor, _make_create_table_if_not_exists(statement))
    if not table_name:
        return

    existing_columns = _get_existing_columns(cursor, db_name, table_name)
    table_sql = _quote_identifier(table_name)
    for column_name, column_definition in _iter_create_table_columns(statement):
        if column_name in existing_columns:
            continue
        _execute_schema_statement(cursor, f"ALTER TABLE {table_sql} ADD COLUMN {column_definition}")
        existing_columns.add(column_name)


def _sync_alter_table(cursor, statement: str, db_name: str):
    table_name, operations_text = _extract_alter_table_name(statement)
    if not table_name or not operations_text:
        _execute_schema_statement(cursor, statement)
        return

    table_sql = _quote_identifier(table_name)
    existing_indexes = None
    existing_foreign_keys = None

    for operation in _split_sql_items(operations_text):
        op = operation.strip()
        if not op:
            continue

        upper_op = op.upper()
        if upper_op.startswith("ADD PRIMARY KEY"):
            if existing_indexes is None:
                existing_indexes = _get_existing_indexes(cursor, db_name, table_name)
            if "PRIMARY" in existing_indexes:
                continue
            _execute_schema_statement(cursor, f"ALTER TABLE {table_sql} {op}")
            existing_indexes.add("PRIMARY")
            continue

        index_match = re.match(r"ADD\s+(?:UNIQUE\s+)?KEY\s+`([^`]+)`", op, flags=re.IGNORECASE)
        if index_match:
            if existing_indexes is None:
                existing_indexes = _get_existing_indexes(cursor, db_name, table_name)
            index_name = index_match.group(1)
            if index_name in existing_indexes:
                continue
            _execute_schema_statement(cursor, f"ALTER TABLE {table_sql} {op}")
            existing_indexes.add(index_name)
            continue

        fk_match = re.match(r"ADD\s+CONSTRAINT\s+`([^`]+)`", op, flags=re.IGNORECASE)
        if fk_match:
            if existing_foreign_keys is None:
                existing_foreign_keys = _get_existing_foreign_keys(cursor, db_name, table_name)
            constraint_name = fk_match.group(1)
            if constraint_name in existing_foreign_keys:
                continue
            _execute_schema_statement(cursor, f"ALTER TABLE {table_sql} {op}")
            existing_foreign_keys.add(constraint_name)
            continue

        _execute_schema_statement(cursor, f"ALTER TABLE {table_sql} {op}")


def execute_sql_script(connection, sql_text: str, db_name: str):
    statements = split_sql_statements(render_sql_for_database(sql_text, db_name))
    cursor = connection.cursor()
    try:
        for statement in statements:
            normalized = statement.strip()
            upper_statement = normalized.upper()

            if upper_statement.startswith("DROP TABLE"):
                continue

            if upper_statement.startswith("CREATE TABLE"):
                _sync_create_table(cursor, normalized, db_name)
            elif upper_statement.startswith("ALTER TABLE"):
                _sync_alter_table(cursor, normalized, db_name)
            elif upper_statement.startswith("INSERT INTO"):
                cursor.execute(_make_insert_ignore(normalized))
            else:
                cursor.execute(normalized)

            if getattr(cursor, "with_rows", False):
                cursor.fetchall()
        connection.commit()
    except Exception as exc:
        try:
            connection.rollback()
        except Exception:
            pass
        raise InstallerError(f"SQL import failed: {exc}") from exc
    finally:
        try:
            cursor.close()
        except Exception:
            pass


def validate_password_policy_level(level: str) -> str:
    normalized = str(level or "").strip().lower()
    if normalized not in PASSWORD_POLICY_PRESETS:
        raise InstallerError("Please choose a valid default password complexity.")
    return normalized


def validate_password_against_policy(password: str, policy_level: str):
    policy = PASSWORD_POLICY_PRESETS[validate_password_policy_level(policy_level)]

    if len(password or "") < int(policy["min_length"]):
        raise InstallerError(
            f"Initial admin password must be at least {int(policy['min_length'])} characters for the selected password policy."
        )
    if policy["require_upper"] and not any(ch.isupper() for ch in password):
        raise InstallerError("Initial admin password must include an uppercase letter.")
    if policy["require_lower"] and not any(ch.islower() for ch in password):
        raise InstallerError("Initial admin password must include a lowercase letter.")
    if policy["require_digit"] and not any(ch.isdigit() for ch in password):
        raise InstallerError("Initial admin password must include a digit.")
    if policy["require_special"] and not any(not ch.isalnum() for ch in password):
        raise InstallerError("Initial admin password must include a special character.")


def create_initial_admin(connection, admin_email: str, admin_password: str):
    normalized_email = admin_email.strip().lower()
    password_hash = generate_password_hash(admin_password, method="pbkdf2:sha256")
    password_hash = str(password_hash)

    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT id, role
            FROM llm_users
            WHERE email = %s
            LIMIT 1
            """,
            (normalized_email,),
        )
        existing_user = cursor.fetchone()
        if existing_user:
            if str(existing_user.get("role") or "").lower() == "admin":
                cursor.execute(
                    """
                    UPDATE llm_users
                       SET email_confirmed_at=COALESCE(email_confirmed_at, created_at, NOW()),
                           password_changed_at=COALESCE(password_changed_at, created_at, NOW()),
                           session_version=CASE
                               WHEN session_version IS NULL OR session_version < 1 THEN 1
                               ELSE session_version
                           END
                     WHERE id=%s
                    """,
                    (existing_user["id"],),
                )
                connection.commit()
                return int(existing_user["id"])
            raise InstallerError("Initial admin creation failed: that email address already belongs to a non-admin account.")

        cursor.execute(
            """
            INSERT INTO llm_users (
                email, password_hash, role, must_change_pw, temp_password,
                is_active, email_confirmed_at, password_changed_at, session_version
            )
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW(), %s)
            """,
            (normalized_email, password_hash, "admin", 0, None, 1, 1),
        )
        connection.commit()
        return cursor.lastrowid
    except Exception as exc:
        try:
            connection.rollback()
        except Exception:
            pass
        raise InstallerError(f"Initial admin creation failed: {exc}") from exc
    finally:
        try:
            cursor.close()
        except Exception:
            pass


def apply_password_policy(connection, policy_level: str, updated_by_user_id=None):
    normalized = validate_password_policy_level(policy_level)
    policy = PASSWORD_POLICY_PRESETS[normalized]

    rows = [
        ("security.password_policy.level", normalized, "string", "Password policy level: basic/moderate/strong/custom"),
        ("security.password_policy.min_length", str(int(policy["min_length"])), "int", "Minimum password length"),
        ("security.password_policy.require_upper", "1" if policy["require_upper"] else "0", "bool", "Require uppercase letter"),
        ("security.password_policy.require_lower", "1" if policy["require_lower"] else "0", "bool", "Require lowercase letter"),
        ("security.password_policy.require_digit", "1" if policy["require_digit"] else "0", "bool", "Require digit"),
        ("security.password_policy.require_special", "1" if policy["require_special"] else "0", "bool", "Require special character"),
    ]

    cursor = connection.cursor()
    try:
        for key, value, value_type, description in rows:
            cursor.execute(
                """
                INSERT INTO llm_app_settings (`key`, `value`, `value_type`, `description`, `updated_by_user_id`)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    `value` = VALUES(`value`),
                    `value_type` = VALUES(`value_type`),
                    `description` = VALUES(`description`),
                    `updated_by_user_id` = VALUES(`updated_by_user_id`),
                    `updated_at` = CURRENT_TIMESTAMP
                """,
                (key, value, value_type, description, updated_by_user_id),
            )
        connection.commit()
    except Exception as exc:
        try:
            connection.rollback()
        except Exception:
            pass
        raise InstallerError(f"Default password policy could not be saved: {exc}") from exc
    finally:
        try:
            cursor.close()
        except Exception:
            pass


def load_installer_review_settings(connection):
    defaults = get_installer_review_defaults()
    settings_by_key = {field["key"]: field for field in INSTALLER_REVIEW_FIELDS}
    placeholders = ",".join(["%s"] * len(INSTALLER_REVIEW_FIELDS))

    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            f"""
            SELECT `key`, `value`, `value_type`
            FROM llm_app_settings
            WHERE `key` IN ({placeholders})
            """,
            tuple(settings_by_key.keys()),
        )
        rows = cursor.fetchall() or []
    except Exception as exc:
        raise InstallerError(
            f"Step 2 is not available yet because seeded application settings could not be loaded: {exc}"
        ) from exc
    finally:
        try:
            cursor.close()
        except Exception:
            pass

    loaded = dict(defaults)
    for row in rows:
        meta = settings_by_key.get(row.get("key"))
        if not meta:
            continue
        raw_value = row.get("value")
        if meta["value_type"] == "int":
            try:
                loaded[meta["field"]] = int(raw_value)
            except Exception:
                loaded[meta["field"]] = int(meta["default"])
        else:
            loaded[meta["field"]] = str(raw_value or meta["default"])

    return loaded


def save_installer_review_settings(connection, values: dict, updated_by_user_id=None):
    cursor = connection.cursor()
    try:
        for field in INSTALLER_REVIEW_FIELDS:
            raw_value = values[field["field"]]
            if field["value_type"] == "int":
                value = str(int(raw_value))
            else:
                value = str(raw_value)

            cursor.execute(
                """
                INSERT INTO llm_app_settings (`key`, `value`, `value_type`, `description`, `updated_by_user_id`)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    `value` = VALUES(`value`),
                    `value_type` = VALUES(`value_type`),
                    `description` = VALUES(`description`),
                    `updated_by_user_id` = VALUES(`updated_by_user_id`),
                    `updated_at` = CURRENT_TIMESTAMP
                """,
                (
                    field["key"],
                    value,
                    field["value_type"],
                    field["description"],
                    updated_by_user_id,
                ),
            )
        connection.commit()
    except Exception as exc:
        try:
            connection.rollback()
        except Exception:
            pass
        raise InstallerError(f"Step 2 settings could not be saved: {exc}") from exc
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        