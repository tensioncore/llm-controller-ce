# LLM Controller CE — Windows installation

[← Installation and upgrades](INSTALL.md) · [Documentation](docs/README.md) · [Optional local voice](VOICE_INSTALL.md)

Follow this numbered procedure for a **fresh Windows x64 installation**. For an existing database, follow the [upgrade instructions](INSTALL.md#existing-installation-upgrades). If a step fails, use [Troubleshooting](#13-troubleshooting) before continuing.

The operator-validated L03 reference was a Lenovo Yoga 9i running **Windows 10 Pro**, with an Intel Core i9-10980HK, 16 GB RAM, and an NVIDIA GTX 1650 Ti Max-Q with 4 GB VRAM. It used Python 3.11 in a virtual environment, MySQL Server 8.0.46 x64, NVIDIA driver 561.09, CUDA Toolkit 12.6.85, Visual Studio 2022 Build Tools/MSVC, CMake 4.4, and Ninja. Database initialization, CPU/CUDA inference, image inference, model switching, chat persistence, GPU monitoring, and a six-model benchmark succeeded on that deployment.

These are reference versions and hardware, not universal requirements. L03 does not validate Windows 11 or other machines. No exact llama.cpp revision was supplied with that reference; record the runtime release or source archive version you deploy.

## 1. Prerequisites

Use an account that can install host software and write to the CE folder. Internet access is needed to obtain software, Python packages, models, and optional document artifacts. Use elevated PowerShell only for host installation steps that require it; run CE as your normal user.

| Component | Requirement |
| --- | --- |
| Python | 64-bit Python 3.11; see Step 2. |
| Database | Local MySQL Server 8.0 and a dedicated application account. |
| Runtime | A compatible Windows x64 `llama-server.exe`, with matching DLLs. |
| Models | Complete GGUF models sized for available RAM/VRAM. |
| NVIDIA acceleration | A supported NVIDIA GPU and compatible driver/runtime. Source compilation also needs the CUDA Toolkit and C++ build tools. |

This guide uses the following layout. Substitute your own paths consistently if needed.

| Purpose | Path |
| --- | --- |
| Application | `C:\llm-controller-ce\` |
| Python environment | `C:\llm-controller-ce\venv\` |
| Runtime executable and DLLs | `C:\llm-controller-ce\llama-server\` |
| Models | `C:\llm-controller-ce\LLMs\` |
| Separate llama.cpp source/build tree | `C:\src\llama.cpp\` |

Commands are **PowerShell** unless marked `cmd` or SQL. Steps 7–8 use the **x64 Native Tools Command Prompt for Visual Studio 2022** when building from source.

## 2. Install Python

Install 64-bit Python 3.11 from [Python's Windows downloads](https://www.python.org/downloads/windows/), including pip and the Python launcher. [Python 3.11.9](https://www.python.org/downloads/release/python-3119/) was the last 3.11 release with official Windows binary installers; later 3.11 security releases are source-only.

```powershell
py -3.11 --version
py -3.11 -c "import struct; print(struct.calcsize('P') * 8)"
```

Confirm `Python 3.11.x` and `64`. The current requirements impose a **Python 3.11 minimum**: the pinned [ONNX Runtime 1.30.0](https://pypi.org/project/onnxruntime/1.30.0/) requires Python 3.11 or newer and supplies a Windows x64 CPython 3.11 wheel. CE has no separate declared Python support range; dependency eligibility alone does not validate newer Python versions. Use 3.11 for this procedure.

## 3. Install and configure MySQL Server

Download Oracle's [MySQL Installer for Windows](https://dev.mysql.com/downloads/installer/) for the 8.0 series. Run its MSI to install MySQL Installer, then close the graphical installer before using the console to install the server. The full installer bundle can reduce package downloads. This fresh-install example uses the L03-tested **Server 8.0.46 x64** package.

Open **PowerShell as Administrator**. Set the console path and enter a new root password interactively. For this command's delimiter-based syntax, use a long random password made from letters, digits, and characters such as `!@#_-`; keep it in your password manager.

```powershell
$installer = 'C:\Program Files (x86)\MySQL\MySQL Installer for Windows\MySQLInstallerConsole.exe'
$rootSecret = Read-Host 'New MySQL root password' -AsSecureString
try {
    $rootPassword = ([System.Net.NetworkCredential]::new('', $rootSecret)).Password
    & $installer --install "server;8.0.46;x64:*:type=main;password=$rootPassword;tcp_ip=true;port=3306;server_type=Developer;as_windows_service=true;windows_service_name=MySQL80;open_windows_firewall=false" --silent
    if ($LASTEXITCODE -ne 0) { throw 'MySQL installation failed; review the installer output.' }
} finally {
    $rootPassword = $null
    $rootSecret.Dispose()
}
Get-Service -Name MySQL80
```

The prompt avoids typing the password into shell history, but the installer receives it in **process arguments**, where operating-system tools with sufficient access may see it. Clearing the variable does not guarantee erasure from memory.

Confirm that `MySQL80` is **Running**. This configuration uses TCP port **3306**, the Developer server profile, and a Windows service. It does **not** open Windows Firewall; local CE does not need a public MySQL port. Oracle documents the console's administrative requirements and product selection syntax in its [console reference](https://dev.mysql.com/doc/mysql-installer/en/MySQLInstallerConsole.html); the `type=main` command above is the syntax verified on L03.

## 4. Create the database and application account

These are manual operator actions. Open the MySQL client, entering the root password at its prompt:

```powershell
& 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe' --host=localhost --port=3306 --user=root --password
```

At the MySQL prompt, run the following SQL after replacing `REPLACE_WITH_YOUR_APP_PASSWORD` with a separate strong application password. Enter SQL in the MySQL client, not PowerShell; escape any SQL quote/backslash characters in your chosen password appropriately.

```sql
CREATE DATABASE llmcontroller CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'llmcontroller'@'localhost' IDENTIFIED BY 'REPLACE_WITH_YOUR_APP_PASSWORD';
GRANT ALL PRIVILEGES ON llmcontroller.* TO 'llmcontroller'@'localhost';
EXIT;
```

The account has privileges only on `llmcontroller.*`; CE should use this account, not root. Creating the database here establishes `utf8mb4` / `utf8mb4_unicode_ci` before the web installer runs. **Do not manually import `install/schema.sql` or `install/seed.sql`**: Step 11 performs that initialization.

## 5. Download and prepare CE

Download the intended CE application archive from the [release downloads](https://github.com/tensioncore/llm-controller-ce/releases). Extract the complete distribution so that `app.py` is directly inside `C:\llm-controller-ce\`, without an extra archive-named directory. Keep `requirements.txt`, `install/schema.sql`, `install/seed.sql`, templates, static files, and the other application files together.

In normal PowerShell:

```powershell
Set-Location 'C:\llm-controller-ce'
New-Item -ItemType Directory -Path '.\LLMs', '.\llama-server' -Force
```

Download a small compatible GGUF chat model into `C:\llm-controller-ce\LLMs\`. Review its license and memory requirements and finish all downloads before scanning. A vision model/projector pair, dedicated title model, and speech model are optional.

## 6. Install Python dependencies

Create the environment under the application root and use its interpreter explicitly:

```powershell
Set-Location 'C:\llm-controller-ce'
py -3.11 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

Use the distribution's current `requirements.txt` as the package contract. For local PDF conversion, also complete [Local Docling document support](INSTALL.md#local-docling-document-support) using this same environment. Optional voice uses its [separate environment](VOICE_INSTALL.md).

## 7. Prepare the Windows runtime tools

**Prefer a compatible prebuilt runtime when one is available for your host.** Download a Windows x64 CPU or CUDA package from the [official llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases), along with any matching CUDA DLL package specified by that release. Keep files from the same release/backend together. Extract the runtime files into `C:\llm-controller-ce\llama-server\`, with `llama-server.exe` directly in that folder, then continue at **Step 9's final-location verification**. A prebuilt runtime does not require a compiler, Ninja, or a full CUDA development toolkit; it does require the release's runtime dependencies and a compatible driver for CUDA.

For the **L03-validated CUDA source build**, install:

- [Git for Windows](https://git-scm.com/install/windows), available on PATH for source/build tooling.
- [Visual Studio 2022 Build Tools](https://visualstudio.microsoft.com/vs/older-downloads/), with **Desktop development with C++**, the MSVC x64/x86 compiler, and a Windows SDK.
- [CMake](https://cmake.org/download/), on PATH, and [Ninja](https://ninja-build.org/), with `ninja.exe` on PATH. Visual Studio's C++ CMake tools can also supply these.
- An NVIDIA driver and [CUDA Toolkit](https://developer.nvidia.com/cuda-toolkit-archive) compatible with your GPU, Windows version, and MSVC compiler.

L03 used CUDA **12.6.85** with driver **561.09**; neither is mandatory for other hosts. Check the selected toolkit's release notes and [Windows compiler/OS requirements](https://docs.nvidia.com/cuda/cuda-installation-guide-microsoft-windows/index.html), including archived documentation when using CUDA 12.6. NVIDIA's [driver compatibility guidance](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html) requires driver branch 580 or newer for CUDA 13.x; do not pair a CUDA 13 runtime with L03's 561.09 driver. CUDA minor-version compatibility has feature/PTX limitations, so check the actual runtime's requirements too.

Open **x64 Native Tools Command Prompt for VS 2022** from the Start menu after installing the tools. Confirm the tools are available in that window:

```cmd
where cl
cmake --version
ninja --version
nvcc --version
nvidia-smi
```

## 8. Build llama.cpp with CUDA

For a source build, download a source archive for the chosen [llama.cpp release](https://github.com/ggml-org/llama.cpp/releases) and extract it so `CMakeLists.txt` is directly in `C:\src\llama.cpp\`. Keep this source/build tree separate from CE. Refer to the matching [upstream build instructions](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md) for that version.

Look up your exact GPU in NVIDIA's [compute-capability table](https://developer.nvidia.com/cuda/gpus) or [legacy table](https://developer.nvidia.com/cuda/gpus/legacy). Remove the decimal point for CMake: compute capability **7.5 → `75`**. The `75` below is L03's Turing target; **replace it for a different GPU** and ensure the selected toolkit supports that architecture.

In the **x64 Native Tools Command Prompt for VS 2022**:

```cmd
cd /d C:\src\llama.cpp
cmake -S . -B build-ninja -G Ninja -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=75 -DCMAKE_BUILD_TYPE=Release
cmake --build build-ninja --target llama-server -j 8
build-ninja\bin\llama-server.exe --list-devices
```

Proceed after configuration and compilation succeed and your CUDA GPU appears. `-j 8` controls build parallelism; reduce it on hosts with less available memory. The Ninja Release output is `build-ninja\bin\`, with no additional `Release` subfolder.

## 9. Deploy and verify llama-server

For a source build, return to **PowerShell** and copy the complete output directory, including dependent DLLs:

```powershell
New-Item -ItemType Directory -Path 'C:\llm-controller-ce\llama-server' -Force
Copy-Item 'C:\src\llama.cpp\build-ninja\bin\*' 'C:\llm-controller-ce\llama-server\' -Recurse -Force
```

For **both prebuilt and source-built runtimes**, verify the final application location from normal PowerShell:

```powershell
Set-Location 'C:\llm-controller-ce'
.\llama-server\llama-server.exe --version
.\llama-server\llama-server.exe --help
.\llama-server\llama-server.exe --list-devices
```

Confirm the executable starts and, for CUDA, lists the intended GPU. A CPU-only runtime does not need to list a CUDA device. Keep the executable and matching DLLs together; copying only the EXE is insufficient for a shared-library build.

CE resolves the default `llama-server/llama-server.exe` path relative to the application folder. It launches managed runtimes itself with model, GPU-layer, thread, context, parallelism, `--fit`, host, and port arguments. Use a runtime whose `--help` includes those options; image inference also needs `--mmproj` and support for the selected model/projector pair. Main and title runtimes bind to `127.0.0.1` on separate ports (defaults **8080** and **8081**). No separately running llama-server process is needed for installation.

## 10. Configure and launch CE

For this local installation, leave the bootstrap defaults in place: CE initially listens at **127.0.0.1:5000**. The web installer will write the database and application configuration.

Use the virtual environment directly every time you launch CE:

```powershell
Set-Location 'C:\llm-controller-ce'
.\venv\Scripts\python.exe app.py
```

Keep that terminal open and open **http://127.0.0.1:5000/** in a browser on the same machine. Complete local setup before configuring access from other machines.

The bundled `start_llm_controller.bat` is also supported: it selects the application's `venv\Scripts\python.exe` when present, without activation. If that executable is absent, it uses `python` from PATH; configure PATH to select the intended system Python with CE's requirements installed. The launcher does not create an environment or install dependencies.

## 11. Complete first-run database initialization

In **installer Step 1**, enter:

| Field | Local installation value |
| --- | --- |
| App host | `127.0.0.1` |
| App port | `5000` |
| CORS accepted origins | `http://127.0.0.1:5000` |
| DB host / MySQL port | `localhost` / `3306` |
| Database name / username | `llmcontroller` / `llmcontroller` |
| Database password | The application account password from Step 4. |
| Initial administrator | Your chosen email and a separate administrator password. |
| Default password complexity | Choose the policy; the administrator password must satisfy it. |

Step 1 validates the supplied inputs and database connection, saves `bootstrap_config.json`, imports `install/schema.sql` and `install/seed.sql` into the named database, creates the initial administrator, and applies the password policy. It does not create the MySQL application account. The pre-created database retains its chosen character set and collation.

In **installer Step 2**, review and save:

| Field | Value or guidance |
| --- | --- |
| Llama-server path | `llama-server/llama-server.exe` |
| Default scan folder | `LLMs` |
| Default GPU layers | Choose for your GPU/model capacity; use `0` for CPU-only operation. |
| Default CPU threads | Choose for your CPU and available resources. |
| GPU split threshold (GB) | Review for your GPU layout; the seeded value is `6`. |
| Llama main / title ports | `8080` / `8081`, distinct and available. |

The seeded GPU-layer and CPU-thread values (`360` and `30`) are not hardware sizing recommendations. Set values appropriate to your host, especially on a 4 GB GPU.

Step 2 stores these defaults in `llm_app_settings` and marks setup complete. **Stop CE with Ctrl+C and restart it with the Step 10 command** to load the normal application routes. Sign in with the administrator account you created. Keep `bootstrap_config.json` and `flask_secret.key` private and writable only by the appropriate operator/application account; see [administration and backups](docs/ADMIN.md).

## 12. Verify installation

- [ ] `Get-Service -Name MySQL80` reports **Running**.
- [ ] CE starts with `venv\Scripts\python.exe` without import errors.
- [ ] The web interface loads at `http://127.0.0.1:5000/`.
- [ ] Both installer steps complete and administrator sign-in succeeds after restarting.
- [ ] The runtime starts from `C:\llm-controller-ce\llama-server\`.
- [ ] For CUDA, `--list-devices` detects the intended GPU; check CE's runtime logs for GPU use when loading a model.
- [ ] Use **Rescan Models**, then load a small GGUF model and wait until it is ready.
- [ ] Send a short chat message, receive a response, and reopen the conversation to confirm persistence.

See [model controls](docs/MODELS.md) and [runtime logs and monitoring](docs/MONITORING.md) for normal operation. A benchmark or stress test is not required to finish installation.

## 13. Troubleshooting

### MySQL installer rejects a parameter

The tested installer accepts `password` or `root_password`, **not `root_passwd`**. Use `type=main`; `type=config` is deprecated in the tested installer, even though older Oracle examples show it. To inspect server settings before installation, run:

```powershell
& "C:\Program Files (x86)\MySQL\MySQL Installer for Windows\MySQLInstallerConsole.exe" --install "server;8.0.46;x64" --show-settings
```

Preserve the Step 3 product/version/configuration structure and avoid unescaped delimiter characters in the password argument.

### MySQL package download fails or the version is unavailable

Use Oracle's full [MySQL Installer bundle or archive](https://downloads.mysql.com/archives/installer/) containing the intended server package, and confirm access through your network/proxy. The console's `--update` refreshes its catalog; `--list` shows available products. A catalog refresh does not guarantee an old package remains downloadable. Use an available compatible 8.0 package deliberately if necessary, recording the version; do not silently substitute another server series. Check installer output and `Get-Service MySQL80` before proceeding. Never remove an existing MySQL data directory to retry a download.

### `ModuleNotFoundError: No module named 'gevent'` or another import error

The v1.5 `start_llm_controller.bat` selects the application's `venv\Scripts\python.exe` when present. Older launchers used PATH Python even when dependencies were installed in `venv`. Use Step 10's explicit interpreter to confirm the environment, and reinstall requirements with that same interpreter if installation was incomplete.

If the local venv executable is absent, the launcher falls back to PATH Python. In that case, run `where python` in Command Prompt and confirm the first result is your intended system interpreter. A present but broken venv is not bypassed automatically; repair it manually. No PowerShell activation-policy change is needed for the primary procedure. If pip rejects a package, confirm Python 3.11 x64 and resolve the reported prerequisite; do not bypass the requirements with `--no-deps` or remove pins.

### `No CUDA toolset found` or CUDA compiler detection fails

L03's Visual Studio CMake-generator attempt failed with `No CUDA toolset found`; the **Ninja** procedure in Steps 7–8 succeeded. Use the x64 Native Tools prompt, confirm `cl`, `nvcc`, `cmake`, and `ninja` are available there, and configure into `build-ninja` rather than reusing a Visual Studio generator's build directory. Match MSVC to the selected toolkit's supported compiler versions. If multiple CUDA toolkits are installed, check `where nvcc` and select the intended toolkit environment before configuring; do not bypass compiler compatibility checks.

### Missing runtime DLLs

Copy the complete matching build output or extract all required release packages together. Common files include `llama.dll`, `ggml.dll`, `ggml-base.dll`, `ggml-cpu.dll`, and, for CUDA, `ggml-cuda.dll`; exact contents depend on the release. CUDA builds may also need the matching CUDA runtime/cuBLAS DLLs. Follow the release's dependency instructions rather than downloading individual DLLs from unrelated sites. For missing `VCRUNTIME`/`MSVCP` DLLs, install Microsoft's [supported x64 Visual C++ Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist). Verify again in normal PowerShell at the final runtime location.

### CUDA GPU is missing or a model will not load

Confirm `nvidia-smi` sees the intended GPU, that the runtime includes its CUDA backend, and that its driver, CUDA version, and compiled architecture match the host. `nvidia-smi`'s CUDA version indicates driver capability, not the installed compiler version; use `nvcc --version` for the toolkit. A prebuilt runtime need not have `nvcc` installed. GPU telemetry alone does not prove inference works. If the GPU is detected but loading runs out of memory, use a smaller model/context or fewer GPU layers and inspect the runtime log.

### Database authentication or configuration fails

Confirm the `MySQL80` service, `localhost:3306`, database name, application username/password, and the `'llmcontroller'@'localhost'` account with privileges on `llmcontroller.*`. Operators can check that account manually with the Step 4 client command using `--user=llmcontroller`. Use the application's password, not root's. After a configuration change, restart CE and retry the failed installer step. Do not import schema/seed manually or delete data to bypass an error.

### Installer form or browser connection fails

For local setup, use `http://127.0.0.1:5000` consistently for the browser and accepted origin. Refresh an expired form. A listen address such as `0.0.0.0` is not a browser origin. If intentionally enabling remote access later, configure the actual browser-visible origin separately, restrict network access, and use HTTPS for credentials/private data over untrusted networks.

### CE appears paused after selecting text in Command Prompt

For the classic Command Prompt window, the existing launcher recommends disabling **QuickEdit Mode**, **Insert Mode**, **Enable line wrapping selection**, and **Extended text selection keys** in console properties. Keep the CE terminal open while the app runs.
