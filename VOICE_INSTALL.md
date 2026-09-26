# Local Voice Dictation

[← Product overview](README.md) · [Documentation](docs/README.md) · [Install CE](INSTALL.md)

Voice dictation is an **optional, fully local** Speech-to-Text (S2T) feature in LLM Controller CE v1.4. Record speech in Chat, finish the recording, review the text inserted into the composer, and **Send** it yourself. No cloud transcription service, OpenAI account, or external transcription API key is required. CE does not maintain an audio library or save recordings.

Normal CE chat works without a speech environment, model, configured runtime path, or CUDA prerequisites. A stopped or misconfigured speech runtime does not make speech setup mandatory for CE. Complete the normal [CE installation](INSTALL.md) first, including the v1.4 upgrade when applicable.

[Support](#current-support) · [Hardware](#hardware--resource-expectations) · [Environment](#2-create-the-dedicated-speech-environment) · [Pinned stack](#3-install-the-tested-s2t-stack) · [Model](#5-download-the-recommended-model) · [Start](#9-start-speech-to-text) · [Dictate](#10-dictate) · [Troubleshooting](#troubleshooting)

## Current Support

The validated v1.4 voice setup is **Windows with NVIDIA CUDA**, a separate operator-installed NeMo Python environment, and a local `.nemo` checkpoint. The validated model is **NVIDIA Nemotron 3.5 ASR Streaming 0.6B**. Other models must be compatible with CE's NeMo adapter: local checkpoint restoration, 16 kHz mono audio, and in-memory `transcribe()` returning text or text hypotheses. Arbitrary speech servers/executables are not interchangeable with this adapter.

AMD/ROCm speech support and other platform/model combinations are not claimed by this guide. CE's broader GPU telemetry support does not establish speech support. CE does not install speech environments, drivers, packages, or models.

An administrator configures, selects, starts, stops, and views logs for the shared speech runtime. Signed-in users can dictate while it is running. There is **no Auto Start, Auto Stop, or automatic Send**. Speech stays loaded between recordings until an administrator clicks **Stop**, CE shuts down, or the process fails. Restarting CE does not start speech automatically.

## Hardware / Resource Expectations

The operator-validated reference host used Windows Server 2019 and a Tesla V100-SXM2-16GB. The Nemotron model occupied approximately **6 GB VRAM** when loaded; inference can need additional transient memory. A chat LLM and speech model can run together only when sufficient memory remains. CE does not automatically unload other models or move them between GPUs to make room.

First startup took approximately **1–2 minutes** in testing; short dictations transcribed very quickly, near-instantly once loaded. These are observations from that host, not universal latency or capacity guarantees. Allow disk space for the separate Python environment, package downloads, and the approximately 2.37 GB checkpoint.

## 1. Check GPU and Driver

Run on the computer hosting CE and the speech runtime:

```powershell
nvidia-smi
```

Confirm the intended NVIDIA GPU appears, the driver responds normally, and enough GPU memory is available. If the command fails or the GPU is absent, stop and resolve the driver/device prerequisite first.

| Tested reference item | Value |
| --- | --- |
| OS | Windows Server 2019 |
| GPU | NVIDIA Tesla V100-SXM2-16GB |
| Driver | NVIDIA Data Center Driver 539.64 / R535 |
| Driver-reported CUDA capability | 12.2 |
| PyTorch wheel runtime | CUDA 11.8 (`cu118`) |

**539.64 is a tested example for that OS/GPU, not a universal driver recommendation.** Choose a compatible NVIDIA Data Center/production driver for your GPU generation, Windows version, and supported driver branch. Do not blindly install the newest branch, particularly on V100/Windows Server 2019.

The CUDA version shown by `nvidia-smi` describes driver capability, not the installed PyTorch wheel or `nvcc` toolkit. The PyTorch CUDA wheel supplies its inference runtime; a separate host CUDA Toolkit is not normally required for this wheel-based inference setup. The validated host retained an older local `nvcc` toolkit while `cu118` worked. Do not replace a working toolkit merely to match those version numbers. See [NVIDIA CUDA compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html).

## 2. Create the Dedicated Speech Environment

Use **64-bit Python 3.11** for this tested configuration. The examples below are **PowerShell**, not CMD. Run the steps in the same PowerShell session so the path variables remain available; if you open a new session, redefine them. Choose a writable speech directory separate from CE's application environment. `C:\LLM-S2T` is only an example.

Install Python 3.11 and its Windows launcher if absent. The commit-pinned NeMo source installation also requires [Git for Windows](https://git-scm.com/install/windows) available on PATH to pip. Internet access is needed for installation and the model download, not for local dictation afterward.

```powershell
$SpeechRoot = 'C:\LLM-S2T'
$SpeechVenv = Join-Path $SpeechRoot 'venv'
$SpeechPython = Join-Path $SpeechVenv 'Scripts\python.exe'

py -3.11 --version
if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11 before continuing.' }
if (Test-Path -LiteralPath $SpeechVenv) { throw 'Environment already exists; verify it instead of overwriting it.' }
py -3.11 -m venv "$SpeechVenv"
if ($LASTEXITCODE -ne 0) { throw 'Speech environment creation failed.' }
& $SpeechPython --version
if ($LASTEXITCODE -ne 0) { throw 'Speech interpreter is unavailable.' }
```

Expected: `Python 3.11.x`. If `py` is unavailable, use the full path of your installed Python 3.11 executable for the two `py -3.11` commands. Do not use CE's environment or another application's environment as the speech venv.

No `activate.bat` or `Activate.ps1` is required. Both these commands and CE invoke the venv's Python executable directly, as supported by [Python's venv documentation](https://docs.python.org/3.11/library/venv.html#how-venvs-work).

## 3. Install the Tested S2T Stack

The intentional v1.4 top-level contract from the working environment is:

| Component | Tested version/source |
| --- | --- |
| Python | 3.11 |
| PyTorch | `torch==2.7.1+cu118` |
| TorchAudio | `torchaudio==2.7.1+cu118` |
| NumPy | `numpy==2.4.6` |
| NeMo ASR | `nemo-toolkit[asr]` at commit `912d96b5355fa4ad64ee7da8604cf6d6d2064c85` |

The underlying NeMo source requirement is `nemo-toolkit @ git+https://github.com/NVIDIA/NeMo.git@912d96b5355fa4ad64ee7da8604cf6d6d2064c85`; `[asr]` requests its ASR dependencies. Use this commit, not an unpinned development branch. The [pinned package metadata](https://github.com/NVIDIA/NeMo/blob/912d96b5355fa4ad64ee7da8604cf6d6d2064c85/pyproject.toml) defines the ASR extra. The CUDA wheel source follows [PyTorch's 2.7.1 installation instructions](https://pytorch.org/get-started/previous-versions/#v271).

This is a top-level contract, not a complete dependency lock. Pip resolves transitive dependencies; the constraints below prevent it from silently replacing the three tested numerical packages. Do not add `torchvision` for CE dictation; the adapter does not require it.

```powershell
& $SpeechPython -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { throw 'Packaging-tool installation failed.' }

& $SpeechPython -m pip install 'torch==2.7.1+cu118' 'torchaudio==2.7.1+cu118' --index-url https://download.pytorch.org/whl/cu118
if ($LASTEXITCODE -ne 0) { throw 'CUDA PyTorch installation failed.' }

$SpeechConstraints = Join-Path $SpeechRoot 'voice-v1.4-constraints.txt'
@'
torch==2.7.1+cu118
torchaudio==2.7.1+cu118
numpy==2.4.6
'@ | Set-Content -LiteralPath $SpeechConstraints -Encoding ASCII

& $SpeechPython -m pip install --constraint "$SpeechConstraints" 'numpy==2.4.6' Cython packaging
if ($LASTEXITCODE -ne 0) { throw 'NumPy/build prerequisite installation failed.' }

& $SpeechPython -m pip install --constraint "$SpeechConstraints" 'nemo-toolkit[asr] @ git+https://github.com/NVIDIA/NeMo.git@912d96b5355fa4ad64ee7da8604cf6d6d2064c85'
if ($LASTEXITCODE -ne 0) { throw 'Pinned NeMo ASR installation failed.' }

& $SpeechPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Resolve dependency conflicts before continuing.' }
```

Cython and packaging prepare for dependencies that need build-time support; they do not install a CUDA Toolkit. If pip reports no compatible wheel, a compiler requirement, or a dependency conflict, **stop at that error**. Resolve the named prerequisite for this Windows/Python combination; do not remove the pins, use `--no-deps`, install a different runtime framework, or modify CE's environment to force success. A fresh resolution is not guaranteed to reproduce every transitive version from the validated host.

## 4. Verify CUDA and NeMo

Run this command directly in the same PowerShell session. It imports packages but does not load a speech model:

```powershell
& $SpeechPython -c "import sys, torch, torchaudio, numpy; print('Python:', sys.version); print('Torch:', torch.__version__); print('TorchAudio:', torchaudio.__version__); print('NumPy:', numpy.__version__); print('Torch CUDA runtime:', torch.version.cuda); print('CUDA available:', torch.cuda.is_available()); print('GPU count:', torch.cuda.device_count()); print('GPU names:', [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]); assert torch.__version__ == '2.7.1+cu118' and torchaudio.__version__ == '2.7.1+cu118' and numpy.__version__ == '2.4.6', 'Stack differs from tested pins'; assert torch.cuda.is_available(), 'CUDA prerequisite failed'; from nemo.collections.asr.models import ASRModel; print('NeMo ASR import: OK')"
if ($LASTEXITCODE -ne 0) { throw 'Speech prerequisite verification failed; stop here.' }
```

Expected: Torch/TorchAudio `2.7.1+cu118`, NumPy `2.4.6`, Torch CUDA runtime `11.8`, `CUDA available: True`, at least one GPU with its NVIDIA name, and `NeMo ASR import: OK`. Dependency warnings may appear; a traceback, failed assertion, missing final message, or hanging import is not a pass. Do not continue to model setup until this succeeds.

## 5. Download the Recommended Model

Use the official [NVIDIA Nemotron 3.5 ASR Streaming 0.6B model card](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b) and its [`.nemo` file](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/blob/main/nemotron-3.5-asr-streaming-0.6b.nemo). Review the model's licence. Download **only** `nemotron-3.5-asr-streaming-0.6b.nemo`; do not download the whole repository or substitute its GGUF/Safetensors variants.

In CE, find **Admin Settings → Model Default Settings → Models DIR**. Use that configured directory, which need not be named `LLMs`. If the value is relative, resolve it against CE's application directory. Replace the example below with that absolute path. The CE process account needs read access to the checkpoint and speech interpreter.

```powershell
$ModelsDir = 'D:\Models' # Replace with the absolute directory from Models DIR.
if (-not (Test-Path -LiteralPath $ModelsDir -PathType Container)) { throw 'Set ModelsDir to the existing configured Models DIR.' }
$ModelPath = Join-Path $ModelsDir 'nemotron-3.5-asr-streaming-0.6b.nemo'
if (Test-Path -LiteralPath $ModelPath) { throw 'Checkpoint already exists; verify it instead of overwriting it.' }
Invoke-WebRequest -UseBasicParsing -Uri 'https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/resolve/main/nemotron-3.5-asr-streaming-0.6b.nemo?download=true' -OutFile $ModelPath -ErrorAction Stop
Get-Item -LiteralPath $ModelPath | Select-Object FullName, Length
```

Wait for the download to finish before rescanning. Expect a multi-gigabyte checkpoint, not a small HTML/error page or repository pointer. If download fails, do not use the partial file. No checksum is published here; optional manual restoration below or CE startup will check whether the checkpoint can load.

## 6. Optional Manual Model Validation

Skip this if you prefer CE's real startup validation in step 9. Stop any running speech instance first and ensure sufficient VRAM; this command loads another copy of the model. It can take a minute or more.

```powershell
& $SpeechPython -c "import os, sys; os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1'); from nemo.collections.asr.models import ASRModel; model = ASRModel.restore_from(restore_path=sys.argv[1], map_location='cuda'); print('Model restored:', type(model).__name__); rate = int(model.cfg.preprocessor.sample_rate); print('Sample rate:', rate); assert rate == 16000, 'Expected 16 kHz model'" "$ModelPath"
if ($LASTEXITCODE -ne 0) { throw 'Local model validation failed; stop before CE startup.' }
```

Expected: successful restoration and `Sample rate: 16000`. This process exits afterward and releases its GPU allocation. It does not test dictation or open a service port. Do not continue if it reports missing assets, an incompatible checkpoint, CUDA failure, or OOM.

## 7. Configure LLM Controller CE

As an administrator, open the **Speech-to-Text Runtime** card in **Admin Settings**, below **API Access**:

| Field | Value |
| --- | --- |
| Speech runtime path | `C:\LLM-S2T\venv\Scripts\python.exe` (use your actual path) |
| Speech service port | `8082` by default |

The runtime path is the dedicated environment's **Python executable**, not the model file, environment folder, activation script, or a command string. Click **Save Settings**. CE invokes this interpreter directly; you do not activate the speech venv before starting CE.

Use an available speech port distinct from CE's app port, **Llama Main Port**, and **Llama Title Port**. The speech service binds only to `127.0.0.1`; browsers communicate through CE. Do not publish the speech port or add a public firewall rule for it. CE supplies its internal authentication automatically.

Configuration does not start speech. After changing the runtime path or port for a running instance, explicitly **Stop**, then **Start** it again.

## 8. Add / Designate the Speech Model

1. In **Admin Settings**, expand **Model Registry** and click **Rescan Models**.
2. Find `nemotron-3.5-asr-streaming-0.6b.nemo`. Confirm it is present and enabled; use the row's **Enable** action if disabled. The green state indicator means **Enabled**; yellow means present but disabled, red means missing.
3. Check **Speech-to-Text** on that row. This checkbox saves immediately; wait for the registry operation to finish. Discovery alone does not designate speech capability.
4. **Save Metadata** is for edited metadata such as profiles/notes; it is not required to persist the Speech-to-Text checkbox or Enable action.

A model must be present, enabled, designated Speech-to-Text, and not designated MMPROJ to be selectable. Speech models are excluded from chat-language-model, title-model, and benchmark choices. Marking an incompatible file as Speech-to-Text does not make it a supported checkpoint.

## 9. Start Speech-to-Text

Open **Model → Speech-to-Text**, select the **Speech-to-Text Model**, and click **Start**. Wait for **Status: Running**, the **Device** name, and **Ready for dictation.** The selected model is saved, but selection, page reload, and mic clicks do not start it.

Allow approximately 1–2 minutes on hardware similar to the reference host. CE allows up to five minutes for initialization. If it fails or times out, stop here and use the troubleshooting section; do not repeatedly start duplicate manual copies.

Administrators can open **Logs → Speech-to-Text** to view the bounded S2T runtime log; it refreshes while that tab is active. The main CE console also shows `[S2T]` lifecycle messages and `[S2T runtime]` child output, including bootstrap/import progress and readiness. No user-managed speech token is needed.

The runtime remains loaded between dictations. Click **Stop** to release it; stop it before selecting a different speech model. There is one shared transcription request at a time. Continue using CE's normal single-worker deployment so one process owns the runtime.

## 10. Dictate

1. Open Chat and click the microphone button (**Voice Dictation**) beside the composer actions.
2. Allow microphone permission, then speak when recording begins.
3. Click the mic again to finish. Its recording action is **Finish dictation**; the status then says **Transcribing locally…**. Recording is limited to two minutes.
4. The recognized text is appended to existing composer text. Review/edit it, then click **Send** normally. Nothing is sent automatically.

**Cancel recording** or **Cancel dictation** discards the pending result. Changing conversations prevents insertion into the wrong conversation. CE sends completed recordings as in-memory mono 16 kHz PCM WAV; it does not create a persistent audio library. Text becomes ordinary chat content only when you choose to Send it.

If speech is stopped, the mic shows **Speech-to-text model is not running.** and **Open Speech Models**. Ask an administrator to start it, or continue typing normally.

## 11. Browser / Microphone Requirements

Use a browser with microphone, Web Audio, and AudioWorklet support. Microphone capture needs permission and a secure context: **HTTPS**, or **localhost on the browser's own computer**. Plain HTTP to a remote server's LAN IP/name generally does not qualify. If CE runs on another machine, use HTTPS for browser access; the microphone belongs to the browser machine, not the CE server. See [browser microphone requirements](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia#privacy_and_security).

Select the correct input in the browser/site microphone settings and Windows **Sound → Input** / **Recording** devices. Verify that the input-level meter moves when speaking. Check Windows microphone privacy access for desktop apps and browser site permissions. A browser capture indicator does not prove the recording contains audible speech.

If the meter stays still or Windows records silence, check mute switches, microphone hardware, the physical jack, and adapters before changing CE. A TRS/TRRS headset or splitter mismatch is one possible hardware cause, not a CE product requirement.

## Troubleshooting

### Speech model does not appear

Confirm the registry row is present, **Enabled**, and **Speech-to-Text** is checked. Stop the current runtime before changing selection. A saved selection that is now missing/disabled must be replaced with an available model.

### Start remains Starting / fails

Open **Logs → Speech-to-Text** and inspect the S2T runtime log alongside the main CE console's `[S2T]` / `[S2T runtime]` messages. Check the configured Python executable, model path, available VRAM, and CUDA/NeMo verification in step 4. Check whether the configured port (default 8082) is already occupied; stop only an instance you own or choose another free speech port. CE ends startup after 300 seconds and reports Error; a child terminated at that point is the timeout cleanup, not proof it failed to launch.

### CUDA unavailable

Run `nvidia-smi`, then repeat step 4 with the dedicated interpreter. If Torch reports a CPU build or `CUDA available: False`, stop and resolve the driver/wheel compatibility. Do not blindly update the driver, replace the host toolkit, or alter another application's Python environment.

### Out of GPU memory

Only diagnose OOM when **Logs → Speech-to-Text** or the main CE console reports it. The loaded model uses meaningful VRAM and transcription can allocate more. Manually stop/reduce other GPU workloads you control, or use a compatible smaller workload. CE does not automatically unload chat models, move GPUs, or silently unload speech. Retest a short dictation after making room; if the speech process exited, explicitly restart it.

### "No speech was recognized"

Check that the Windows microphone level moves and a local Windows recording is audible. Verify browser permission/input selection, mute settings, and the physical microphone/jack/adapter. Then retry a short, clear phrase. Silence is not evidence of a CUDA problem.

### Browser mic unavailable

Check HTTPS/localhost, browser API support, site permission, Windows privacy permission, and the selected device. Remote HTTP access can still support typed chat while microphone capture is unavailable.

### Runtime works manually but not through CE

Compare the interpreter, model, and port with CE's console output; it reports the actual CE interpreter and resolved speech launch paths. Ensure no manual speech instance occupies the port. Use the current v1.4 adapter and repeat steps 4 and 9. Do not add shell activation, change the working directory manually, or revive old stdin/gevent workarounds. If startup or inference still fails, retain the specific exception and last bootstrap stage from **Logs → Speech-to-Text** or the main CE console before changing dependencies.
