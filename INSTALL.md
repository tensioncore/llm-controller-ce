# INSTALL.md

# LLM Controller CE — Install Notes

LLM Controller CE uses a first-run web installer on Windows.

## Requirements

You will need:

*   Windows
*   Python 3
*   MySQL
*   A working `llama-server` runtime
*   Local GGUF model files

## Python Packages

Install the required Python packages with:

`pip install Flask Flask-Bcrypt Flask-Cors Flask-SocketIO Flask-WTF gevent mysql-connector-python requests`

## Required Folders / Files

Your install should include:

*   `app.py`
*   `install/schema.sql`
*   `install/seed.sql`
*   `llama-server/llama-server.exe`
*   required llama runtime DLLs
*   a local models folder such as `LLMs/`

Example llama runtime files may include (and the default expected location):

*   `llama-server/llama-server.exe`
*   `llama-server/ggml.dll`
*   `llama-server/ggml-base.dll`
*   `llama-server/ggml-cpu.dll`
*   `llama-server/llama.dll`

CUDA builds may also require:

*   `llama-server/ggml-cuda.dll`
*   other DLLs included with your compiled runtime

## Important Runtime Note

LLM Controller CE does not build `llama-server` for you.

Your `llama-server` build must already work on your system and must be compatible with:

*   your CUDA version, if using GPU
*   your GPU architecture
*   the DLLs included beside `llama-server.exe`

## Run the App

Start LLM Controller CE using:

`start_llm_controller.bat`

## Recommended Command Prompt Settings

To reduce the chance of the running app console freezing or misbehaving, open the Command Prompt window properties and disable the following:

*   **QuickEdit Mode**
*   **Insert Mode**
*   **Enable line wrapping selection**
*   **Extended text selection keys**

These settings help prevent accidental console interaction while LLM Controller CE is running.

## Optional Console Appearance

If desired, you can also set the Command Prompt text color to green for a classic runtime console look.

## First Start

On a fresh install with no bootstrap config present, LLM Controller CE starts in installer mode at:

`http://127.0.0.1:5000/`

## Installer Step 1

Step 1 asks for:

*   App host
*   App port
*   Database host
*   Database name
*   Database username
*   Database password
*   Initial admin email
*   Initial admin password
*   Default password complexity
*   Optional public app origin (CORS)

Step 1 then:

*   writes bootstrap config `bootstrap_config.json`
*   imports `install/schema.sql`
*   imports `install/seed.sql`
*   creates the initial admin account

## Installer Step 2

Step 2 lets you review and save default runtime settings, including:

*   llama-server path
*   model scan folder
*   default GPU layers
*   default CPU threads
*   GPU split threshold
*   llama main port
*   llama title port

## Restart Required

After setup completes, restart the app.

On the next launch, LLM Controller CE will start in normal mode using your configured host and port.

## Notes

*   Default first-run installer bind is `127.0.0.1:5000`
*   Database bootstrap values are stored in the local bootstrap config
*   Runtime defaults are stored in the database table `llm_app_settings`
*   The default model scan folder is typically `LLMs`
*   Future improvements to the installer are planned

## Attribution

LLM Controller CE is developed by **Tensioncore Administration Services**.
