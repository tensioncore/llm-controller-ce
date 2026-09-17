import os

from app_settings import get_setting


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _coerce_int(value, *, key, minimum=None, maximum=None):
    try:
        parsed = int(value)
    except Exception:
        raise RuntimeError(f"Missing or invalid required setting: {key}")

    if minimum is not None and parsed < int(minimum):
        raise RuntimeError(f"Invalid required setting: {key}")
    if maximum is not None and parsed > int(maximum):
        raise RuntimeError(f"Invalid required setting: {key}")
    return parsed


def get_scan_directory_setting() -> str:
    raw = get_setting("llm.scan_directory")
    value = str(raw or "").strip()
    if not value:
        raise RuntimeError("Missing required setting: llm.scan_directory")
    return value


def get_chat_import_max_mib() -> int:
    return _coerce_int(
        get_setting("llm.chat_import.max_mib", default=64),
        key="llm.chat_import.max_mib",
        minimum=10,
        maximum=1024,
    )


def resolve_scan_directory_path(scan_directory=None) -> str:
    raw_value = get_scan_directory_setting() if scan_directory is None else str(scan_directory or "").strip()
    if not raw_value:
        raise RuntimeError("Missing required setting: llm.scan_directory")
    if os.path.isabs(raw_value):
        return os.path.normpath(raw_value)
    return os.path.normpath(os.path.join(BASE_DIR, raw_value))


def validate_scan_directory_value(scan_directory):
    raw_value = str(scan_directory or "").strip()
    if not raw_value:
        return False, "Required"

    if os.path.isabs(raw_value):
        return True, None

    normalized = os.path.normcase(resolve_scan_directory_path(raw_value))
    base_dir = os.path.normcase(os.path.normpath(BASE_DIR))
    if normalized == base_dir or normalized.startswith(base_dir + os.sep):
        return True, None

    return False, f"Relative scan directory must stay inside {BASE_DIR}"


def get_llama_main_port() -> int:
    return _coerce_int(
        get_setting("llama.main.port", cast=int),
        key="llama.main.port",
        minimum=1,
        maximum=65535,
    )


def get_llama_title_port() -> int:
    return _coerce_int(
        get_setting("llama.title.port", cast=int),
        key="llama.title.port",
        minimum=1,
        maximum=65535,
    )
