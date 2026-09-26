# Models and runtime control

[← Documentation](README.md) · [Chat and files](CHAT.md) · [Monitoring](MONITORING.md)

Manage the local model library and the runtime behind your conversations without turning every model change into a separate terminal workflow.

[Local library](#the-local-library) · [Load and stop](#load-and-stop) · [Image models](#image-capable-models) · [Titles](#chat-title-generation) · [Speech](#speech-models)

![CE model selection and runtime controls](images/model-drawer.png)

## The local library

An administrator configures **Models DIR**, then uses the registry's **Rescan Models** action to discover local GGUF files and `.nemo` speech checkpoints. CE recognizes complete split GGUF sets. Finish copying all required files before rescanning.

Discovery is not a download service or a compatibility guarantee. The operator supplies the model files and a working runtime. Keep files within the configured model directory and keep their accompanying files together as required by the model.

The library supports favorites, enable/disable state, benchmark eligibility, usage profiles, notes, and recorded performance information. Language models can be sorted by name, size, or Max TPS in either direction.

### State and designation

| Indicator or designation | Meaning |
| --- | --- |
| Green state | The registry entry is enabled. |
| Yellow state | The file is present, but the entry is disabled. |
| Red state | The registered file is missing. |
| MMPROJ | A projector asset, not a standalone chat language model. |
| Speech-to-Text | An explicitly designated speech checkpoint, excluded from chat-language-model, title-model, and benchmark choices. |

A file must also meet the relevant availability and compatibility requirements. A green indicator or suitability label does not certify that a particular runtime can load it.

Metadata such as **Images**, profiles, and notes helps the operator describe suitability. It does not add a missing model capability. Keep these distinctions separate from actual runtime readiness.

## Load and stop

Choose an available language model in the model controls and load it. Wait for runtime readiness before chatting. Stop the model through CE when it is no longer needed; consult [runtime logs](MONITORING.md#runtime-logs) when startup fails.

Runtime settings include the `llama-server` executable path, model directory, GPU layers, CPU threads, ports, and GPU split threshold. The operator provides an executable that works on the host; CE does not build or install `llama-server`.

Changes to launch-time configuration, including a projector change, require stopping and reloading the managed model. Do not treat a saved setting as proof that an already-running process has adopted it.

Capacity depends on the model, context, runtime build, and other host workloads. Keep enough memory available; the existence of multiple GPUs does not mean every combination of language, title, and speech models will fit.

CE's managed main and title runtimes bind to **127.0.0.1**. Their default ports are **8080** and **8081**. Clients should use the authenticated CE application, not publicly exposed raw runtime ports.

[Runtime installation and build examples →](../INSTALL.md#8-build-or-provide-llama-server)

## Image-capable models

Image understanding needs three compatible pieces: the language/vision model, its matching projector, and the `llama-server` build.

In Admin Settings, rescan the model registry and save the compatible projector filename for the language-model row. The projector must already exist inside the configured model scan directory. Stop and reload an already-running model so that the runtime starts with `--mmproj`.

The **Images** checkbox is suitability metadata. The running runtime's projector state—not that checkbox alone—determines whether image requests are accepted. An arbitrary projector is not interchangeable with the matching one.

Use a compatible runtime that accepts OpenAI-style inline `image_url` data. CE does not download remote image URLs, infer model/projector compatibility, or provide image generation.

[Supported image formats and conversion behavior →](CHAT.md#image-understanding)

## Chat-title generation

Leave the Admin Settings picker at **Select Title Generation Model** to generate titles with the active main model.

An administrator can select an enabled, present language model for title generation. A dedicated title runtime is used only when the existing lifecycle detects at least two usable GPUs and can host it. Otherwise, title generation falls back to the main model.

After changing the selection, stop and reload the managed main model to start the selected dedicated runtime. Main-model fallback remains active until the dedicated process is ready. Disabling, removing, or losing the selected model also falls back to the main model.

A title model is optional. Do not install a second model solely to make ordinary chat work.

## Speech models

CE discovers local `.nemo` checkpoints, but an administrator must explicitly enable and designate a compatible file as **Speech-to-Text**. Discovery alone does not do this.

Speech entries are excluded from language-model, title-model, and benchmark choices. Selecting a speech model does not start it. Use the separate **Model → Speech-to-Text** controls to start and stop the shared speech runtime explicitly.

The configured speech runtime path is a **dedicated environment's Python executable**, not `llama-server`, the `.nemo` file, or an activation command. The validated v1.4 speech setup is Windows/NVIDIA CUDA; broader GPU telemetry support does not imply AMD speech support.

[Local voice setup, designation, and lifecycle →](../VOICE_INSTALL.md)

## Performance and benchmark eligibility

The library's recorded **Max TPS** is an observed result, not a promise of future performance. Compare measurements in context: hardware, runtime, model, prompt, and settings can all affect throughput.

Administrators decide which eligible models participate in benchmarks. Saved benchmark prompts, results, and stale-result indicators are explained in [Analytics and monitoring](MONITORING.md#benchmarks).

---

[Chat and files](CHAT.md) · [Runtime logs and GPU monitoring](MONITORING.md) · [Installation](../INSTALL.md)
