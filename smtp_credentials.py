import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from bootstrap_config import PersistentSecretKeyError, get_persistent_secret_key


SMTP_CREDENTIAL_PREFIX = "enc:v1:"
MAX_SMTP_PASSWORD_BYTES = 4096
_SMTP_KEY_CONTEXT = b"llm-controller-ce/smtp-password/v1"


class SmtpCredentialError(RuntimeError):
    pass


def _smtp_fernet() -> Fernet:
    try:
        root_key = get_persistent_secret_key().encode("utf-8")
    except (PersistentSecretKeyError, UnicodeError) as exc:
        raise SmtpCredentialError("Persistent SMTP credential key is unavailable.") from exc

    derived_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_SMTP_KEY_CONTEXT,
    ).derive(root_key)
    return Fernet(base64.urlsafe_b64encode(derived_key))


def encrypt_smtp_password(plaintext: str) -> str:
    value = str(plaintext or "")
    if not value:
        return ""

    try:
        plaintext_bytes = value.encode("utf-8")
    except UnicodeError as exc:
        raise SmtpCredentialError("SMTP password must be valid UTF-8 text.") from exc
    if len(plaintext_bytes) > MAX_SMTP_PASSWORD_BYTES:
        raise SmtpCredentialError("SMTP password exceeds the configured size limit.")

    token = _smtp_fernet().encrypt(plaintext_bytes).decode("ascii")
    return f"{SMTP_CREDENTIAL_PREFIX}{token}"


def decrypt_smtp_password(stored_value: str) -> str:
    value = str(stored_value or "")
    if not value:
        return ""
    if not value.startswith(SMTP_CREDENTIAL_PREFIX):
        raise SmtpCredentialError("Stored SMTP password is not encrypted.")

    token = value[len(SMTP_CREDENTIAL_PREFIX):]
    try:
        plaintext_bytes = _smtp_fernet().decrypt(token.encode("ascii"))
        plaintext = plaintext_bytes.decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise SmtpCredentialError("Stored SMTP password cannot be decrypted.") from exc

    if not plaintext or len(plaintext_bytes) > MAX_SMTP_PASSWORD_BYTES:
        raise SmtpCredentialError("Stored SMTP password is invalid.")
    return plaintext


def smtp_password_is_configured(stored_value: str) -> bool:
    return bool(decrypt_smtp_password(stored_value))
