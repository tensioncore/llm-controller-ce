<table style="border: 0px;">
  <tr style="border: 0px;">
    <td style="border: 0px;">
      
<IMG SRC="static/LLM-Controller-LOGO.png" WIDTH="75px">

# LLM Controller CE

> **A local-first control platform for running, managing, chatting with, monitoring, and benchmarking GGUF language models on your own hardware.**

LLM Controller CE brings local AI operations and everyday workflows into one product: setup, model selection, conversation management, benchmarks, telemetry, analytics, and administration.

This is not just a launcher.  
It is not just a chat wrapper.  
It is not just a benchmark tool.

**LLM Controller CE** is the Community Edition foundation of the LLM Controller platform — built to make self-hosted AI feel like a real product.
    </td>
    <td>
      <img src="docs/images/hero-screenshot.png" alt="Hero Screenshot" width="420">
    </td>
  </tr>
</table>

---

## 🚀 Why LLM Controller CE?

Running local models often means juggling folders, terminals, runtime flags, scattered utilities, and disconnected interfaces.

LLM Controller CE changes that.

It combines the operational side and the everyday usage side of local AI into one unified web interface, including:

- ⚡ **Managed model discovery and launch control**
- 💬 **Persistent streaming chat sessions**
- 🧠 **Optional reasoning visibility when supported by the model**
- 📎 **Attachment-aware prompting for text and code files**
- 📊 **Built-in benchmarking and result history**
- 🖥 **Logs, GPU telemetry, runtime visibility, and analytics**
- 👤 **Authenticated access with administrator and user roles**
- 🔧 **Saved defaults, benchmark prompt editing, and user administration**
- 🏠 **A local-first experience that stays on your own hardware**

---

## ✨ Highlights

### 💬 Persistent Chat Workspace
Stream responses live, stop generation mid-stream, regenerate the latest answer, edit the latest prompt, and keep conversations organized through saved chat sessions with auto-generated titles.

![Chat Screenshot Placeholder](docs/images/chat-screenshot.png)

### 🧠 Reasoning-Aware Responses
When a model returns reasoning content, the interface can expose it with a dedicated show/hide workflow instead of burying it behind raw output.

![Thoughts Screenshot Placeholder](docs/images/thoughts-screenshot.png)

### 🎛 Managed Model Library
Scan a configured model directory for GGUF files, maintain a registry of available models, recognize complete split model sets, mark favorites, enable or disable entries, and control which models are allowed in benchmarks.

![Model Drawer Placeholder](docs/images/model-drawer.png)

### 📎 File-Aware Conversations
Attach supported text-based files directly to prompts. LLM Controller CE applies limits, processes attachment context, and preserves attachment context with saved chats.

### 📈 Benchmarking Built In
Run administrator-controlled benchmarks across eligible models, edit the benchmark prompt set, review best-run summaries, inspect detailed saved outputs, and clearly distinguish current results from stale ones after prompt changes.

![Benchmark Screenshot Placeholder](docs/images/benchmark.png)

### 🖥 Built-In Monitoring & Operations
Monitor runtime logs, poll GPU telemetry, inspect raw GPU output, and review per-model analytics without needing a separate dashboard.

![Observability Screenshot Placeholder](docs/images/logs.png)

### 📊 GPU Monitoring
The GPU monitor supports both NVIDIA and AMD telemetry paths in the current build, with structured visibility into usage, utilization, temperature, power, and available process data.

![GPU Monitor Placeholder](docs/images/gpu-monitor.png)

### ⚙️ Built-In First-Run Setup
A built-in two-step setup flow initializes the application, prepares the MySQL database, creates the first administrator account, and saves runtime defaults before normal app access is opened.

![Installer Screenshot Placeholder](docs/images/installer.png)

---

## 🔥 What Makes It Special

LLM Controller CE is designed to feel like a **real local AI control platform**, not a loose collection of scripts and utilities.

It brings together runtime control, conversations, reasoning-aware UI, observability, benchmarking, and system administration into one self-hosted experience that stays on your own hardware.

For people who care about local AI and controlling their own stack, this is the experience the software should deliver.

---

## 🛠 Requirements

LLM Controller CE expects a self-hosted environment with:

- Python 3
- MySQL
- A working `llama-server` runtime
- Local GGUF model files

---

## 📦 Installation

LLM Controller CE uses a first-run web installer.

Basic flow:

1. Install Python packages
2. Prepare MySQL
3. Place your `llama-server` runtime
4. Place your local models
5. Start the application
6. Complete the two-step setup flow
7. Restart the app cleanly
8. Log in and begin using the platform

For full install notes, see **`INSTALL.md`**.

---

## 🔐 Access Model

LLM Controller CE is built for authenticated use, including shared environments, not just a one-off single-user shell.

Current access behavior includes:

- Email and password login
- Remember-me support
- Role-aware interface behavior
- Administrator-only management controls
- Forced password change flow for accounts created with temporary credentials

---

## 🧾 License

LLM Controller CE is source-available under the **Tensioncore Community Edition License 1.0**.

You may use, modify, and deploy it for personal use, research, internal business use, hosted workflows, and revenue-generating service operations that rely on the software.

You may not sell, white-label, sublicense, or commercially redistribute the software itself without written permission from **Tensioncore Administration Services**.

See **`LICENSE`** for full terms.

---

## 🏷 Attribution

LLM Controller CE is developed by **Tensioncore Administration Services**.

---

## 🌌 The Bigger Picture

LLM Controller CE is the start of a broader platform vision:

**run local AI cleanly, monitor it properly, evaluate it honestly, and keep control of your own infrastructure.**

That’s what this project is about.

## Related Project

- [LLM Controller Archive Viewer](https://github.com/tensioncore/llm-controller-archive-viewer)
