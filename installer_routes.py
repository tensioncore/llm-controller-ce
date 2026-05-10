from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for
from flask_wtf.csrf import generate_csrf, validate_csrf

from bootstrap_config import (
    CORS_ALLOWED_ORIGINS_KEY,
    DEFAULT_BOOTSTRAP_CONFIG,
    derive_cors_allowed_origins,
    normalize_app_host,
    normalize_cors_origin,
    save_bootstrap_config,
)
from installer_service import (
    INSTALLER_REVIEW_FIELDS,
    InstallerError,
    PASSWORD_POLICY_OPTIONS,
    apply_password_policy,
    connect_mysql_server,
    create_initial_admin,
    execute_sql_script,
    get_install_sql_paths,
    get_installer_review_defaults,
    load_installer_review_settings,
    load_sql_file,
    save_installer_review_settings,
    validate_password_against_policy,
    validate_password_policy_level,
)


installer_routes = Blueprint("installer", __name__, url_prefix="/installer")


def _bootstrap_defaults():
    bootstrap_config = current_app.config.get("BOOTSTRAP_CONFIG", {})
    return {
        "app_host": bootstrap_config.get("app_host", DEFAULT_BOOTSTRAP_CONFIG["app_host"]),
        "app_port": str(bootstrap_config.get("app_port", DEFAULT_BOOTSTRAP_CONFIG["app_port"])),
        CORS_ALLOWED_ORIGINS_KEY: ", ".join(bootstrap_config.get(CORS_ALLOWED_ORIGINS_KEY, []) or []),
        "db_host": bootstrap_config.get("db_host", DEFAULT_BOOTSTRAP_CONFIG["db_host"]),
        "db_port": str(bootstrap_config.get("db_port", DEFAULT_BOOTSTRAP_CONFIG["db_port"])),
        "db_name": bootstrap_config.get("db_name", DEFAULT_BOOTSTRAP_CONFIG["db_name"]),
        "db_user": bootstrap_config.get("db_user", ""),
        "db_password": bootstrap_config.get("db_password", ""),
        "admin_email": "",
        "admin_password": "",
        "password_policy_level": "strong",
    }


def _merge_form_data(overrides=None):
    data = _bootstrap_defaults()
    if overrides:
        for key, value in overrides.items():
            data[key] = value
    return data


def _render_step_one(form_data=None, errors=None, installer_error=None):
    bootstrap_config = current_app.config.get("BOOTSTRAP_CONFIG", {})
    install_paths = get_install_sql_paths()
    return render_template(
        "installer_placeholder.html",
        csrf_token=generate_csrf(),
        bootstrap_config_path=current_app.config.get("BOOTSTRAP_CONFIG_PATH"),
        app_host=bootstrap_config.get("app_host", "127.0.0.1"),
        app_port=bootstrap_config.get("app_port", 5000),
        setup_complete=bool(current_app.config.get("SETUP_COMPLETE")),
        form_data=_merge_form_data(form_data),
        errors=errors or {},
        installer_error=installer_error,
        password_policy_options=PASSWORD_POLICY_OPTIONS,
        schema_sql_path=install_paths["schema"],
        seed_sql_path=install_paths["seed"],
    )


def _connect_bootstrap_database():
    bootstrap_config = current_app.config.get("BOOTSTRAP_CONFIG", {})
    if not bootstrap_config:
        raise InstallerError("Bootstrap configuration is missing. Complete step 1 first.")

    try:
        return connect_mysql_server(
            bootstrap_config.get("db_host", ""),
            bootstrap_config.get("db_user", ""),
            bootstrap_config.get("db_password", ""),
            database=bootstrap_config.get("db_name", ""),
            db_port=bootstrap_config.get("db_port", DEFAULT_BOOTSTRAP_CONFIG["db_port"]),
        )
    except Exception as exc:
        raise InstallerError(
            f"Step 2 is only available after step 1 succeeds and the configured database can be opened: {exc}"
        ) from exc


def _review_form_defaults():
    defaults = get_installer_review_defaults()
    return {key: str(value) for key, value in defaults.items()}


def _render_step_two(form_data=None, errors=None, installer_error=None):
    bootstrap_config = current_app.config.get("BOOTSTRAP_CONFIG", {})
    values = _review_form_defaults()
    if form_data:
        values.update(form_data)

    return render_template(
        "installer_step2_placeholder.html",
        csrf_token=generate_csrf(),
        bootstrap_config_path=current_app.config.get("BOOTSTRAP_CONFIG_PATH"),
        bootstrap_config=bootstrap_config,
        setup_complete=bool(current_app.config.get("SETUP_COMPLETE")),
        form_data=values,
        errors=errors or {},
        installer_error=installer_error,
        review_fields=INSTALLER_REVIEW_FIELDS,
    )


def _get_installer_csrf_error():
    try:
        validate_csrf(request.form.get("csrf_token"))
    except Exception:
        return "The setup form expired or was rejected. Refresh the page and try again."
    return None


@installer_routes.route("/", methods=["GET"], strict_slashes=False)
def index():
    if current_app.config.get("SETUP_COMPLETE"):
        return redirect(url_for("index"))
    return _render_step_one()


