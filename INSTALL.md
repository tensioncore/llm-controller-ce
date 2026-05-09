# INSTALL.md

# LLM Controller CE — Install Notes

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

`pip install Flask Flask-Bcrypt Flask-Cors Flask-SocketIO Flask-WTF gevent mysql-connector-python requests`

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

## Important Runtime Note

LLM Controller CE does not build `llama-server` for you.

Your `llama-server` build must already work on your system and must be compatible with:

* your host OS and hardware
* your CUDA or ROCm environment, if using GPU acceleration
* your GPU architecture
* the DLLs or shared libraries required by your compiled runtime

## Run the App

On Windows, start LLM Controller CE using:

`start_llm_controller.bat`

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
