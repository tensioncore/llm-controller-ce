# LLM Controller CE - Installation Guide

LLM Controller CE uses a first-run web installer. This guide gives one ordered Ubuntu/Linux fresh install flow from a new VM to a running service, plus Windows runtime notes.

## Requirements

You will need:

* Python 3
* MySQL
* A working `llama-server` runtime compatible with the host OS and hardware
* Local GGUF model files
* A writable install folder
* For GPU telemetry, NVIDIA tools such as `nvidia-smi` or AMD ROCm tools such as `rocm-smi` or `rocminfo`, where applicable

Windows and Linux/Ubuntu are supported when Python, MySQL, and a compatible `llama-server` runtime are configured for the host.

## Required Application Files

A complete LLM Controller CE application folder should include:

* `app.py`
* `requirements.txt`
* `install/schema.sql`
* `install/seed.sql`
* a local models folder such as `LLMs/`

## Ubuntu/Linux Fresh Install

This is a supported fresh Ubuntu service-style layout, not the only valid Linux deployment pattern:

* `/srv/llmcontroller`
* `/srv/llmcontroller/LLMs`
* `/srv/llama.cpp`

Follow these steps in order on a brand-new Ubuntu or cloud VM.

### 1. Install OS Prerequisites

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv python3.12-venv git curl wget unzip build-essential cmake pkg-config mysql-server mysql-client ufw
```

### 2. Configure Firewall For Direct Port 5000 Access

```bash
sudo ufw allow OpenSSH
sudo ufw allow 5000/tcp
sudo ufw --force enable
sudo ufw status
```

Firewall and reverse-proxy needs may differ if you deploy behind another proxy, load balancer, or provider firewall.

### 3. Start MySQL And Create The Database User

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

The web installer does not create the MySQL account for you. Before completing installer Step 1, MySQL must be running and the database credentials you enter must have write access to the selected database. Installer Step 1 saves and tests those values.

### 4. Clone Or Extract LLM Controller CE

Create a writable application folder and clone the public release repository:

```bash
cd /srv
sudo mkdir -p /srv/llmcontroller
sudo chown -R "$USER":"$USER" /srv/llmcontroller
git clone https://github.com/tensioncore/llm-controller-ce.git llmcontroller
cd /srv/llmcontroller
```

These commands make `/srv/llmcontroller` writable by the current shell user so Git and Python package installs do not need elevated permissions. If you copy or extract files into `/srv` manually, `sudo` may be required for the folder creation, copy, extract, or ownership steps.

For private or test installs, use an HTTPS token or existing SSH access if required, or extract a release archive so that `app.py` is directly inside `/srv/llmcontroller`.

### 5. Create The Local Model Folder

```bash
mkdir -p /srv/llmcontroller/LLMs
```

### 6. Create The Virtual Environment And Install Requirements

```bash
cd /srv/llmcontroller
python3 -m venv venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.txt
```

If virtual environment creation fails on Ubuntu 24.04 with `ensurepip is not available`, install the matching venv package, then recreate the venv:

```bash
sudo apt install -y python3.12-venv
python3 -m venv venv
```

Do not use `sudo pip`. If package installation fails because of permissions, fix ownership of the application folder, then rerun the venv Python command:

```bash
sudo chown -R "$USER":"$USER" /srv/llmcontroller
```

### 7. Download The Example GGUF Model

Small public example model:

* Repository: `bartowski/DeepSeek-R1-Distill-Qwen-1.5B-GGUF`
* File: `DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf`
* URL: `https://huggingface.co/bartowski/DeepSeek-R1-Distill-Qwen-1.5B-GGUF/resolve/main/DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf`

Download it into the model folder. In v1.0, this exact small GGUF model is required for background chat title generation:

```bash
cd /srv/llmcontroller/LLMs
wget -O DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf "https://huggingface.co/bartowski/DeepSeek-R1-Distill-Qwen-1.5B-GGUF/resolve/main/DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf"
```

For gated Hugging Face models, use your own token according to Hugging Face's download instructions. Keep model files local and point LLM Controller CE at the folder that contains them.

### 8. Build Or Provide llama-server

LLM Controller CE does not build `llama-server` for you. The `llama-server` binary must already work from the shell before you point LLM Controller CE at it.

On Linux, `/srv/llama.cpp` is a convenient source/build folder:

```bash
sudo mkdir -p /srv/llama.cpp
sudo chown -R "$USER":"$USER" /srv/llama.cpp
cd /srv
git clone https://github.com/ggml-org/llama.cpp.git llama.cpp
cd /srv/llama.cpp
```

