# Chat, Projects, and files

[← Documentation](README.md) · [Install CE](../INSTALL.md) · [Models](MODELS.md)

A saved workspace for conversations with the models running on your CE host.

[Conversations](#conversations) · [Projects and instructions](#projects-and-instructions) · [Files](#files-in-a-conversation) · [Images](#image-understanding) · [Voice](#local-voice-dictation) · [Import and export](#import-and-export)

![The CE chat interface](images/chat-screenshot.png)

## Conversations

Responses stream into the conversation as the model generates them. Stop an in-progress response, edit the latest prompt, or regenerate the latest answer through the existing chat controls. When an answer has multiple revisions, switch between them; the displayed model attribution follows the selected revision.

CE saves chat sessions, generates titles, and provides sidebar search. Direct chat URLs preserve the active conversation across a page refresh. Title generation normally uses the main model; administrators can configure an [optional title model](MODELS.md#chat-title-generation).

### Reasoning display

When a model returns supported reasoning content, CE provides a show/hide control separately from its answer. Reasoning availability and quality depend on the model and runtime; not every response includes it.

<details>
<summary>See the reasoning display</summary>

![Expandable reasoning content in a conversation](images/thoughts-screenshot.png)

</details>

## Projects and instructions

Use **Projects** to group related conversations. **Project Instructions** provide context for conversations in that Project, while personal **System Instructions** apply to your browser chats. Personal instructions are available in the **Instructions** tab of Account Settings, alongside separate Email and Password tabs.

For example, a Project for writing can specify an audience and tone; a different Project for software work can specify the conventions that matter there.

Personal and Project instructions are combined into one leading system message, followed by the conversation history and the current message. This preserves compatibility with chat templates that expect a single initial system message.

Project organization is not a promise of automatic cross-chat memory or a searchable knowledge base. Keep the relevant source material in the conversation. These instruction settings describe browser-chat behavior, not automatic instruction injection into API requests.

## Files in a conversation

Use the attachment picker, drag-and-drop, or supported clipboard paste to add material to a conversation. CE uses native handling for text/code, local Docling conversion for supported documents, and a separate image path.

| Material | Supported handling |
| --- | --- |
| Text and code | Native text handling for supported text/code files. |
| PDF | Local conversion to Markdown; requires the prepared PDF artifacts described in the installation guide. |
| Modern Microsoft Office | Office Open XML documents, spreadsheets, presentations, templates, slideshows, and supported macro-enabled variants. |
| OpenDocument | Documents, spreadsheets, presentations, and supported templates. |
| Email and reading formats | EML, MSG, EPUB, and Apple Pages. |
| Other documents | AsciiDoc, LaTeX, BoxNote, WebVTT, JATS `.nxml`, XBRL `.xbrl`, and DocLang `.dclg`/`.dclx`. |

Legacy binary **DOC, XLS, and PPT are unsupported**. A supported extension does not guarantee that every damaged, encrypted, or unusual document can be converted.

Docling runs locally. PDF conversion uses the CPU with **OCR disabled**; image-only scans therefore do not acquire a separate OCR text-extraction path. No LibreOffice, ffmpeg, or external OCR application is required by this conversion workflow.

For converted documents, CE saves the extracted Markdown/text and basic metadata with the conversation, **not the original document binary**. Keep your own originals. Image retention differs, as described below.

[Prepare local PDF artifacts →](../INSTALL.md#local-docling-document-support)

### Attachment limits

Administrators control attachment count, per-file size, combined size, and context limits. In v1.4, the size settings are displayed in MiB.

| Boundary | v1.4 behavior |
| --- | --- |
| Per-file setting | Can be configured up to **10 MiB**. |
| Combined-attachment setting | Can be configured up to **40 MiB**. |
| Browser socket transport | Bounded at **64 MiB**, allowing for binary encoding and envelope overhead. This is not an attachment allowance. |
| Additional validation | Count, serialized-payload, and context checks still apply. |
| API request body | Separate **16 MiB** limit; see the [API guide](API.md#images-and-request-limits). |

The first two values are configurable ceilings, **not new default settings**. Actual allowed uploads depend on the saved configuration and all applicable checks.

## Image understanding

Attach **PNG, JPEG, WebP, GIF, HEIC/HEIF, AVIF, TIFF, or BMP** images. SVG is not supported.

Image understanding requires a compatible GGUF vision model, a matching multimodal projector (`mmproj`), and a `llama-server` build that supports the pair. The registry's **Images** suitability flag alone does not enable vision. An administrator must [configure the projector and reload the model](MODELS.md#image-capable-models).

CE preserves original images with the conversation. TIFF and HEIC/HEIF receive browser-compatible PNG previews, and GIF previews can remain animated. The model sees the **first frame or page**, not an entire animation or multipage image.

PNG and JPEG use the direct image path. Other supported formats are normalized to an 8-bit RGB PNG, with transparency flattened onto white. Converted images are limited to **16,777,216 pixels**. Browser preview behavior and model input are therefore not always identical.

This is image **understanding**, not image generation or editing. Interpretation depends on the active model; check important details against the source image.

## Local voice dictation

Voice is optional. While an administrator has the speech runtime running, click the microphone, speak, and finish the recording. Review the transcription in the composer and click **Send** yourself.

Dictation does not automatically send a message or create a spoken conversation with the assistant. Recordings are processed in memory rather than kept in an audio library. The current validated voice setup is Windows/NVIDIA CUDA with a separate NeMo environment and a compatible local `.nemo` model.

Normal typed chat works without any speech setup. Full setup, explicit Start/Stop behavior, and HTTPS/localhost microphone requirements belong in the [Local Voice Dictation guide](../VOICE_INSTALL.md).

## Import and export

Import **JSON backups or CSV chat exports**. New JSON exports use a **versioned backup format** that preserves chat records, Project definitions and instructions, conversation-to-Project membership, and empty Projects within the exported scope.

When restoring a versioned JSON backup after **Clear Chat History**, CE reuses matching surviving Projects and recreates missing Projects with locally allocated destination IDs. Conversation membership is remapped to the restored or reused Projects; Unassigned conversations remain Unassigned. Source numeric Project IDs are backup-local references, not trusted destination IDs, and existing ownership boundaries remain enforced.

Older JSON backups containing bare chat arrays remain importable, but cannot restore Project membership because those files never included that metadata. **CSV remains chat interchange** and does not preserve Projects. Administrators can also import compatible **SQLite backups**, including Project metadata.

Export conversations as JSON, CSV, or a **read-only Markdown archive**. Markdown is a human-readable archive, not a round-trip restore format. JSON provides portable chat and Project backup/restore, not a complete installation backup; it does not replace a coordinated application/database backup.

Imports are bounded by the administrator's **Maximum Chat Import Size** setting. v1.4 removes the old fixed 25,000-row ceiling without silently truncating imports. SQLite header, integrity, schema, and ownership checks remain in place, with read-only inspection of the source backup.

Large imports still need enough host memory and request time. A file-size allowance is not a guarantee of a fixed memory footprint.

Exporting a conversation does not itself clear the workspace. Before replacing data or using a blank demonstration environment, preserve the original installation and use the [operator backup guidance](ADMIN.md#backups-and-restoration).

---

[Models and runtime control](MODELS.md) · [Local voice setup](../VOICE_INSTALL.md) · [Administration and data](ADMIN.md)
