import base64
import binascii
import json
import mimetypes
import os
import re


ATTACHMENT_ENVELOPE_TYPE = "llm-controller-attachments"
ATTACHMENT_ENVELOPE_VERSION = 1
ATTACHMENT_ENVELOPE_MAX_FILES = 1000

TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".py", ".js", ".ts", ".html", ".css", ".json", ".xml",
    ".yaml", ".yml", ".csv", ".log", ".ini", ".cfg", ".bat", ".ps1", ".sh",
    ".sql", ".php", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs",
})
IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
IMAGE_EXTENSION_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
TEXT_DEFAULT_MIME_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".ts": "text/plain",
    ".html": "text/html",
    ".css": "text/css",
    ".json": "application/json",
    ".xml": "application/xml",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".csv": "text/csv",
    ".log": "text/plain",
    ".ini": "text/plain",
    ".cfg": "text/plain",
    ".bat": "text/plain",
    ".ps1": "text/plain",
    ".sh": "text/x-shellscript",
    ".sql": "application/sql",
    ".php": "text/x-php",
    ".java": "text/x-java-source",
    ".c": "text/x-c",
    ".cpp": "text/x-c++",
    ".h": "text/x-c",
    ".cs": "text/plain",
    ".go": "text/plain",
    ".rs": "text/plain",
}
SUPPORTED_ATTACHMENT_EXTENSIONS = TEXT_EXTENSIONS.union(IMAGE_EXTENSIONS)
SAFE_APPLICATION_TEXT_MIME_TYPES = frozenset({
    "application/json",
    "application/javascript",
    "application/sql",
    "application/typescript",
    "application/xml",
    "application/x-httpd-php",
    "application/x-javascript",
    "application/x-sh",
    "application/x-yaml",
    "application/yaml",
})
DATA_URL_RE = re.compile(
    r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=_-]+)$",
    re.IGNORECASE,
)
LEGACY_ATTACHMENT_NAME_RE = re.compile(r"^\[Attached file:\s*(.+?)\]\s*$")
LEGACY_ATTACHMENT_INDEX_RE = re.compile(r"^\[Attachment\s+(\d+)/(\d+)\]\s*$")


def attachment_extension(filename):
    return os.path.splitext(str(filename or "").strip().lower())[1]


def sanitize_attachment_name(filename):
    raw = str(filename or "").replace("\x00", "").strip().replace("\\", "/")
    safe = raw.rsplit("/", 1)[-1].strip()
    safe = "".join(ch for ch in safe if ch >= " " and ch != "\x7f")
    safe = safe.encode("utf-8", errors="ignore").decode("utf-8")
    if safe in ("", ".", ".."):
        return ""
    return safe[:255]


def _normalize_mime_type(value):
    return str(value or "").split(";", 1)[0].strip().lower()


def infer_mime_type(filename, supplied=None):
    supplied_value = _normalize_mime_type(supplied)
    if supplied_value:
        return supplied_value
    extension = attachment_extension(filename)
    if extension in IMAGE_EXTENSION_TO_MIME:
        return IMAGE_EXTENSION_TO_MIME[extension]
    if extension in TEXT_DEFAULT_MIME_TYPES:
        return TEXT_DEFAULT_MIME_TYPES[extension]
    return mimetypes.guess_type(filename)[0] or "text/plain"


def _validate_text_mime_type(filename, supplied):
    supplied_value = _normalize_mime_type(supplied)
    if supplied_value == "application/octet-stream":
        supplied_value = ""
    if supplied_value and not (
        supplied_value.startswith("text/")
        or supplied_value in SAFE_APPLICATION_TEXT_MIME_TYPES
    ):
        raise ValueError(f"File type does not match filename: {filename}")
    return supplied_value or infer_mime_type(filename)


