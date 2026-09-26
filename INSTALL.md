# LLM Controller CE — Installation

[← Product overview](README.md) · [Documentation](docs/README.md) · [Optional local voice](VOICE_INSTALL.md)

CE uses a first-run web installer. This guide covers the existing installation contract: an ordered Ubuntu/Linux setup, Windows runtime notes, local document preparation, and manual upgrades.

**Installing a new instance?** Start with [Requirements](#requirements), then follow [Ubuntu/Linux](#ubuntulinux-fresh-install) or the [Windows notes](#windows-default-runtime-layout). **Updating an existing instance?** Use [Existing installation upgrades](#existing-installation-upgrades), not the clean-install database files.

## Requirements

The CE v1.4 reference targets are **Python 3.11.7** and **MySQL 8.0.22**. These identify the project's validated baseline, not a requirement to replace a working host with those exact historical patch versions. Use a compatible application environment and validate it on the intended host.

You need Python, MySQL, a writable application folder, local GGUF models, and a working `llama-server` runtime compatible with the host operating system and hardware. Windows and Ubuntu/Linux are supported when these components are configured correctly.

The runtime and models are supplied by the operator. For GPU telemetry, install the appropriate NVIDIA or AMD host tools separately. Image understanding additionally needs a compatible model/projector pair. Speech-to-text is optional and uses its own [dedicated environment](VOICE_INSTALL.md).

### Required application files

Use the complete application distribution. Important files include `app.py`, `requirements.txt`, `install/schema.sql`, `install/seed.sql`, the applicable `install/upgrade_v1_2.sql`, `install/upgrade_v1_3.sql`, and `install/upgrade_v1_4.sql` scripts, and `speech_runtime.py` for the optional speech adapter. The application also needs its normal templates, static files, helpers, and configuration example.

Keep a local model directory such as `LLMs/`, or configure another location in **Models DIR**.

## Local Docling document support

`requirements.txt` includes **Docling 2.121.0** and its OpenDocument, JATS XML, and XBRL XML extras. Documents are converted locally to Markdown; only the converted text and basic metadata are saved. Supported formats and retention behavior are listed in the [chat and files guide](docs/CHAT.md#files-in-a-conversation).

PDF conversion runs on CPU with **OCR disabled**. No LibreOffice, ffmpeg, or external OCR application is required. After installing the application requirements, download the layout and table models into `docling_artifacts/` beside `app.py`.

This preparation needs internet access. Chat-time PDF conversion uses local artifacts and does not download them. Missing or incomplete artifacts prevent PDF conversion; other supported documents do not require these PDF artifacts.

Ubuntu/Linux:

```bash
cd /srv/llmcontroller
./venv/bin/docling-tools models download layout tableformer --output-dir /srv/llmcontroller/docling_artifacts
```

Windows, using the existing global-Python example:

```cmd
cd /d E:\LLM-Controller
C:\Python311\Scripts\docling-tools.exe models download layout tableformer --output-dir E:\LLM-Controller\docling_artifacts
```

Use the `docling-tools` executable from **your application's Python environment**; the example paths are not mandatory. Keep the output at `<LLM_CONTROLLER_DIR>/docling_artifacts` with these exact child directory names:

```text
<LLM_CONTROLLER_DIR>/docling_artifacts/
  docling-project--docling-layout-heron/
  docling-project--docling-models/
```

If downloaded repositories have another namespace-derived name, such as `ds4sd--...`, rename the corresponding artifact directories to the names above. Do not change CE's lookup path to work around a differently named download.

## Existing installation upgrades

These are **manual operator steps**, not actions for a coding agent. Before upgrading, stop CE and back up MySQL, `chats.sqlite`, `bootstrap_config.json`, and the matching `flask_secret.key`. Preserve backups outside the public application/documentation tree.

Treat the matching `flask_secret.key` as sensitive, protected backup material. Stored SMTP credentials use encryption key material derived from it; if the matching key is unavailable after restoration, SMTP credentials must be entered again.

Replace the application files with the intended version, rerun the requirements installation using the application's Python executable, and prepare PDF artifacts when needed:

```bash
python -m pip install -r requirements.txt
```

Use the actual application interpreter in place of `python` when necessary. Do not substitute the optional speech interpreter.

Apply every required upgrade in order. **Do not execute fresh-install schema or seed files against an existing database.** Do not use MySQL's `--force`; if a statement fails, keep CE stopped and resolve the error. MySQL DDL commits independently, so an upgrade is not an all-or-nothing transaction.

### v1.3 to v1.4 upgrade

Run `install/upgrade_v1_4.sql` against the configured MySQL database. It adds `llm_benchmark_models.is_s2t` only when absent, seeds the three speech settings while preserving existing values, and updates `app.version` to `LLM Controller CE v1.4`.

```bash
mysql -u llmcontroller -p llmcontroller < install/upgrade_v1_4.sql
```

Replace the example account and database with your configured values. From PowerShell, use CMD's input redirection:

```powershell
cmd /c "mysql -u llmcontroller -p llmcontroller < install/upgrade_v1_4.sql"
```

The script does not install speech software or change SQLite. Rerunning it preserves existing speech paths, ports, selections, and S2T designations; it does not repair a pre-existing column with the wrong definition. Older installations must first complete their earlier upgrades below.

### v1.2 to v1.3 upgrade

Run `install/upgrade_v1_3.sql` against MySQL. It creates `llm_user_preferences` with the fresh-install primary key and foreign key, then sets `app.version` to `LLM Controller CE v1.3`.

```bash
mysql -u llmcontroller -p llmcontroller < install/upgrade_v1_3.sql
```

PowerShell:

```powershell
cmd /c "mysql -u llmcontroller -p llmcontroller < install/upgrade_v1_3.sql"
```

It can be rerun without replacing existing preference rows; it does not repair an already-existing table with a different definition. SQLite Project and request-tracking tables are initialized at application startup; this script does not modify SQLite.

Continue with the v1.4 upgrade before starting a v1.4 application. After the complete upgrade, restart CE, confirm the About version, and check that System Instructions save and reload.

### Historical v1.1 to v1.2 upgrade

For a pre-v1.2 database, first run `install/upgrade_v1_2.sql` manually with the configured account after taking backups. The original command sequence is:

```bash
mysqldump -u llmcontroller -p llmcontroller > llmcontroller-before-v1.2.sql
cp chats.sqlite chats-before-v1.2.sqlite
mysql -u llmcontroller -p llmcontroller < install/upgrade_v1_2.sql
```

Replace the example backup destinations with secure locations outside the public application tree before running them. CE must already be stopped. This historical migration sets the version to v1.2; complete the v1.3 and v1.4 upgrades in order before restarting.

## Ubuntu/Linux fresh install

The example layout uses `/srv/llmcontroller` for CE, `/srv/llmcontroller/LLMs` for models, and `/srv/llama.cpp` for a separately supplied runtime source/build tree. This is one supported service-style layout, not the only valid Linux deployment pattern.

### 1. Install OS prerequisites

On a new Ubuntu host:

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv curl wget unzip build-essential cmake pkg-config mysql-server mysql-client ufw
python3 --version
```

The distribution's `python3` package may differ from CE's reference version. Confirm which interpreter you intend to use before creating the environment; its venv package must match it.

### 2. Configure firewall for direct port 5000 access

For a deployment intentionally allowing direct access on port 5000, the existing example is:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 5000/tcp
sudo ufw enable
sudo ufw status
```

Check existing firewall rules and the actual SSH port before enabling UFW remotely. Scope access to the intended clients; do not expose an unfinished installer to arbitrary visitors. Local-only, provider-firewall, or reverse-proxy deployments may not need a public port-5000 rule. Use HTTPS for credentials and private data over untrusted networks.

### 3. Start MySQL and create the database user

The operator starts MySQL and prepares a fresh database/account:

```bash
sudo systemctl enable --now mysql
sudo mysql
```

```sql
CREATE DATABASE llmcontroller CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'llmcontroller'@'localhost' IDENTIFIED BY 'change-this-password';
GRANT ALL PRIVILEGES ON llmcontroller.* TO 'llmcontroller'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

Replace the example password before running the SQL. The web installer does not create the MySQL account. MySQL must be running, and the credentials entered in installer Step 1 must have write access to the selected database.

### 4. Extract the application files

Obtain the application archive for the version being installed from the [release downloads](https://github.com/tensioncore/llm-controller-ce/releases). Create a writable destination:

```bash
sudo mkdir -p /srv/llmcontroller
sudo chown -R "$USER":"$USER" /srv/llmcontroller
```

Extract or copy the complete application so that **`app.py` is directly inside `/srv/llmcontroller`**, not inside an extra archive-named subdirectory. Then enter the folder:

```bash
cd /srv/llmcontroller
```

Application package installs should not require elevated permissions. Manual copying/extraction into `/srv` may require permission or ownership adjustments.

### 5. Create the local model folder

```bash
mkdir -p /srv/llmcontroller/LLMs
```

### 6. Create the virtual environment and install requirements

```bash
cd /srv/llmcontroller
python3 -m venv venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.txt
```

For PDF support, complete [Local Docling document support](#local-docling-document-support) before starting CE.

If venv creation fails with `ensurepip is not available`, install the venv package matching the selected interpreter. For the distribution's default Python:

```bash
sudo apt install -y python3-venv
python3 -m venv venv
```

Do not use `sudo pip`. Fix application-folder ownership rather than installing application dependencies as root:

```bash
sudo chown -R "$USER":"$USER" /srv/llmcontroller
```

### 7. Add a GGUF model

Place a complete compatible model in the local model folder. This small public model remains an optional example, not a required or bundled model:

```bash
cd /srv/llmcontroller/LLMs
wget -O DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf "https://huggingface.co/bartowski/DeepSeek-R1-Distill-Qwen-1.5B-GGUF/resolve/main/DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf"
```

Review the chosen model's license and requirements. For gated models, follow the model host's download instructions with your own credentials. Finish the download before rescanning in CE; a partial file is not a usable model.

A separate title model is optional. [Title generation](docs/MODELS.md#chat-title-generation) normally uses the active main model and only uses a dedicated runtime when the existing lifecycle can host it.

### 8. Build or provide llama-server

CE does not build `llama-server`. Supply a compatible executable and verify it from the shell before configuring CE to use it. Packaged binaries, provider-supplied builds, CPU builds, and compatible GPU builds are valid options.

For image understanding, the runtime must support the selected model/projector pair, `--mmproj`, and OpenAI-style `image_url` content with inline data URLs. See the upstream [server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) and [multimodal documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md) for your runtime version.

For a source build, obtain a source archive from the [llama.cpp project](https://github.com/ggml-org/llama.cpp) and extract it so the source root is `/srv/llama.cpp`. Make that source/build folder writable by the account performing the build.

NVIDIA CUDA hosts need a compatible driver and CUDA Toolkit before building:

```bash
cd /srv/llama.cpp
cmake -S . -B build \
  -DGGML_CUDA=ON \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -- -j "$(nproc)"
./build/bin/llama-server --list-devices
```

Existing CUDA architecture examples are `-DCMAKE_CUDA_ARCHITECTURES=70` for Tesla V100 and `-DCMAKE_CUDA_ARCHITECTURES=120` for RTX PRO 6000 Blackwell / compute-capability-12.0-class cards. Use a toolkit and build compatible with the actual GPU; these are examples, not interchangeable settings.

Existing AMD ROCm/HIP example for MI300X:

```bash
cd /srv/llama.cpp
HIPCXX="$(hipconfig -l)/clang" HIP_PATH="$(hipconfig -R)" \
cmake -S . -B build \
  -DGGML_HIP=ON \
  -DGPU_TARGETS=gfx942 \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -- -j "$(nproc)"
```

MI300X uses the `gfx942` target in this example. Managed GPU images may already provide ROCm; do not reinstall a working stack just to follow an example. Inference support and telemetry support depend on the supplied host environment.

### 9. Start the first-run installer manually

Untouched defaults bind the installer to **127.0.0.1:5000**. Keep those defaults when using a browser on the same machine.

For a fresh remote installation, copy the configuration example before starting the app:

```bash
cd /srv/llmcontroller
cp bootstrap_config.example.json bootstrap_config.json
```

Do not overwrite an existing installation's bootstrap configuration. In the new file, configure the following separately:

| Setting | Meaning |
| --- | --- |
| `app_host` | An address the server can listen on, such as `0.0.0.0` or a local interface address. |
| `app_port` | The application port; `5000` in this example. |
| `cors-allowed-origins` | The actual browser-visible origin, including scheme, hostname/IP, and port. |

`0.0.0.0` is a listen value, **not a browser origin**. For example, a browser connecting to `http://<VM_IP>:5000` must use that actual origin, not `http://0.0.0.0:5000`. Match the real HTTPS origin when using a reverse proxy.

Start CE:

```bash
cd /srv/llmcontroller
./venv/bin/python app.py
```

For local setup, open `http://127.0.0.1:5000/`. For remote setup, open the actual configured browser origin through the intended private/restricted access path.

### 10. Complete installer Step 1

Step 1 asks for the application host/port, CORS accepted origins, database host/name/username/password, initial administrator email/password, and default password complexity.

It writes `bootstrap_config.json`, tests the database credentials, imports the fresh `install/schema.sql` and `install/seed.sql`, and creates the initial administrator.

The page loading does not prove that the origin settings are correct: a mismatched browser origin can still make submission fail. Keep server listen values separate from the browser-visible address.

![The first-run installer](docs/images/installer.png)

### 11. Complete installer Step 2

Review and save the runtime defaults. For the Ubuntu layout above:

| Setting | Example |
| --- | --- |
| llama-server path | `/srv/llama.cpp/build/bin/llama-server` |
| Model scan folder | `/srv/llmcontroller/LLMs` |
| Main llama port | `8080` |
| Title llama port | `8081` |

Other runtime settings include default GPU layers, default CPU threads, and GPU split threshold. After installation, restart CE cleanly so it starts in normal mode with the configured host and port.

Managed main and title `llama-server` processes bind to **127.0.0.1**. Keep those ports private; clients use the authenticated CE application endpoint.

Optional configuration is documented in its primary guide: [title generation](docs/MODELS.md#chat-title-generation), [image models](docs/MODELS.md#image-capable-models), and [API access](docs/API.md#enable-access). None is required to complete normal typed-chat setup.

For image support, the normal application requirements supply Pillow (`>=11.3,<13`) and `pillow-heif` (`>=1.1.1,<2`). No extra image models or browser decoder libraries are required beyond the compatible language-model/projector pair. Custom platforms without matching wheels must supply the required codecs.

### 12. Stop the manual app process

After installation, stop the manually running app with **Ctrl+C** in its terminal before starting a service instance. Do not leave two app processes competing for ports and runtime ownership.

### 13. Install Gunicorn into the same virtual environment

For the following systemd example:

```bash
cd /srv/llmcontroller
./venv/bin/python -m pip install gunicorn
```

### 14. Create and enable the systemd service

Create `/etc/systemd/system/llmcontroller.service`:

```bash
sudo nano /etc/systemd/system/llmcontroller.service
```

Use the existing single-worker service definition, adapting paths, listen address, MySQL service name, and service-account policy to the host before enabling it:

```ini
[Unit]
Description=LLM Controller CE
After=network-online.target mysql.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/srv/llmcontroller
Environment="PYTHONUNBUFFERED=1"
ExecStart=/srv/llmcontroller/venv/bin/gunicorn --workers 1 --worker-class gevent --bind 0.0.0.0:5000 --access-logfile - --error-logfile - --capture-output --timeout 300 app:app
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

`--bind 0.0.0.0:5000` listens on all interfaces when network rules allow it. It remains separate from CORS accepted origins. Preserve the **single-worker** runtime-ownership model; this is not a multi-worker scaling recipe.

```bash
sudo systemctl daemon-reload
sudo systemctl enable llmcontroller
sudo systemctl start llmcontroller
```

### 15. Management and log commands

```bash
sudo systemctl restart llmcontroller
sudo systemctl stop llmcontroller
sudo systemctl status llmcontroller --no-pager
sudo journalctl -u llmcontroller -f
sudo journalctl -u llmcontroller -n 100 --no-pager
```

An enabled service starts again at the normal multi-user boot target. CE service startup does **not** automatically start the optional speech model.

### Optional: Troubleshooting llama-server

A direct runtime launch can distinguish a model/runtime/driver issue from an application configuration issue. Stop the CE-managed model first, ensure the test port is free, and account for other GPU workloads:

```bash
/srv/llama.cpp/build/bin/llama-server \
  -m /srv/llmcontroller/LLMs/DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf \
  --host 127.0.0.1 \
  --port 8080 \
  -ngl 999
```

For a compatible multimodal pair, add `--mmproj /path/to/projector.gguf`. Do not bind this troubleshooting runtime publicly. Stop it before returning ownership to CE.

## Optional: Local voice dictation

Normal CE chat works without speech software, a speech model, CUDA prerequisites, or a configured speech interpreter. Voice uses a **separate operator-installed environment** and compatible local `.nemo` model. CE does not install those components or NVIDIA drivers.

Follow the [Local Voice Dictation guide](VOICE_INSTALL.md) for the pinned Windows/NVIDIA CUDA setup, explicit Start/Stop controls, microphone requirements, and troubleshooting.

## Attachment and import limits in v1.4

The current limits and retention rules are maintained in [Chat, Projects, and files](docs/CHAT.md#attachment-limits). v1.4 supports per-file settings up to 10 MiB and combined settings up to 40 MiB without changing existing defaults. The API retains its separate 16 MiB body limit.

The old fixed 25,000-row import ceiling is removed. **Maximum Chat Import Size**, integrity/schema/ownership checks, and practical host-memory/request-time limits still apply. See [Import and export](docs/CHAT.md#import-and-export).

## Windows default runtime layout

The Windows notes assume that Python, MySQL, the application requirements, and the first-run application setup described above have been prepared for the Windows host. SQL actions remain manual operator steps; CMD input redirection can be used from PowerShell as shown under upgrades.

The default layout contains:

```text
<LLM_CONTROLLER_DIR>/
  app.py
  start_llm_controller.bat
  llama-server/
    llama-server.exe
    ...required runtime DLLs...
```

Common runtime DLLs include `ggml.dll`, `ggml-base.dll`, `ggml-cpu.dll`, and `llama.dll`. CUDA builds may also need `ggml-cuda.dll` and other DLLs supplied with the compiled runtime. Keep the matching runtime files together.

For a Windows CUDA source build, install compatible Visual Studio Build Tools, CMake, and an NVIDIA CUDA Toolkit. Obtain and extract the llama.cpp source archive into a source directory such as `C:\src\llama.cpp`:

```powershell
cd C:\src\llama.cpp
cmake -S . -B build -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j 16
.\build\bin\Release\llama-server.exe --list-devices
```

Optional architecture-specific configuration examples:

```powershell
# Tesla V100
cmake -S . -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=70 -DCMAKE_BUILD_TYPE=Release

# RTX PRO 6000 Blackwell / compute capability 12.0 class
cmake -S . -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120 -DCMAKE_BUILD_TYPE=Release
```

Choose the appropriate configuration before building. Copy the executable and matching DLLs into the CE runtime directory, using your actual application path:

```powershell
$ControllerDir = 'E:\LLM-Controller' # The folder containing app.py.
New-Item -ItemType Directory -Path "$ControllerDir\llama-server" -Force
Copy-Item .\build\bin\Release\llama-server.exe "$ControllerDir\llama-server\"
Copy-Item .\build\bin\Release\*.dll "$ControllerDir\llama-server\"
```

## Windows run notes

The default convenience launcher is:

```cmd
start_llm_controller.bat
```

Other launch methods are valid when they use the same application environment. For the running Command Prompt window, the existing operational guidance is to disable **QuickEdit Mode**, **Insert Mode**, **Enable line wrapping selection**, and **Extended text selection keys** in the console properties to reduce accidental console-interaction problems.

## Storage and host dependencies

MySQL stores system data; `chats.sqlite` stores chat/workspace data; bootstrap values live in local configuration. Runtime defaults are stored in `llm_app_settings`. The model scan directory is configurable rather than fixed to the example `LLMs` location.

NVIDIA and AMD telemetry tools are optional host-side dependencies. Install and verify `nvidia-smi`, `rocm-smi`, or `rocminfo` as appropriate for the existing host stack; CE does not install them.

[Administration and backups](docs/ADMIN.md) · [Models](docs/MODELS.md) · [Monitoring](docs/MONITORING.md)

Developed by **Tensioncore Administration Services**.