NVIDIA CUDA hosts need a compatible NVIDIA driver and CUDA Toolkit installed before building. From `/srv/llama.cpp`, build and verify a CUDA-enabled `llama-server` with:

```bash
cd /srv/llama.cpp

cmake -S . -B build \
  -DGGML_CUDA=ON \
  -DCMAKE_BUILD_TYPE=Release

cmake --build build --config Release -- -j "$(nproc)"

./build/bin/llama-server --list-devices
```

Optional CUDA architecture examples:

* Tesla V100: add `-DCMAKE_CUDA_ARCHITECTURES=70`
* RTX PRO 6000 Blackwell / compute capability 12.0 class cards: add `-DCMAKE_CUDA_ARCHITECTURES=120`

Example AMD ROCm/HIP build for MI300X:

```bash
HIPCXX="$(hipconfig -l)/clang" HIP_PATH="$(hipconfig -R)" \
cmake -S . -B build \
  -DGGML_HIP=ON \
  -DGPU_TARGETS=gfx942 \
  -DCMAKE_BUILD_TYPE=Release

cmake --build build --config Release -- -j "$(nproc)"
```

MI300X is normally `gfx942`. Managed cloud GPU images often already include ROCm; do not reinstall ROCm unless your provider requires it. NVIDIA CUDA, CPU-only, packaged binaries, or provider-supplied builds are also valid runtime paths as long as `llama-server` works on the host.

Runtime/offload examples for CUDA builds and general `llama-server` usage:

```bash
/srv/llama.cpp/build/bin/llama-server \
  -m /srv/llmcontroller/LLMs/model.gguf \
  --host 0.0.0.0 \
  --port 8080 \
  -ngl 999
```

Two-GPU layer split:

```bash
/srv/llama.cpp/build/bin/llama-server \
  -m /srv/llmcontroller/LLMs/model.gguf \
  --host 0.0.0.0 \
  --port 8080 \
  -ngl 999 \
  --split-mode layer \
  --tensor-split 1,1
```

Uneven two-GPU split:

```bash
--tensor-split 3,2
```

Partial GPU offload / CPU fallback:

```bash
-ngl 20
```

`-ngl 999` attempts to offload as many layers as possible to GPU. Lower `-ngl` values leave more work on CPU/system RAM. `--tensor-split` uses comma-separated positive proportions such as `1,1` or `3,2`. The `model.gguf` path is an example; replace it with your actual GGUF filename. LLM Controller CE stores comparable runtime settings through the installer/settings UI, including GPU layers and model folder paths, but `llama-server` should work from the shell first.

### 9. Start The First-Run Installer Manually

```bash
cd /srv/llmcontroller
./venv/bin/python app.py
```

For local setup, open http://127.0.0.1:5000/. For a remote cloud VM, open http://<VM_PUBLIC_IP>:5000/.

### 10. Complete Installer Step 1

Installer Step 1 asks for:

* App host
* App port
* CORS accepted origins
* Database host
* Database name
* Database username
* Database password
* Initial admin email
* Initial admin password
* Default password complexity

For local-only browser use on the same machine, `127.0.0.1` is fine:

* `app_host`: `127.0.0.1`
* `app_port`: `5000`
* CORS accepted origins: `http://127.0.0.1:5000`

For remote browser access to a cloud VM, use the public IP or hostname, not `0.0.0.0`:

* `app_host`: `<VM_PUBLIC_IP>`
* `app_port`: `5000`
* CORS accepted origins: `http://<VM_PUBLIC_IP>:5000`

CORS accepted origins must include scheme, host, and port. The installer page may load but form submission can fail with CORS if this is wrong.

Step 1 then:

* writes bootstrap config `bootstrap_config.json`
* tests the database credentials
* imports `install/schema.sql`
* imports `install/seed.sql`
* creates the initial admin account

### 11. Complete Installer Step 2

Installer Step 2 lets you review and save default runtime settings. For the Ubuntu layout above, use:

* llama-server path: `/srv/llama.cpp/build/bin/llama-server`
* model scan folder: `/srv/llmcontroller/LLMs`
* main llama port: `8080`
* title llama port: `8081`

Other runtime settings include:

* default GPU layers
* default CPU threads
* GPU split threshold

After installation completes, restart the app. On the next launch, LLM Controller CE starts in normal mode using your configured host and port.

### 12. Stop The Manual App Process

After installer completion, stop the manually running app with `Ctrl+C` in the terminal where `./venv/bin/python app.py` is running.

### 13. Install Gunicorn Into The Same Virtual Environment