@installer_routes.route("/bootstrap", methods=["POST"])
def bootstrap_submit():
    if current_app.config.get("SETUP_COMPLETE"):
        return redirect(url_for("index"))

    raw_form = {key: (value or "").strip() for key, value in request.form.items()}
    if raw_form.get("public_origin") and not raw_form.get(CORS_ALLOWED_ORIGINS_KEY):
        raw_form[CORS_ALLOWED_ORIGINS_KEY] = raw_form.get("public_origin", "")
    form_data = _merge_form_data(raw_form)
    form_data["admin_email"] = str(form_data.get("admin_email") or "").strip().lower()
    errors = {}

    app_host = normalize_app_host(form_data.get("app_host", ""), DEFAULT_BOOTSTRAP_CONFIG["app_host"])
    form_data["app_host"] = app_host or DEFAULT_BOOTSTRAP_CONFIG["app_host"]

    csrf_error = _get_installer_csrf_error()
    if csrf_error:
        form_data["admin_password"] = ""
        return _render_step_one(form_data=form_data, installer_error=csrf_error), 400

    required_fields = {
        "app_port": "App port is required.",
        "db_host": "Database host is required.",
        "db_name": "Database name is required.",
        "db_user": "Database username is required.",
        "admin_email": "Initial admin email is required.",
        "admin_password": "Initial admin password is required.",
    }

    for field, message in required_fields.items():
        if not form_data.get(field):
            errors[field] = message

    app_port = None
    if form_data.get("app_port"):
        try:
            app_port = int(form_data["app_port"])
            if not (1 <= app_port <= 65535):
                raise ValueError()
        except Exception:
            errors["app_port"] = "App port must be a number between 1 and 65535."

    db_port = DEFAULT_BOOTSTRAP_CONFIG["db_port"]
    if form_data.get("db_port"):
        try:
            db_port = int(form_data["db_port"])
            if not (1 <= db_port <= 65535):
                raise ValueError()
            form_data["db_port"] = str(db_port)
        except Exception:
            errors["db_port"] = "MySQL port must be a number between 1 and 65535."
    else:
        form_data["db_port"] = str(db_port)

    try:
        password_policy_level = validate_password_policy_level(form_data.get("password_policy_level"))
    except InstallerError as exc:
        errors["password_policy_level"] = str(exc)
        password_policy_level = "strong"

    admin_email = form_data.get("admin_email", "")
    if admin_email:
        if "@" not in admin_email or "." not in admin_email.split("@", 1)[-1]:
            errors["admin_email"] = "Initial admin email must be a valid email address."

    configured_cors_origins = []
    raw_cors_allowed_origins = str(form_data.get(CORS_ALLOWED_ORIGINS_KEY) or "").strip()
    if raw_cors_allowed_origins:
        for raw_origin in raw_cors_allowed_origins.split(","):
            trimmed = str(raw_origin or "").strip()
            if not trimmed:
                continue
            normalized_origin = normalize_cors_origin(trimmed)
            if not normalized_origin:
                errors[CORS_ALLOWED_ORIGINS_KEY] = (
                    "CORS accepted origins must be comma-separated browser origins like "
                    "http://example.com or https://example.com."
                )
                break
            configured_cors_origins.append(normalized_origin)

    if configured_cors_origins:
        form_data[CORS_ALLOWED_ORIGINS_KEY] = ", ".join(configured_cors_origins)
    elif raw_cors_allowed_origins:
        form_data[CORS_ALLOWED_ORIGINS_KEY] = raw_cors_allowed_origins

    if not errors and form_data.get("admin_password"):
        try:
            validate_password_against_policy(form_data["admin_password"], password_policy_level)
        except InstallerError as exc:
            errors["admin_password"] = str(exc)

    if errors:
        form_data["admin_password"] = ""
        return _render_step_one(form_data=form_data, errors=errors), 400

    bootstrap_payload = {
        "app_host": form_data["app_host"],
        "app_port": app_port,
        "db_host": form_data["db_host"],
        "db_port": db_port,
        "db_name": form_data["db_name"],
        "db_user": form_data["db_user"],
        "db_password": form_data.get("db_password", ""),
        CORS_ALLOWED_ORIGINS_KEY: derive_cors_allowed_origins(
            form_data["app_host"],
            app_port,
            extra_origins=configured_cors_origins,
        ),
        "setup_complete": False,
    }

    admin_email = form_data["admin_email"]
    admin_password = form_data["admin_password"]

    server_connection = None
    database_connection = None

    try:
        try:
            server_connection = connect_mysql_server(
                form_data["db_host"],
                form_data["db_user"],
                form_data.get("db_password", ""),
                db_port=db_port,
            )
        except Exception as exc:
            raise InstallerError(
                f"Could not connect to MySQL with the provided host, username, and password: {exc}"
            ) from exc

        saved_config = save_bootstrap_config(bootstrap_payload)
        current_app.config["BOOTSTRAP_CONFIG"] = saved_config

        install_paths = get_install_sql_paths()
        schema_sql = load_sql_file(install_paths["schema"])
        execute_sql_script(server_connection, schema_sql, form_data["db_name"])

        try:
            database_connection = connect_mysql_server(
                form_data["db_host"],
                form_data["db_user"],
                form_data.get("db_password", ""),
                database=form_data["db_name"],
                db_port=db_port,
            )
        except Exception as exc:
            raise InstallerError(
                f"The database '{form_data['db_name']}' could not be opened after schema import: {exc}"
            ) from exc

        seed_sql = load_sql_file(install_paths["seed"])
        execute_sql_script(database_connection, seed_sql, form_data["db_name"])

        admin_user_id = create_initial_admin(database_connection, admin_email, admin_password)
        apply_password_policy(
            database_connection,
            password_policy_level,
            updated_by_user_id=admin_user_id,
        )

    except InstallerError as exc:
        form_data["admin_password"] = ""
        return _render_step_one(form_data=form_data, installer_error=str(exc)), 400
    except Exception as exc:
        form_data["admin_password"] = ""
        return _render_step_one(
            form_data=form_data,
            installer_error=f"Installer setup failed unexpectedly: {exc}",
        ), 500
    finally:
        try:
            if database_connection is not None:
                database_connection.close()
        except Exception:
            pass
        try:
            if server_connection is not None:
                server_connection.close()
        except Exception:
            pass

    return redirect(url_for("installer.step_two"))


