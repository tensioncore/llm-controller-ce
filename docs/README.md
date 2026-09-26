# LLM Controller CE documentation

[← Product overview](../README.md)

Start with installation, then choose the guide for the job in front of you. These guides describe **CE v1.4**; use the documentation supplied with the version you install.

| Guide | What you will find |
| --- | --- |
| [Install and upgrade CE](../INSTALL.md) | Prerequisites, first-run setup, Ubuntu service deployment, Windows runtime notes, and ordered upgrades. |
| [Chat, Projects, and files](CHAT.md) | Conversations, instructions, reasoning display, attachments, supported formats, import, and export. |
| [Models and runtime control](MODELS.md) | Local discovery, model selection, projectors, title generation, speech designation, and runtime settings. |
| [Analytics and monitoring](MONITORING.md) | Usage, benchmarks, logs, memory, GPU telemetry, and how to interpret the results. |
| [Connect through the API](API.md) | API keys, the two supported endpoints, request examples, streaming, and limitations. |
| [Administration and data](ADMIN.md) | Accounts, administrative boundaries, settings, storage, and backup responsibilities. |
| [Set up local voice](../VOICE_INSTALL.md) | Optional speech-to-text, the tested Windows/CUDA stack, microphone requirements, and troubleshooting. |

## Before your first conversation

Complete the [installation guide](../INSTALL.md), place a compatible local model in the configured model directory, and use CE's model controls to load it. A smaller model that fits the available memory is a useful starting point; file size, runtime compatibility, and workload all matter.

Normal typed chat does not require a vision projector, a speech environment, or an API key. Set up those capabilities only when you need them.

## Operator reference

For a problem report, include the CE version, operating system, runtime/model involved, the relevant action, and a sanitized error or log excerpt. Leave out credentials, API keys, personal chats, and database exports.

[Report an issue](https://github.com/tensioncore/llm-controller-ce/issues) · [Website](https://www.llmcontroller.com) · [Wiki](https://wiki.llmcontroller.com) · [LLM Controller Archive Viewer](https://github.com/tensioncore/llm-controller-archive-viewer)

Developed by **Tensioncore Administration Services**. See the repository's [GPLv3 license](../LICENSE).