Install Gunicorn into the same virtual environment if you want to use the systemd service example:

```bash
cd /srv/llmcontroller
./venv/bin/python -m pip install gunicorn
```

### 14. Create And Enable The systemd Service

Create the systemd service file:

```bash
sudo nano /etc/systemd/system/llmcontroller.service
```

Use this service definition:

```ini
[Unit]
Description=LLM Controller CE
After=network-online.target mysql.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/srv/llmcontroller
Environment="PYTHONUNBUFFERED=1"
ExecStart=/srv/llmcontroller/venv/bin/gunicorn --workers 1 --worker-class gthread --threads 4 --bind 0.0.0.0:5000 --access-logfile - --error-logfile - --capture-output --timeout 300 app:app
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

The `--bind 0.0.0.0:5000` setting makes the service listen on all interfaces when firewall and network rules allow it. This is separate from the installer `app_host` value and CORS accepted origins, which should use the browser-visible host.

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable llmcontroller
sudo systemctl start llmcontroller
```

Adjust the service file before enabling it if your application folder, bind address, port, MySQL service name, or reverse proxy setup differs.

### 15. Management And Log Commands

Systemd service commands:

```bash
sudo systemctl restart llmcontroller
sudo systemctl stop llmcontroller
sudo systemctl status llmcontroller --no-pager
```

Systemd log commands:

```bash
sudo journalctl -u llmcontroller -f
sudo journalctl -u llmcontroller -n 100 --no-pager
```

Because the service is enabled under `multi-user.target`, it will start again automatically after reboot when systemd reaches the normal multi-user boot target.

## Windows Default Runtime Layout

The default Windows layout expects:

* `llama-server/llama-server.exe`
* required runtime DLLs beside it
* `start_llm_controller.bat`

Example Windows llama runtime files may include:

* `llama-server/llama-server.exe`
* `llama-server/ggml.dll`
* `llama-server/ggml-base.dll`
* `llama-server/ggml-cpu.dll`
* `llama-server/llama.dll`

CUDA builds may also require:

* `llama-server/ggml-cuda.dll`
* other DLLs included with your compiled runtime

Install Visual Studio Build Tools, CMake, Git, and a compatible NVIDIA CUDA Toolkit before building a Windows CUDA runtime.

Simple Windows CUDA build example:

```powershell
cd C:\src
git clone https://github.com/ggml-org/llama.cpp.git
cd llama.cpp

cmake -S . -B build -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j 16

.\build\bin\Release\llama-server.exe --list-devices
```

Optional Windows CUDA architecture examples:

```powershell
# Tesla V100
cmake -S . -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=70 -DCMAKE_BUILD_TYPE=Release

# RTX PRO 6000 Blackwell / compute capability 12.0 class cards
cmake -S . -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120 -DCMAKE_BUILD_TYPE=Release
```

Copy the built runtime into the LLM Controller CE app folder layout:

```powershell
mkdir <LLM_CONTROLLER_DIR>\llama-server -Force
copy .\build\bin\Release\llama-server.exe <LLM_CONTROLLER_DIR>\llama-server\
copy .\build\bin\Release\*.dll <LLM_CONTROLLER_DIR>\llama-server\
```

`<LLM_CONTROLLER_DIR>` is your local LLM Controller CE application folder and should contain `app.py`. Keep the required runtime DLLs beside `llama-server/llama-server.exe`.

## Windows Run Notes

On Windows, start LLM Controller CE using:

```cmd
start_llm_controller.bat
```

This batch file is the default convenience launcher. Other launch methods are fine if they start the same app environment.

To reduce the chance of the running app console freezing or misbehaving, open the Command Prompt window properties and disable:

* **QuickEdit Mode**
* **Insert Mode**
* **Enable line wrapping selection**
* **Extended text selection keys**

These settings help prevent accidental console interaction while LLM Controller CE is running.

## Notes

* LLM Controller CE uses MySQL for system data and SQLite for chat history.
* Database bootstrap values are stored in local bootstrap config.
* Runtime defaults are stored in the database table `llm_app_settings`.
* The default model scan folder is typically `LLMs` on Windows and `/srv/llmcontroller/LLMs` in the Ubuntu example.
* AMD ROCm and NVIDIA telemetry tools are optional host-side dependencies for GPU reporting where applicable. Install and verify tools such as `rocm-smi`, `rocminfo`, or `nvidia-smi` separately according to the host GPU stack; they are not installed by LLM Controller CE.

## Attribution

LLM Controller CE is developed by **Tensioncore Administration Services**.