@installer_routes.route("/step-2", methods=["GET", "POST"])
def step_two():
    if current_app.config.get("SETUP_COMPLETE"):
        return redirect(url_for("index"))

    database_connection = None
    try:
        database_connection = _connect_bootstrap_database()
    except InstallerError as exc:
        return _render_step_one(installer_error=str(exc)), 400

    try:
        if request.method == "GET":
            loaded_values = load_installer_review_settings(database_connection)
            form_data = {key: str(value) for key, value in loaded_values.items()}
            return _render_step_two(form_data=form_data)

        raw_form = {key: (value or "").strip() for key, value in request.form.items()}
        csrf_error = _get_installer_csrf_error()
        if csrf_error:
            return _render_step_two(form_data=raw_form, installer_error=csrf_error), 400

        errors = {}
        cleaned_values = {}

        for field in INSTALLER_REVIEW_FIELDS:
            form_value = raw_form.get(field["field"], "")
            if not form_value:
                errors[field["field"]] = f"{field['label']} is required."
                continue

            if field["value_type"] == "int":
                try:
                    parsed = int(form_value)
                    minimum = field.get("minimum")
                    maximum = field.get("maximum")
                    if minimum is not None and parsed < int(minimum):
                        raise ValueError()
                    if maximum is not None and parsed > int(maximum):
                        raise ValueError()
                    cleaned_values[field["field"]] = parsed
                except Exception:
                    minimum = field.get("minimum")
                    maximum = field.get("maximum")
                    if minimum is not None and maximum is not None:
                        errors[field["field"]] = f"{field['label']} must be a number between {minimum} and {maximum}."
                    else:
                        errors[field["field"]] = f"{field['label']} must be a valid number."
            else:
                cleaned_values[field["field"]] = form_value

        if errors:
            return _render_step_two(form_data=raw_form, errors=errors), 400

        save_installer_review_settings(database_connection, cleaned_values)

        bootstrap_config = dict(current_app.config.get("BOOTSTRAP_CONFIG", {}))
        bootstrap_config["setup_complete"] = True
        saved_config = save_bootstrap_config(bootstrap_config)

        current_app.config["BOOTSTRAP_CONFIG"] = saved_config
        current_app.config["SETUP_COMPLETE"] = True

        return redirect(url_for("index"))

    except InstallerError as exc:
        form_data = raw_form if request.method == "POST" else None
        return _render_step_two(form_data=form_data, installer_error=str(exc)), 400
    except Exception as exc:
        form_data = raw_form if request.method == "POST" else None
        return _render_step_two(
            form_data=form_data,
            installer_error=f"Step 2 save failed unexpectedly: {exc}",
        ), 500
    finally:
        try:
            if database_connection is not None:
                database_connection.close()
        except Exception:
            pass


@installer_routes.route("/status", methods=["GET"])
def status():
    bootstrap_config = current_app.config.get("BOOTSTRAP_CONFIG", {})
    return jsonify(
        {
            "installer_mode": bool(current_app.config.get("INSTALLER_MODE")),
            "setup_complete": bool(current_app.config.get("SETUP_COMPLETE")),
            "bootstrap_config_path": current_app.config.get("BOOTSTRAP_CONFIG_PATH"),
            "app_host": bootstrap_config.get("app_host", DEFAULT_BOOTSTRAP_CONFIG["app_host"]),
            "app_port": bootstrap_config.get("app_port", DEFAULT_BOOTSTRAP_CONFIG["app_port"]),
            "db_port": bootstrap_config.get("db_port", DEFAULT_BOOTSTRAP_CONFIG["db_port"]),
            "next_step_url": url_for("installer.step_two"),
        }
    )
