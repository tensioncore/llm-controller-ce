# INSTALL.md

# LLM Controller CE — Installation Guide

LLM Controller CE uses a first-run web installer.

## Requirements

You will need:

* Python 3
* MySQL
* A working `llama-server` runtime compatible with the host OS and hardware
* Local GGUF model files
* A writable install folder
* For GPU telemetry, NVIDIA tools such as `nvidia-smi` or AMD ROCm tools such as `rocm-smi` or `rocminfo`, where applicable

Windows and Linux/Ubuntu are supported when Python, MySQL, and a compatible `llama-server` runtime are configured for the host.

## Python Packages

Install the required Python packages with:

```bash
pip install Flask Flask-Bcrypt Flask-Cors Flask-SocketIO Flask-WTF gevent mysql-connector-python requests
```

## Required Application Files

Your install should include:

* `app.py`
* `install/schema.sql`
* `install/seed.sql`
* a local models folder such as `LLMs/`

## Windows Default Runtime Layout

The default Windows layout expects:

* `llama-server/llama-server.exe`
* required llama runtime DLLs beside it
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

## Linux/Ubuntu Runtime Layout

On Linux/Ubuntu, configure the `llama-server` executable path during installation or later in Installation & Settings.

Make sure the binary is executable and works from the shell before pointing LLM Controller CE at it.

Run the Python app using the operator's chosen shell, supervisor, or service method.

## Ubuntu/Linux Service Deployment

This is a supported service example, not the only valid Linux deployment pattern.

For Ubuntu/Linux service installs, the recommended application folder is:

`/srv/llmcontroller`

Place or extract the LLM Controller CE application files into that folder before creating the service.

Install the base OS packages:

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv mysql-server
```

Create and activate a Python virtual environment:

```bash
sudo mkdir -p /srv/llmcontroller
sudo chown -R "$USER":"$USER" /srv/llmcontroller
cd /srv/llmcontroller
python3 -m venv venv
source venv/bin/activate
```

Install the Python package requirements, including Gunicorn for the service runtime:

```bash
pip install Flask Flask-Bcrypt Flask-Cors Flask-SocketIO Flask-WTF gevent mysql-connector-python requests gunicorn
```

Before installing the system service, run the app manually so the first-run web installer can complete:

```bash
python3 app.py
```

Open the installer in a browser, complete Step 1, then complete Step 2 with the Linux runtime settings for this host. See the [Web Installer guide](#first-start) below for the detailed installer flow.

The Linux runtime settings should point to the configured `llama-server` binary and the local model folder. LLM Controller CE does not build or install `llama-server`; the binary must already be installed separately and working from the shell.

After installer completion, stop the manual process with `Ctrl+C`.

Create the systemd service file:

```bash
sudo nano /etc/systemd/system/llmcontroller.service
```

Use this service definition:

This example assumes the main Flask file is `app.py` and the Flask app object is named `app`, which is why the Gunicorn command ends with `app:app`.

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

The `--bind 0.0.0.0:5000` setting makes the app reachable from the host network when firewall and network rules allow it. You may change the bind address for local-only or reverse-proxy-only deployments.

If the application folder, bind address, port, or MySQL service name differs on your host, adjust the service file before enabling it.

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable llmcontroller
sudo systemctl start llmcontroller
```

Basic service management commands:

```bash
sudo systemctl restart llmcontroller
sudo systemctl stop llmcontroller
sudo systemctl status llmcontroller --no-pager
```

Basic log commands:

```bash
sudo journalctl -u llmcontroller -f
sudo journalctl -u llmcontroller -n 100 --no-pager
```

Because the service is enabled under `multi-user.target`, it will start again automatically after reboot when systemd reaches the normal multi-user boot target.

AMD ROCm and NVIDIA telemetry tools are optional host-side dependencies for GPU reporting where applicable. Install and verify tools such as `rocm-smi`, `rocminfo`, or `nvidia-smi` separately according to the host GPU stack; they are not installed by LLM Controller CE.

## Important Runtime Note

LLM Controller CE does not build `llama-server` for you.

Your `llama-server` build must already work on your system and must be compatible with:

* your host OS and hardware
* your CUDA or ROCm environment, if using GPU acceleration
* your GPU architecture
* the DLLs or shared libraries required by your compiled runtime

## Run the App

On Windows, start LLM Controller CE using:

```cmd
start_llm_controller.bat
```

This batch file is the default convenience launcher. Other launch methods are fine if they start the same app environment.

On Linux/Ubuntu, start the Python app using your chosen shell or service method.

## Windows-Only Command Prompt Settings

To reduce the chance of the running app console freezing or misbehaving, open the Command Prompt window properties and disable the following:

* **QuickEdit Mode**
* **Insert Mode**
* **Enable line wrapping selection**
* **Extended text selection keys**

These settings help prevent accidental console interaction while LLM Controller CE is running.

## First Start

Make sure MySQL is reachable before starting the app.

On a fresh install with no bootstrap config present, LLM Controller CE starts in installer mode at:

`http://127.0.0.1:5000/`

## Installer Step 1

Step 1 asks for:

* App host
* App port
* Optional CORS origin
* Database host
* Database name
* Database username
* Database password
* Initial admin email
* Initial admin password
* Default password complexity

Step 1 then:

* writes bootstrap config `bootstrap_config.json`
* imports `install/schema.sql`
* imports `install/seed.sql`
* creates the initial admin account

## Installer Step 2

Step 2 lets you review and save default runtime settings, including:

* llama-server path
* model scan folder
* llama main port
* llama title port
* default GPU layers
* default CPU threads
* GPU split threshold

## Restart Required

After installation completes, restart the app.

On the next launch, LLM Controller CE will start in normal mode using your configured host and port.

## Notes

* LLM Controller uses MySQL for system data and SQLite for chat history
* Default first-run installer bind is `127.0.0.1:5000`
* Database bootstrap values are stored in the local bootstrap config
* Runtime defaults are stored in the database table `llm_app_settings`
* The default model scan folder is typically `LLMs`

## Attribution

LLM Controller CE is developed by **Tensioncore Administration Services**.
