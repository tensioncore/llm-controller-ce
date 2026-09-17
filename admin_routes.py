import re
import sqlite3

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, g
from helpers import DB_PATH, get_password_settings, generate_temp_password
from auth import login_required
from flask_bcrypt import generate_password_hash

from db_mysql import mysql_conn

admin = Blueprint('admin', __name__)

ADMIN_EMAIL_RE = re.compile(
    r"^[a-z0-9._%+\-]+@[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?)+$"
)
MAX_ADMIN_EMAIL_LENGTH = 254

def get_db():
    """
    Request-scoped MySQL connection.
    """
    if 'db' not in g:
        try:
            g.db = mysql_conn()
        except Exception as e:
            print("MySQL connect error:", e)
            g.db = None
    return g.db


def _purge_user_chats(user_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM chat_sessions WHERE user_id=?", (user_id,))
        cursor.execute("DELETE FROM projects WHERE user_id=?", (user_id,))
        cursor.execute("DELETE FROM chats WHERE user_id=?", (user_id,))
        deleted_rows = cursor.rowcount
        conn.commit()
        return deleted_rows
    finally:
        conn.close()

def _normalize_admin_email(raw_email):
    email = str(raw_email or "").strip().lower()
    if not email:
        return "", "Email is required."
    if len(email) > MAX_ADMIN_EMAIL_LENGTH:
        return "", "Email is too long."
    if not ADMIN_EMAIL_RE.match(email):
        return "", "Enter a valid email address."
    local_part = email.split("@", 1)[0]
    if local_part.startswith(".") or local_part.endswith(".") or ".." in local_part:
        return "", "Enter a valid email address."
    return email, None

@admin.route('/admin/users')
@login_required(role='admin')
def admin_users():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute(
        "SELECT id, email, role, is_active, created_at, last_login, temp_password FROM llm_users"
    )
    users = cursor.fetchall()

    settings = get_password_settings()

    return render_template('admin_users.html', users=users, settings=settings)

@admin.route('/admin/users/delete/<int:user_id>', methods=['POST'])
@login_required(role='admin')
def delete_user(user_id):
    db = get_db()
    cursor = db.cursor()
    chat_action = str(request.form.get('chat_action', 'retain') or 'retain').strip().lower()
    if chat_action not in {'retain', 'purge'}:
        chat_action = 'retain'

    # Don't allow admin self-delete
    if user_id == session.get('user_id'):
        flash("You can't delete yourself.", "error")
        return redirect(url_for('admin.admin_users'))

    cursor.execute("DELETE FROM llm_users WHERE id=%s AND role!='admin'", (user_id,))
    deleted_users = cursor.rowcount
    db.commit()

    if deleted_users == 0:
        flash("User could not be deleted.", "error")
        return redirect(url_for('admin.admin_users'))

    if chat_action == 'purge':
        try:
            deleted_rows = _purge_user_chats(user_id)
            flash(f"User deleted and chats purged ({deleted_rows} rows).", "success")
        except Exception as e:
            print("Failed to purge user chats:", e)
            flash("User deleted, but chat purge failed. Chats were retained.", "error")
    else:
        flash("User deleted. Chats retained.", "success")

    return redirect(url_for('admin.admin_users'))

@admin.route('/admin/users/add', methods=['POST'])
@login_required(role='admin')
def add_user():
    db = get_db()
    cursor = db.cursor()

    email, email_error = _normalize_admin_email(request.form.get('email', ''))
    if email_error:
        flash(email_error, "error")
        return redirect(url_for('admin.admin_users'))

    role = request.form.get('role', 'user').strip().lower()
    if role != 'user':
        flash("Only the user role is available in this release.", "error")
        return redirect(url_for('admin.admin_users'))

    settings = get_password_settings()
    temp_pw = generate_temp_password(settings)
    pw_hash = generate_password_hash(temp_pw).decode('utf-8')

    try:
        # is_active is the account-enabled flag.
        cursor.execute(
            """
            INSERT INTO llm_users (
                email, password_hash, role, must_change_pw, temp_password,
                is_active, email_confirmed_at, session_version
            )
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s)
            """,
            (email, pw_hash, role, True, temp_pw, 1, 1)
        )
        db.commit()

        flash(
            "User created. The temporary password is shown in the user table until the user changes it.",
            "success"
        )
    except Exception as e:
        flash(f"Error: {e}", "error")

    return redirect(url_for('admin.admin_users'))
