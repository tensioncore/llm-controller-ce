(function () {
  const $ = id => document.getElementById(id);
  let runtime = { status: "stopped" };
  let phase = "idle";
  let capture = null;
  let operation = 0;
  let requestController = null;
  let controlBusy = false;
  let controlError = "";
  let recordingChat = null;
  let statusTimer = null;
  let logTimer = null;
  let logRequest = null;
  const supported = () => window.isSecureContext && navigator.mediaDevices?.getUserMedia && window.AudioContext && window.OfflineAudioContext && window.AudioWorkletNode;
  const chatId = () => typeof currentSessionId === "undefined" ? null : currentSessionId;

  function say(message, { showModels = false, transient = false } = {}) {
    clearTimeout(statusTimer);
    $("dictationStatus").textContent = message;
    $("dictationStatusStrip").hidden = !message;
    $("openSpeechModels").hidden = !showModels;
    $("dismissDictationStatus").hidden = phase !== "idle" || !message;
    if (transient) statusTimer = setTimeout(() => { if (phase === "idle") say(""); }, 8000);
  }

  function render() {
    const starting = runtime.status === "starting";
    $("speechStatus").textContent = runtime.status === "running" ? "✅ Running" : starting ? "Starting..." : runtime.status === "error" ? "❌ Error" : "⏸ Stopped";
    $("speechStatus").classList.toggle("is-loading", starting);
    $("speechStatus").dataset.state = runtime.status;
    $("speechRunningModel").textContent = runtime.model || "None";
    $("speechDevice").textContent = runtime.device || "—";
    $("speechMessage").textContent = controlError || runtime.message || "";
    const active = ["starting", "running"].includes(runtime.status);
    if ($("speechStart")) {
      $("speechStart").disabled = controlBusy || active || !$("speechModelSelect").value;
      $("speechStart").classList.toggle("is-loading", starting);
      $("speechStart").setAttribute("aria-busy", String(starting));
      $("speechStart").textContent = starting ? "Starting..." : "🚀 Start";
    }
    if ($("speechStop")) $("speechStop").disabled = controlBusy || runtime.status === "stopped";
    if ($("speechModelSelect")) $("speechModelSelect").disabled = controlBusy || active;
    const mic = $("dictationButton");
    const busy = ["preparing", "transcribing"].includes(phase);
    const label = phase === "recording" ? "Finish dictation" : phase === "transcribing" ? "Transcribing dictation" : phase === "preparing" ? "Requesting microphone" : "Voice Dictation";
    $("dictationIcon").textContent = phase === "recording" ? "■" : busy ? "…" : "🎙️";
    mic.title = label;
    mic.setAttribute("aria-label", label);
    mic.classList.toggle("recording", phase === "recording");
    mic.setAttribute("aria-pressed", String(phase === "recording"));
    mic.setAttribute("aria-busy", String(busy));
    mic.disabled = busy;
    $("cancelDictation").hidden = phase === "idle";
    $("cancelDictation").textContent = phase === "transcribing" ? "Cancel dictation" : "Cancel recording";
    $("dismissDictationStatus").hidden = phase !== "idle" || !$("dictationStatus").textContent;
  }

  async function refreshModels() {
    const select = $("speechModelSelect");
    if (!select) return;
    const data = await window.ApiHttp.requestJSON("/speech/models", { cache: "no-store" });
    select.replaceChildren(new Option("Select a speech model", ""));
    data.models.forEach(model => select.add(new Option(model.name, model.value)));
    if (data.selected_model && !data.models.some(model => model.value === data.selected_model)) {
      const missing = new Option("Selected model is unavailable — choose another", data.selected_model);
      missing.disabled = true;
      select.add(missing);
    }
    select.value = data.selected_model || "";
    render();
  }

  async function refreshStatus() {
    runtime = await window.ApiHttp.requestJSON("/speech/status", { cache: "no-store" });
    if (phase === "idle" && runtime.status === "running" && !$("openSpeechModels").hidden) say("");
    if (phase === "recording" && runtime.status !== "running") {
      cancel("Speech runtime stopped. Recording cancelled.");
    }
    render();
    return runtime;
  }

  function logVisible() {
    const panel = $("speechLogsPanel");
    return !document.hidden && panel && !panel.hidden && $("logsDrawer").classList.contains("open");
  }

  async function refreshLogs() {
    clearTimeout(logTimer);
    if (!logVisible() || logRequest) return;
    const controller = new AbortController();
    logRequest = controller;
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const data = await window.ApiHttp.requestJSON("/speech/logs", { cache: "no-store", signal: controller.signal });
      if (logVisible()) window.SystemDrawer.renderLogLines($("speechLogs"), data.lines);
    } catch (error) {
      if (logVisible() && error.name !== "AbortError") $("speechLogs").textContent = error.message;
    } finally {
      clearTimeout(timeout);
      logRequest = null;
      if (logVisible()) logTimer = setTimeout(refreshLogs, controller.signal.aborted ? 0 : runtime.status === "starting" ? 1500 : 5000);
    }
  }

  function syncLogPolling() {
    clearTimeout(logTimer);
    if (logVisible()) refreshLogs();
    else logRequest?.abort();
  }

  function chooseTab(speech, focusTab = false, group = "Model") {
    $(`language${group}Panel`).hidden = speech;
    $(`speech${group}Panel`).hidden = !speech;
    [$(`language${group}Tab`), $(`speech${group}Tab`)].forEach((tab, index) => {
      const selected = (index === 1) === speech;
      tab.classList.toggle("active", selected);
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      if (selected && focusTab) tab.focus();
    });
    if (group === "Model" && speech) refreshModels().catch(error => { controlError = error.message; render(); });
    if (group === "Logs") {
      [$("btnLogStart"), $("btnLogStop"), $("btnLogClear")].forEach(button => {
        if (button) button.hidden = speech;
      });
      syncLogPolling();
    }
  }

  function openSpeech() {
    if (!$("modelDrawer").classList.contains("open")) window.toggleDrawer("modelDrawer");
    chooseTab(true, true);
  }

  async function control(action) {
    controlBusy = true;
    controlError = "";
    render();
    try {
      if (action === "stop" && phase !== "idle") cancel("Dictation cancelled.");
      await window.ApiHttp.postJSONRequest(`/speech/${action}`, {});
      await refreshStatus();
    } catch (error) {
      controlError = error.message;
    } finally {
      controlBusy = false;
      render();
    }
  }

  function releaseCapture(recording) {
    if (!recording) return;
    clearTimeout(recording.timer);
    recording.stream?.getTracks().forEach(track => track.stop());
    recording.source?.disconnect();
    recording.node?.disconnect();
    if (recording.context && recording.context.state !== "closed") recording.context.close().catch(() => {});
  }

  function cancel(message) {
    operation++;
    requestController?.abort();
    requestController = null;
    releaseCapture(capture);
    if (capture) capture.chunks = [];
    capture = null;
    phase = "idle";
    say(message, { showModels: runtime.status !== "running" });
    render();
  }

  async function startRecording() {
    if (phase !== "idle") return;
    if (!supported()) {
      say("Microphone requires HTTPS or localhost and a supported browser.");
      return;
    }
    if (runtime.status !== "running") {
      say("Speech-to-text model is not running.", { showModels: true });
      return;
    }
    phase = "preparing";
    const id = ++operation;
    recordingChat = chatId();
    const recording = { chunks: [], frames: 0 };
    capture = recording;
    render();
    say("Requesting microphone access…");
    try {
      recording.context = new AudioContext();
      await recording.context.resume();
      if (id !== operation) { releaseCapture(recording); return; }
      await refreshStatus();
      if (id !== operation) { releaseCapture(recording); return; }
      if (runtime.status !== "running") throw new Error("Speech-to-text model is not running.");
      recording.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true }, video: false });
      if (id !== operation) { releaseCapture(recording); return; }
      await recording.context.audioWorklet.addModule("/static/speech_capture.js");
      if (id !== operation) { releaseCapture(recording); return; }
      recording.node = new AudioWorkletNode(recording.context, "ce-speech-capture", { channelCount: 1, channelCountMode: "explicit" });
      recording.node.port.onmessage = ({ data }) => {
        if (data === "finished") { recording.onFinished?.(); return; }
        if (id !== operation) return;
        const remaining = Math.max(0, recording.context.sampleRate * 120 - recording.frames);
        const chunk = data.subarray(0, remaining);
        if (chunk.length) { recording.chunks.push(chunk); recording.frames += chunk.length; }
      };
      recording.node.onprocessorerror = () => {
        if (id === operation) cancel("Microphone capture failed. Try recording again.");
      };
      recording.source = recording.context.createMediaStreamSource(recording.stream);
      recording.source.connect(recording.node);
      recording.node.connect(recording.context.destination);
      recording.stream.getAudioTracks().forEach(track => track.addEventListener("ended", () => {
        if (phase === "recording") cancel("Microphone disconnected. Recording cancelled.");
      }));
      phase = "recording";
      say("Recording — click the mic again to finish (maximum 2 minutes).");
      recording.timer = setTimeout(finishRecording, 120000);
      render();
    } catch (error) {
      releaseCapture(recording);
      if (id !== operation) return;
      cancel(error.name === "NotAllowedError" ? "Microphone permission was denied. Allow microphone access in your browser." : error.name === "NotFoundError" ? "No microphone is available." : (error.message || "Microphone could not start."));
    }
  }

  async function wavBlob(recording) {
    if (!recording.frames) throw new Error("No audio was captured. Try again.");
    const frames = Math.min(16000 * 120, Math.floor(recording.frames * 16000 / recording.context.sampleRate));
    const offline = new OfflineAudioContext(1, frames, 16000);
    const buffer = offline.createBuffer(1, recording.frames, recording.context.sampleRate);
    let offset = 0;
    for (const chunk of recording.chunks) { buffer.getChannelData(0).set(chunk, offset); offset += chunk.length; }
    recording.chunks = [];
    const source = offline.createBufferSource();
    source.buffer = buffer;
    source.connect(offline.destination);
    source.start();
    const samples = (await offline.startRendering()).getChannelData(0);
    const bytes = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(bytes);
    const label = (at, text) => [...text].forEach((char, i) => view.setUint8(at + i, char.charCodeAt(0)));
    label(0, "RIFF"); view.setUint32(4, bytes.byteLength - 8, true); label(8, "WAVE"); label(12, "fmt ");
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, 16000, true); view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    label(36, "data"); view.setUint32(40, samples.length * 2, true);
    samples.forEach((sample, i) => { const value = Math.max(-1, Math.min(1, sample)); view.setInt16(44 + i * 2, value * (value < 0 ? 32768 : 32767), true); });
    return new Blob([bytes], { type: "audio/wav" });
  }

  async function finishRecording() {
    if (phase !== "recording" || !capture) return;
    phase = "transcribing";
    const id = operation;
    const recording = capture;
    clearTimeout(recording.timer);
    render();
    say("Transcribing locally…");
    try {
      recording.source.disconnect();
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error("Audio capture did not finish. Please record again.")), 2000);
        recording.onFinished = () => { clearTimeout(timeout); resolve(); };
        recording.node.port.postMessage("finish");
      });
      releaseCapture(recording);
      if (id !== operation) return;
      const audio = await wavBlob(recording);
      if (id !== operation) return;
      capture = null;
      requestController = new AbortController();
      const data = await window.ApiHttp.requestJSON("/speech/transcribe", {
        method: "POST", headers: { "Content-Type": "audio/wav", "X-CSRFToken": window.CSRF_TOKEN },
        body: audio, signal: requestController.signal
      }, "Speech could not be transcribed.");
      if (id !== operation) return;
      if (recordingChat !== chatId()) throw new Error("Conversation changed. Dictation was not inserted; record again in this conversation.");
      const text = String(data.text || "").trim();
      if (text) {
        const input = $("chatInput");
        input.value += `${input.value && !/\s$/.test(input.value) ? " " : ""}${text}`;
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.focus();
        if (typeof window.autoResize === "function") window.autoResize(input);
        say("Dictation added. Review and edit it, then Send when ready.", { transient: true });
      } else say("No speech was recognized. Try again.");
    } catch (error) {
      if (id === operation) say(error.message || "Dictation failed. Try again.");
    } finally {
      releaseCapture(recording);
      recording.chunks = [];
      if (id === operation) { capture = null; requestController = null; phase = "idle"; render(); }
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!$("dictationButton")) return;
    ["Model", "Logs"].forEach(group => {
      const tabs = [$(`language${group}Tab`), $(`speech${group}Tab`)];
      if (!tabs.every(Boolean)) return;
      tabs.forEach((tab, index) => {
        tab.addEventListener("click", () => chooseTab(index === 1, false, group));
        tab.addEventListener("keydown", event => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === "Home" ? 0 : event.key === "End" ? 1 : 1 - index;
          chooseTab(next === 1, true, group);
        });
      });
    });
    $("openSpeechModels").addEventListener("click", openSpeech);
    $("dictationButton").addEventListener("click", () => phase === "recording" ? finishRecording() : startRecording());
    $("cancelDictation").addEventListener("click", () => cancel("Dictation cancelled."));
    $("dismissDictationStatus").addEventListener("click", () => { say(""); $("dictationButton").focus(); });
    $("speechStart")?.addEventListener("click", () => control("start"));
    $("speechStop")?.addEventListener("click", () => control("stop"));
    $("speechModelSelect")?.addEventListener("change", async () => {
      controlBusy = true; controlError = ""; render();
      try { await window.ApiHttp.postJSONRequest("/speech/select", { model_path: $("speechModelSelect").value }); }
      catch (error) {
        controlError = error.message;
        try { await refreshModels(); } catch (_) { /* Keep the selection error visible. */ }
      }
      finally { controlBusy = false; render(); }
    });
    if ($("speechLogsPanel")) {
      document.addEventListener("visibilitychange", syncLogPolling);
      const visibilityObserver = new MutationObserver(syncLogPolling);
      visibilityObserver.observe($("logsDrawer"), { attributes: true, attributeFilter: ["class"] });
    }
    async function poll() {
      try {
        if (!document.hidden || phase !== "idle") {
          if (phase === "recording" && recordingChat !== chatId()) cancel("Conversation changed. Recording cancelled.");
          await refreshStatus();
        }
      } catch (error) {
        runtime = { status: "error", message: error.message };
        render();
        if (phase !== "idle") cancel("Speech status is unavailable. Dictation cancelled.");
      } finally { setTimeout(poll, 5000); }
    }
    render();
    poll();
  });
  window.addEventListener("pagehide", () => { if (capture || requestController) cancel("Dictation cancelled."); });
})();