def _image_magic_matches(mime_type, data):
    if mime_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/jpeg":
        return len(data) >= 3 and data[:3] == b"\xff\xd8\xff"
    if mime_type == "image/webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def decode_image_data_url(data_url, max_bytes):
    max_bytes = max(1, int(max_bytes))
    raw = str(data_url or "").strip()
    match = DATA_URL_RE.fullmatch(raw)
    if not match:
        raise ValueError("Images must be supplied as PNG, JPEG, or WebP data URLs.")
    mime_type = match.group(1).lower()
    encoded = match.group(2).replace("-", "+").replace("_", "/")
    max_encoded_chars = ((max_bytes + 2) // 3) * 4 + 4
    if len(encoded) > max_encoded_chars:
        raise ValueError("The image exceeds the per-file attachment limit.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("The image data was not valid base64.") from exc
    if not data:
        raise ValueError("The image is empty.")
    if len(data) > max_bytes:
        raise ValueError("The image exceeds the per-file attachment limit.")
    if not _image_magic_matches(mime_type, data):
        raise ValueError("The image contents do not match the declared image type.")
    return mime_type, data


def image_data_url(mime_type, data):
    mime = _normalize_mime_type(mime_type)
    if mime not in IMAGE_MIME_TYPES:
        raise ValueError("Unsupported image type.")
    return f"data:{mime};base64,{base64.b64encode(data or b'').decode('ascii')}"


def _declared_attachment_size(attachment, filename, max_file_bytes):
    raw_size = attachment.get("size")
    if raw_size is None or raw_size == "":
        return None
    try:
        value = int(raw_size)
    except Exception as exc:
        raise ValueError(f"Invalid file size for {filename}.") from exc
    if value < 0:
        raise ValueError(f"Invalid file size for {filename}.")
    if value > int(max_file_bytes):
        raise ValueError(f"{filename} exceeds the per-file attachment limit.")
    return value


def normalize_browser_attachments(raw_attachments, max_files, max_file_bytes, max_total_bytes):
    attachments = [] if raw_attachments is None else raw_attachments
    if not isinstance(attachments, list):
        raise ValueError("Invalid attachment payload.")

    max_files = max(1, int(max_files))
    max_file_bytes = max(1, int(max_file_bytes))
    max_total_bytes = max(1, int(max_total_bytes))
    if len(attachments) > max_files:
        raise ValueError(f"You can attach up to {max_files} files per message.")

    normalized = []
    total_bytes = 0
    for attachment in attachments:
        if not isinstance(attachment, dict):
            raise ValueError("Invalid attachment payload.")

        filename = sanitize_attachment_name(attachment.get("name"))
        extension = attachment_extension(filename)
        if not filename or extension not in SUPPORTED_ATTACHMENT_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {filename or 'unnamed attachment'}")

        supplied_kind = str(attachment.get("kind") or "").strip().lower()
        if supplied_kind == "file":
            supplied_kind = "text"
        inferred_kind = "image" if extension in IMAGE_EXTENSIONS else "text"
        if supplied_kind and supplied_kind != inferred_kind:
            raise ValueError(f"File type does not match filename: {filename}")
        kind = inferred_kind

        supplied_mime = attachment.get("mime_type")
        if supplied_mime is None:
            supplied_mime = attachment.get("type")
        _declared_attachment_size(attachment, filename, max_file_bytes)

        if kind == "image":
            expected_mime = IMAGE_EXTENSION_TO_MIME[extension]
            declared_mime = _normalize_mime_type(supplied_mime)
            if declared_mime and declared_mime != expected_mime:
                raise ValueError(f"Image type does not match filename: {filename}")
            mime_type, data = decode_image_data_url(attachment.get("data_url"), max_file_bytes)
            if mime_type != expected_mime:
                raise ValueError(f"Image type does not match filename: {filename}")
            file_size = len(data)
            normalized_attachment = {
                "kind": "image",
                "name": filename,
                "mime_type": mime_type,
                "size": file_size,
                "data_url": image_data_url(mime_type, data),
            }
        else:
            content = attachment.get("content")
            if not isinstance(content, str) or not content:
                raise ValueError(f"Could not read {filename} as text.")
            if "\x00" in content:
                raise ValueError(f"{filename} looks like a binary file and can't be used here.")
            try:
                data = content.encode("utf-8")
            except UnicodeError as exc:
                raise ValueError(f"Could not read {filename} as valid UTF-8 text.") from exc
            file_size = len(data)
            if file_size > max_file_bytes:
                raise ValueError(f"{filename} exceeds the per-file attachment limit.")
            mime_type = _validate_text_mime_type(filename, supplied_mime)
            normalized_attachment = {
                "kind": "text",
                "name": filename,
                "mime_type": mime_type,
                "size": file_size,
                "content": content,
            }

        if file_size <= 0:
            raise ValueError(f"{filename} is empty.")
        total_bytes += file_size
        if total_bytes > max_total_bytes:
            raise ValueError("Total attachment size exceeds the configured limit.")
        normalized.append(normalized_attachment)

    return normalized


def serialize_attachment_envelope(attachments):
    if not attachments:
        return ""
    return json.dumps(
        {
            "type": ATTACHMENT_ENVELOPE_TYPE,
            "version": ATTACHMENT_ENVELOPE_VERSION,
            "attachments": attachments,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def parse_attachment_context(raw_context, max_files, max_file_bytes, max_total_bytes):
    raw = str(raw_context or "")
    if not raw:
        return {"format": "empty", "attachments": [], "legacy_context": None}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {"format": "legacy", "attachments": [], "legacy_context": raw}

    if not isinstance(payload, dict) or payload.get("type") != ATTACHMENT_ENVELOPE_TYPE:
        return {"format": "legacy", "attachments": [], "legacy_context": raw}
    if type(payload.get("version")) is not int or payload.get("version") != ATTACHMENT_ENVELOPE_VERSION:
        raise ValueError("This chat uses an unsupported attachment format.")
    envelope_attachments = payload.get("attachments")
    if not isinstance(envelope_attachments, list):
        raise ValueError("This chat contains invalid stored attachment data.")

    attachments = normalize_browser_attachments(
        envelope_attachments,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
    )
    return {"format": "envelope", "attachments": attachments, "legacy_context": None}


def legacy_attachment_metadata(raw_context):
    lines = str(raw_context or "").splitlines()
    indexed = {}
    fallback_names = []
    current_name = None
    for line in lines:
        name_match = LEGACY_ATTACHMENT_NAME_RE.match(line.strip())
        if name_match:
            current_name = sanitize_attachment_name(name_match.group(1))
            if current_name:
                fallback_names.append(current_name)
            continue
        index_match = LEGACY_ATTACHMENT_INDEX_RE.match(line.strip())
        if index_match and current_name:
            index = int(index_match.group(1))
            indexed.setdefault(index, current_name)

    if indexed:
        names = [indexed[index] for index in sorted(indexed)]
    else:
        names = []
        seen = set()
        for name in fallback_names:
            if name not in seen:
                seen.add(name)
                names.append(name)

    return [
        {
            "kind": "text",
            "name": name,
            "mime_type": infer_mime_type(name),
            "size": None,
            "legacy": True,
        }
        for name in names
    ]


def normalize_openai_messages(
    raw_messages,
    max_total_chars=200000,
    max_image_bytes=4 * 1024 * 1024,
    max_images=8,
    max_total_image_bytes=None,
):
    if not isinstance(raw_messages, list) or not raw_messages:
        raise ValueError("messages must be a non-empty array.")

    max_total_chars = max(1, int(max_total_chars))
    max_image_bytes = max(1, int(max_image_bytes))
    max_images = max(1, int(max_images))
    if max_total_image_bytes is None:
        max_total_image_bytes = max_image_bytes * max_images
    max_total_image_bytes = max(1, int(max_total_image_bytes))

    normalized = []
    total_chars = 0
    image_count = 0
    total_image_bytes = 0
    for message in raw_messages:
        if not isinstance(message, dict):
            raise ValueError("Each message must be an object.")
        role = str(message.get("role") or "").strip().lower()
        if role not in {"system", "user", "assistant"}:
            raise ValueError("Only system, user, and assistant message roles are supported.")
        content = message.get("content")
        if isinstance(content, str):
            total_chars += len(content)
            normalized.append({"role": role, "content": content})
            continue
        if not isinstance(content, list) or not content:
            raise ValueError("Message content must be text or an array of text/image parts.")

        parts = []
        for part in content:
            if not isinstance(part, dict):
                raise ValueError("Each content part must be an object.")
            part_type = str(part.get("type") or "").strip().lower()
            if part_type == "text":
                text = part.get("text")
                if not isinstance(text, str):
                    raise ValueError("Text content parts must contain text.")
                total_chars += len(text)
                parts.append({"type": "text", "text": text})
            elif part_type == "image_url":
                image_url = part.get("image_url")
                url = image_url.get("url") if isinstance(image_url, dict) else image_url
                mime_type, image_data = decode_image_data_url(url, max_image_bytes)
                image_count += 1
                total_image_bytes += len(image_data)
                if image_count > max_images:
                    raise ValueError(f"A request can contain at most {max_images} images.")
                if total_image_bytes > max_total_image_bytes:
                    raise ValueError("Total image size exceeds the configured request limit.")
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": image_data_url(mime_type, image_data)},
                })
            else:
                raise ValueError("Only text and image_url content parts are supported.")
        normalized.append({"role": role, "content": parts})

    if total_chars > max_total_chars:
        raise ValueError("The request content exceeds the safe context limit.")
    return normalized
