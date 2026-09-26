# Administration and data

[← Documentation](README.md) · [Installation](../INSTALL.md) · [Monitoring](MONITORING.md)

Operate CE as a self-hosted application, with authenticated users and administrative controls kept separate from normal chat.

[Accounts](#accounts-and-access) · [Settings](#settings) · [Storage](#where-data-lives) · [Backups](#backups-and-restoration) · [Network access](#network-access)

## Accounts and access

CE uses email-and-password sign-in, Remember-me support, role-aware interface behavior, and administrator-only management controls. Accounts created with temporary credentials follow a forced password-change flow.

Account Settings separates **Instructions**, **Email**, and **Password** into tabs. Personal instructions belong to the user's browser-chat preferences; system-wide runtime settings belong to administration.

Give administrative access only to people who need the management controls. A shared installation still uses shared runtime resources; separate user accounts do not create a separate GPU or model process for every conversation.

For a clean demonstration, prefer an operator-prepared demo account and harmless demo conversations rather than deleting an established workspace. Do not capture real account lists, credentials, or personal chat titles in public screenshots.

## Settings

Use the existing administrative sections for runtime defaults, model management, API access, speech configuration, attachment limits, and import limits. Relevant details have one primary guide:

| Area | Guide |
| --- | --- |
| Language models, projectors, and title selection | [Models and runtime control](MODELS.md) |
| Attachment and import boundaries | [Chat, Projects, and files](CHAT.md) |
| API key lifecycle and enablement | [Connect through the API](API.md) |
| Optional dedicated speech environment | [Local Voice Dictation](../VOICE_INSTALL.md) |
| Initial configuration and ordered upgrades | [Installation](../INSTALL.md) |

The speech runtime has its own settings card below API Access. Saving a speech path or selecting a speech model does not start speech; the administrator explicitly uses **Start** and **Stop**.

Review runtime status after changing launch-time settings. A saved setting and a running process are different states; some changes require a managed stop/reload.

## Where data lives

| Storage | Role |
| --- | --- |
| MySQL | System data, including application settings, model registry information, accounts, and user preferences. Runtime defaults are stored in `llm_app_settings`. |
| `chats.sqlite` | Chat history and associated local workspace/request-tracking data. |
| `bootstrap_config.json` | Local bootstrap configuration, including database connection values. Treat it as sensitive. |
| `flask_secret.key` | Application secret key; also supplies key material for stored SMTP credential encryption. Treat it as sensitive. |
| Configured model directory | Operator-supplied model and projector files. The folder need not be named `LLMs`. |
| `docling_artifacts/` | Pre-downloaded layout/table artifacts used for local PDF conversion. |
| Dedicated speech environment | Optional operator-managed Python environment, separate from CE's application environment. |

For converted documents, CE retains extracted Markdown/text and basic metadata, not the original document binary. Original images are retained with conversations. Voice recordings are processed in memory, not saved as an audio library; text sent from the composer becomes ordinary chat content.

The API request-tracking path records operational metadata without storing request prompts, responses, images, keys, or authorization headers. This does not replace a review of logging elsewhere in your deployment.

## Backups and restoration

Before an upgrade or a deliberate workspace reset, the operator should preserve a coordinated backup of **MySQL, `chats.sqlite`, `bootstrap_config.json`, and the matching `flask_secret.key`**, along with any other local files needed to reproduce the installation. The documented upgrade workflow stops CE before taking backups and applying changes.

Keep backups outside the public application/documentation tree. Protect database dumps, configuration, and chat archives as private data. Preserve the matching `flask_secret.key` as sensitive, protected backup material: stored SMTP credentials use encryption key material derived from it. If the matching key is unavailable after restoration, SMTP credentials must be entered again. Preserve the installed version and the relevant runtime/settings information so a restore has context.

Versioned JSON backups provide portable chat and Project backup/restore, preserving chat records, Project definitions and instructions, conversation membership, and empty Projects within the exported scope. Imports reuse matching surviving Projects or recreate missing ones with local IDs and remap membership while enforcing ownership boundaries. Unassigned conversations remain Unassigned. JSON does not replace a coordinated application/database backup.

Legacy JSON backups containing bare chat arrays remain importable but lack Project metadata and cannot restore membership. CSV remains chat interchange without Projects; Markdown is a read-only archive, not a round-trip restore format. Administrators can still import compatible SQLite backups with Project metadata, subject to the import controls and validation described in the [chat guide](CHAT.md#import-and-export).

Exporting does not erase the live workspace. Do not treat a human-readable archive as proof that every account, setting, attachment, Project, and historical record can be restored from it.

Database backup, restoration, schema changes, and migrations are **operator actions**. Use the [ordered upgrade instructions](../INSTALL.md#existing-installation-upgrades) and do not run clean-install schema or seed files against an existing installation.

## Network access

CE's browser-facing application endpoint is distinct from its managed inference services. Main and title `llama-server` processes bind to loopback; the optional speech adapter also binds to loopback. Do not expose those raw ports as a shortcut around the application's access controls.

Configure the application's listen address separately from the accepted browser origin. `0.0.0.0` can be a server listen value; it is **not** a browser origin. See the [installer networking instructions](../INSTALL.md#9-start-the-first-run-installer-manually).

Use HTTPS for remote access where credentials or private conversations traverse an untrusted network. Remote browser microphone capture also requires a secure context; HTTP to another machine's LAN IP is not the same as localhost on the browser's own computer.

Local inference means CE does not require a cloud AI service for the documented workflows. It does not remove the operator's responsibilities for host access, transport security, backups, or independently configured integrations and logs.

## Diagnosing a problem

Start with the visible error, the relevant [runtime log](MONITORING.md#runtime-logs), and the configuration involved. Avoid changing drivers, dependencies, GPU allocation, and application settings all at once.

For a public report, include the version, operating system, relevant runtime/model, and a short sanitized reproduction. Do not include bootstrap configuration, database exports, authentication headers, or private conversations.

---

[Installation and upgrades](../INSTALL.md) · [Monitoring](MONITORING.md) · [Product overview](../README.md)
