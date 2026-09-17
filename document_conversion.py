"""Narrow, CPU-only Docling conversion boundary for chat attachments.

Attachment validation calls this module for supported documents. It has no
dependency on Flask, Socket.IO, persistence, or the attachment envelope.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import json
import os
import zipfile


DEFAULT_MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_DOCUMENT_PAGES = 25
DOCLING_ARTIFACTS_PATH = Path(__file__).resolve().parent / "docling_artifacts"

DOCUMENT_FORMATS = {
    ".pdf": {
        "format_name": "pdf",
        "mime_type": "application/pdf",
        "input_format": "PDF",
        "validation": "pdf",
    },
    ".docx": {
        "format_name": "docx",
        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "input_format": "DOCX",
        "required_member": "word/document.xml",
    },
    ".dotx": {
        "format_name": "docx",
        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "input_format": "DOCX",
        "required_member": "word/document.xml",
    },
    ".docm": {
        "format_name": "docx",
        "mime_type": "application/vnd.ms-word.document.macroEnabled.12",
        "input_format": "DOCX",
        "required_member": "word/document.xml",
    },
    ".dotm": {
        "format_name": "docx",
        "mime_type": "application/vnd.ms-word.template.macroEnabled.12",
        "input_format": "DOCX",
        "required_member": "word/document.xml",
    },
    ".pptx": {
        "format_name": "pptx",
        "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "input_format": "PPTX",
        "required_member": "ppt/presentation.xml",
    },
    ".potx": {
        "format_name": "pptx",
        "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.template",
        "input_format": "PPTX",
        "required_member": "ppt/presentation.xml",
    },
    ".ppsx": {
        "format_name": "pptx",
        "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.slideshow",
        "input_format": "PPTX",
        "required_member": "ppt/presentation.xml",
    },
    ".pptm": {
        "format_name": "pptx",
        "mime_type": "application/vnd.ms-powerpoint.presentation.macroEnabled.12",
        "input_format": "PPTX",
        "required_member": "ppt/presentation.xml",
    },
    ".potm": {
        "format_name": "pptx",
        "mime_type": "application/vnd.ms-powerpoint.template.macroEnabled.12",
        "input_format": "PPTX",
        "required_member": "ppt/presentation.xml",
    },
    ".ppsm": {
        "format_name": "pptx",
        "mime_type": "application/vnd.ms-powerpoint.slideshow.macroEnabled.12",
        "input_format": "PPTX",
        "required_member": "ppt/presentation.xml",
    },
    ".xlsx": {
        "format_name": "xlsx",
        "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "input_format": "XLSX",
        "required_member": "xl/workbook.xml",
    },
    ".xlsm": {
        "format_name": "xlsx",
        "mime_type": "application/vnd.ms-excel.sheet.macroEnabled.12",
        "input_format": "XLSX",
        "required_member": "xl/workbook.xml",
    },
    ".odt": {
        "format_name": "odt",
        "mime_type": "application/vnd.oasis.opendocument.text",
        "input_format": "ODT",
        "required_member": "content.xml",
        "zip_mimetype": "application/vnd.oasis.opendocument.text",
    },
    ".ott": {
        "format_name": "odt",
        "mime_type": "application/vnd.oasis.opendocument.text-template",
        "input_format": "ODT",
        "required_member": "content.xml",
        "zip_mimetype": "application/vnd.oasis.opendocument.text-template",
    },
    ".ods": {
        "format_name": "ods",
        "mime_type": "application/vnd.oasis.opendocument.spreadsheet",
        "input_format": "ODS",
        "required_member": "content.xml",
        "zip_mimetype": "application/vnd.oasis.opendocument.spreadsheet",
    },
    ".ots": {
        "format_name": "ods",
        "mime_type": "application/vnd.oasis.opendocument.spreadsheet-template",
        "input_format": "ODS",
        "required_member": "content.xml",
        "zip_mimetype": "application/vnd.oasis.opendocument.spreadsheet-template",
    },
    ".odp": {
        "format_name": "odp",
        "mime_type": "application/vnd.oasis.opendocument.presentation",
        "input_format": "ODP",
        "required_member": "content.xml",
        "zip_mimetype": "application/vnd.oasis.opendocument.presentation",
    },
    ".otp": {
        "format_name": "odp",
        "mime_type": "application/vnd.oasis.opendocument.presentation-template",
        "input_format": "ODP",
        "required_member": "content.xml",
        "zip_mimetype": "application/vnd.oasis.opendocument.presentation-template",
    },
    ".epub": {
        "format_name": "epub",
        "mime_type": "application/epub+zip",
        "input_format": "EPUB",
        "required_member": "META-INF/container.xml",
        "zip_mimetype": "application/epub+zip",
    },
    ".eml": {
        "format_name": "email",
        "mime_type": "message/rfc822",
        "input_format": "EMAIL",
        "validation": "email",
        "accepted_mime_types": ("text/plain",),
    },
    ".msg": {
        "format_name": "email",
        "mime_type": "application/vnd.ms-outlook",
        "input_format": "EMAIL",
        "validation": "ole",
    },
    ".adoc": {
        "format_name": "asciidoc",
        "mime_type": "text/asciidoc",
        "input_format": "ASCIIDOC",
        "validation": "text",
        "accepted_mime_types": ("text/plain",),
    },
    ".asciidoc": {
        "format_name": "asciidoc",
        "mime_type": "text/asciidoc",
        "input_format": "ASCIIDOC",
        "validation": "text",
        "accepted_mime_types": ("text/plain",),
    },
    ".tex": {
        "format_name": "latex",
        "mime_type": "text/x-tex",
        "input_format": "LATEX",
        "validation": "text",
        "accepted_mime_types": ("application/x-tex", "text/x-latex", "text/plain"),
    },
    ".latex": {
        "format_name": "latex",
        "mime_type": "text/x-latex",
        "input_format": "LATEX",
        "validation": "text",
        "accepted_mime_types": ("application/x-tex", "text/x-tex", "text/plain"),
    },
    ".boxnote": {
        "format_name": "boxnote",
        "mime_type": "application/vnd.box.boxnote",
        "input_format": "BOXNOTE",
        "validation": "json",
        "accepted_mime_types": ("application/json",),
    },
    ".vtt": {
        "format_name": "vtt",
        "mime_type": "text/vtt",
        "input_format": "VTT",
        "validation": "vtt",
        "accepted_mime_types": ("text/plain",),
    },
    ".pages": {
        "format_name": "pages",
        "mime_type": "application/vnd.apple.pages",
        "input_format": "IWORK_PAGES",
        "validation": "pages",
        "accepted_mime_types": (
            "application/zip",
            "application/x-zip-compressed",
            "application/xml",
            "text/xml",
        ),
    },
    ".nxml": {
        "format_name": "jats",
        "mime_type": "application/xml",
        "input_format": "XML_JATS",
        "validation": "xml",
        "accepted_mime_types": ("text/xml",),
    },
    ".xbrl": {
        "format_name": "xbrl",
        "mime_type": "application/xml",
        "input_format": "XML_XBRL",
        "validation": "xml",
        "accepted_mime_types": ("text/xml", "application/xhtml+xml"),
    },
    ".dclg": {
        "format_name": "doclang",
        "mime_type": "application/xml",
        "input_format": "XML_DOCLANG",
        "validation": "xml",
        "accepted_mime_types": ("text/xml",),
    },
    ".dclx": {
        "format_name": "dclx",
        "mime_type": "application/zip",
        "input_format": "DCLX",
        "validation": "zip",
        "accepted_mime_types": ("application/x-zip-compressed",),
    },
}

SUPPORTED_DOCUMENT_FORMAT_NAMES = tuple(
    dict.fromkeys(info["format_name"].upper() for info in DOCUMENT_FORMATS.values())
)


class DocumentConversionError(ValueError):
    """A controlled conversion failure safe to show to an attachment caller."""


@dataclass(frozen=True)
class ConvertedDocument:
    """Normalized document content suitable for the existing text attachment path."""

    markdown: str
    source_name: str
    source_mime_type: str
    source_format: str
    page_count: int | None


def is_supported_document_name(filename: str) -> bool:
    """Return whether a filename has a CE-supported Docling extension."""
    return _document_extension(filename) in DOCUMENT_FORMATS


def get_docling_artifacts_path() -> Path:
    """Return the deterministic CE-local location for Docling PDF artifacts."""
    return DOCLING_ARTIFACTS_PATH


def convert_document_bytes(
    data: bytes,
    filename: str,
    *,
    max_file_bytes: int = DEFAULT_MAX_DOCUMENT_BYTES,
    max_pages: int = DEFAULT_MAX_DOCUMENT_PAGES,
) -> ConvertedDocument:
    """Convert one allowed in-memory binary document to normalized Markdown.

    The caller owns the input bytes.  They are only held in a local ``BytesIO``
    for Docling and are neither written nor retained by this module.

    PDF conversion requires pre-downloaded artifacts in the CE-local
    ``docling_artifacts`` directory.  The function never creates or populates
    that directory during a chat request.
    """
    safe_name = _safe_document_name(filename)
    extension = _document_extension(safe_name)
    format_info = DOCUMENT_FORMATS.get(extension)
    if format_info is None:
        raise DocumentConversionError(
            "Unsupported document type. Supported document formats are "
            f"{', '.join(SUPPORTED_DOCUMENT_FORMAT_NAMES)}."
        )

    if not isinstance(data, bytes) or not data:
        raise DocumentConversionError("The document is empty or could not be read.")

    try:
        max_file_bytes = int(max_file_bytes)
        max_pages = int(max_pages)
    except (TypeError, ValueError) as exc:
        raise ValueError("Document conversion limits must be positive integers.") from exc
    if max_file_bytes < 1 or max_pages < 1:
        raise ValueError("Document conversion limits must be positive integers.")
    if len(data) > max_file_bytes:
        raise DocumentConversionError("The document exceeds the allowed size limit.")

    _validate_document_container(data, extension, format_info)
    pdf_artifacts_path = _require_pdf_artifacts_path() if extension == ".pdf" else None

    try:
        (
            AcceleratorDevice,
            AcceleratorOptions,
            DocumentConverter,
            DocumentStream,
            InputFormat,
            PdfFormatOption,
            PdfPipelineOptions,
        ) = _docling_api()

        pipeline_options = PdfPipelineOptions(
            accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU),
            artifacts_path=str(pdf_artifacts_path) if pdf_artifacts_path else None,
            do_ocr=False,
            enable_remote_services=False,
            allow_external_plugins=False,
        )
        converter = DocumentConverter(
            allowed_formats=list(
                dict.fromkeys(
                    getattr(InputFormat, info["input_format"])
                    for info in DOCUMENT_FORMATS.values()
                )
            ),
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            },
        )
        result = converter.convert(
            DocumentStream(name=safe_name, stream=BytesIO(data)),
            max_file_size=max_file_bytes,
            max_num_pages=max_pages,
            raises_on_error=True,
        )
        markdown = str(result.document.export_to_markdown()).strip()
    except DocumentConversionError:
        raise
    except Exception as exc:
        if extension == ".pdf" and _is_missing_pdf_artifacts_error(exc):
            raise DocumentConversionError(
                "PDF conversion is unavailable because required local Docling artifacts are missing or incomplete. "
                "Ask an operator to prepare the CE Docling artifacts."
            ) from exc
        raise DocumentConversionError(
            "The document could not be converted. It may be malformed, protected, or exceed the document-processing limit."
        ) from exc

    if not markdown:
        raise DocumentConversionError("The document did not contain usable text.")

    return ConvertedDocument(
        markdown=markdown,
        source_name=safe_name,
        source_mime_type=format_info["mime_type"],
        source_format=format_info["format_name"],
        page_count=_page_count(result.document),
    )


def _docling_api():
    """Import Docling only when a conversion is requested."""
    try:
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import DocumentStream, InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as exc:
        raise DocumentConversionError(
            "Document conversion is unavailable because the required Docling package is not installed."
        ) from exc

    return (
        AcceleratorDevice,
        AcceleratorOptions,
        DocumentConverter,
        DocumentStream,
        InputFormat,
        PdfFormatOption,
        PdfPipelineOptions,
    )


def _safe_document_name(filename: str) -> str:
    raw_name = str(filename or "").replace("\x00", "").replace("\\", "/")
    safe_name = raw_name.rsplit("/", 1)[-1].strip()
    if not safe_name or safe_name in {".", ".."}:
        raise DocumentConversionError("The document has no usable filename.")
    return safe_name[:255]


def _document_extension(filename: str) -> str:
    return os.path.splitext(str(filename or "").lower())[1]


def _validate_document_container(data: bytes, extension: str, format_info: dict) -> None:
    validation = format_info.get("validation")
    if validation == "pdf":
        if not data.startswith(b"%PDF-"):
            raise DocumentConversionError("The document contents do not match its PDF filename.")
        return

    if validation in {None, "zip"}:
        _validate_zip_document(data, format_info)
        return
    if validation == "pages":
        if data.startswith(b"PK\x03\x04"):
            _validate_zip_document(data, format_info)
            return
        try:
            is_xml = data.decode("utf-8-sig").lstrip().startswith("<")
        except UnicodeDecodeError as exc:
            raise DocumentConversionError("The Pages document is malformed or unreadable.") from exc
        if not is_xml:
            raise DocumentConversionError("The document contents do not match its Pages filename.")
        return
    if validation == "ole":
        if not data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            raise DocumentConversionError("The document contents do not match its MSG filename.")
        return
    if validation == "json":
        try:
            json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DocumentConversionError("The BoxNote document is malformed or unreadable.") from exc
        return
    if validation == "vtt":
        try:
            is_vtt = data.decode("utf-8-sig").lstrip().startswith("WEBVTT")
        except UnicodeDecodeError as exc:
            raise DocumentConversionError("The WebVTT document is malformed or unreadable.") from exc
        if not is_vtt:
            raise DocumentConversionError("The document contents do not match its WebVTT filename.")
        return
    if validation == "xml":
        try:
            is_xml = data.decode("utf-8-sig").lstrip().startswith("<")
        except UnicodeDecodeError as exc:
            raise DocumentConversionError("The XML document is malformed or unreadable.") from exc
        if not is_xml:
            raise DocumentConversionError("The document contents do not match its XML filename.")
        return
    if validation == "email":
        header_sample = data[:16384].decode("ascii", errors="ignore")
        if not any(
            line.partition(":")[0].replace("-", "").isalnum()
            for line in header_sample.splitlines()
            if ":" in line
        ):
            raise DocumentConversionError("The document contents do not match its EML filename.")
        return
    if validation == "text":
        if b"\x00" in data:
            raise DocumentConversionError("The document contents do not match its filename.")
        return

    raise DocumentConversionError("The document has an unsupported validation type.")


def _validate_zip_document(data: bytes, format_info: dict) -> None:
    if not data.startswith(b"PK\x03\x04"):
        raise DocumentConversionError("The document contents do not match its filename.")

    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            required_member = format_info.get("required_member")
            if required_member and required_member not in archive.namelist():
                raise DocumentConversionError("The document contents do not match its filename.")
            expected_zip_mimetype = format_info.get("zip_mimetype")
            if expected_zip_mimetype:
                mimetype_entry = archive.getinfo("mimetype")
                if mimetype_entry.file_size > 256:
                    raise DocumentConversionError("The document archive is malformed or unreadable.")
                actual_zip_mimetype = archive.read("mimetype").decode("ascii", errors="replace")
                if actual_zip_mimetype != expected_zip_mimetype:
                    raise DocumentConversionError("The document contents do not match its filename.")
    except DocumentConversionError:
        raise
    except (KeyError, OSError, UnicodeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise DocumentConversionError("The document archive is malformed or unreadable.") from exc


def _require_pdf_artifacts_path() -> Path:
    artifacts_path = get_docling_artifacts_path()
    if not artifacts_path.is_dir():
        raise DocumentConversionError(
            "PDF conversion is unavailable until local Docling artifacts are prepared by an operator."
        )
    return artifacts_path


def _is_missing_pdf_artifacts_error(exc: Exception) -> bool:
    current = exc
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        name = current.__class__.__name__.lower()
        detail = str(current).lower()
        if isinstance(current, FileNotFoundError):
            return True
        if any(token in name for token in ("localentrynotfound", "offlinemode", "entrynotfound")):
            return True
        if "artifact" in detail and any(token in detail for token in ("missing", "not found", "incomplete")):
            return True
        if "offline mode" in detail or "cannot reach https://huggingface" in detail:
            return True
        if "offline" in detail and any(token in detail for token in ("model", "file", "entry", "cache")):
            return True
        current = current.__cause__ or current.__context__
    return False


def _page_count(document) -> int | None:
    try:
        count = len(document.pages)
    except Exception:
        return None
    return count if count >= 0 else None
