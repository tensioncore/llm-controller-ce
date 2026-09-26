(function () {
  if (window.__systemDrawerInitialized) {
    return;
  }
  window.__systemDrawerInitialized = true;

  let logStreamIntervalId = null;
  let logPollInFlight = false;
  let logAutoRefreshEnabled = false;
  let logDrawerObserver = null;
  let smiIntervalId = null;
  let smiPollInFlight = false;
  let smiAutoRefreshEnabled = false;
  let smiFetchController = null;
  let smiDrawerObserver = null;
  let _registryCache = [];

  const MODEL_PREFIX = window.MODEL_PREFIX || "/model";
  const REGISTRY_PROFILE_FIELDS = [
    ["profile_general", "General"],
    ["profile_coding", "Coding"],
    ["profile_writing", "Writing"],
    ["profile_reasoning", "Reasoning"],
    ["profile_math", "Math"],
    ["profile_agents", "Agents"],
    ["profile_images", "Images"]
  ];

  const _escape = (typeof window.escapeHtml === "function")
    ? window.escapeHtml
    : (text) => String(text ?? "").replace(/[&<>"']/g, (m) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    })[m]);

  function $(id) { return document.getElementById(id); }

  function setText(el, text) {
    if (!el) return;
    el.textContent = (text === undefined || text === null) ? "" : String(text);
  }

  function _toNumberOrNull(value) {
    if (value === null || value === undefined || value === "") return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function setHtml(el, html) {
    if (!el) return;
    el.innerHTML = html;
  }

  function scrollToBottom(el) {
    if (!el) return;
    setTimeout(() => { el.scrollTop = el.scrollHeight; }, 0);
  }

  function formatLogLine(line) {
    const s = String(line ?? "");
    const upper = s.toUpperCase();

    const isBad =
      upper.includes("ERROR") ||
      upper.includes("EXCEPTION") ||
      upper.includes("TRACEBACK") ||
      upper.includes("FAILED") ||
      upper.includes("CRITICAL");

    const color = isBad ? "red" : "#0f0";
    return `<span style="color:${color};">${_escape(s)}</span>`;
  }

  function setAdminStatus(msg, ok) {
    const el = $("adminRegistryStatus");
    if (!el) return;
    el.textContent = msg || "";
    el.style.color = ok === true ? "#7CFC90" : (ok === false ? "#ff6b6b" : "");
  }

  function renderLogLines(element, lines) {
    setHtml(element, lines && lines.length ? lines.map(formatLogLine).join("<br>") : "No logs yet.");
    scrollToBottom(element);
  }

  function setRegistryStatus(msg, ok) {
    const el = $("adminRegistryStatus");
    if (!el) return;
    el.textContent = msg || "";
    el.style.color = ok === true ? "#7CFC90" : (ok === false ? "#ff6b6b" : "");
  }

  function as01(v) {
    if (v === true || v === 1) return 1;
    const normalized = String(v ?? "").trim().toLowerCase();
    return (normalized === "1" || normalized === "true" || normalized === "yes") ? 1 : 0;
  }

  async function safeJson(res) {
    try { return await res.json(); } catch { return null; }
  }

  async function fetchLogs() {
    const logContent = $("logContent");
    if (!logContent) return;

    try {
      const res = await fetch(`${MODEL_PREFIX}/logs`, { cache: "no-store", credentials: "same-origin" });
      if (!res.ok) throw new Error(`Logs endpoint HTTP ${res.status}`);

      const ct = (res.headers.get("content-type") || "").toLowerCase();
      let lines = [];

      if (ct.includes("application/json")) {
        const data = await res.json();
        lines = Array.isArray(data.logs) ? data.logs : [];
      } else {
        const text = await res.text();
        lines = text.split("\n");
      }

      if (!isLogsDrawerOpen()) return;

      renderLogLines(logContent, lines);
    } catch (err) {
      setHtml(
        logContent,
        `<span style="color:#ff6b6b;">❌ Log stream error: ${_escape(err.message || String(err))}</span>`
      );
      console.error("fetchLogs failed:", err);
    }
  }

  function isLogsDrawerOpen() {
    const drawer = $("logsDrawer");
    return !!(drawer && drawer.classList.contains("open"));
  }

  function clearLogTimer() {
    if (!logStreamIntervalId) return;
    clearTimeout(logStreamIntervalId);
    logStreamIntervalId = null;
  }

  function scheduleNextLogPoll(delayMs = 1500) {
    clearLogTimer();
    if (!logAutoRefreshEnabled || !isLogsDrawerOpen()) return;
    logStreamIntervalId = setTimeout(() => {
      logStreamIntervalId = null;
      runLogPoll();
    }, delayMs);
  }

  function syncLogStreamToDrawerState() {
    if (!isLogsDrawerOpen()) {
      stopLogStream();
      return;
    }

    if (!logAutoRefreshEnabled) {
      const logContent = $("logContent");
      if (logContent) setText(logContent, "Starting log stream...");
      logAutoRefreshEnabled = true;
      clearLogTimer();
    }

    if (!logPollInFlight && !logStreamIntervalId) {
      runLogPoll();
    }
  }

  function ensureLogsDrawerObserver() {
    if (logDrawerObserver || typeof MutationObserver === "undefined") return;
    const drawer = $("logsDrawer");
    if (!drawer) return;

    logDrawerObserver = new MutationObserver(() => {
      syncLogStreamToDrawerState();
    });
    logDrawerObserver.observe(drawer, {
      attributes: true,
      attributeFilter: ["class"]
    });
    syncLogStreamToDrawerState();
  }

  async function runLogPoll() {
    if (logPollInFlight || !logAutoRefreshEnabled || !isLogsDrawerOpen()) return;
    logPollInFlight = true;
    try {
      await fetchLogs();
    } finally {
      logPollInFlight = false;
      if (logAutoRefreshEnabled && isLogsDrawerOpen()) {
        scheduleNextLogPoll(1500);
      }
    }
  }

  function startLogStream() {
    const logContent = $("logContent");
    if (logContent && !logAutoRefreshEnabled) {
      setText(logContent, "Starting log stream…");
    }

    logAutoRefreshEnabled = true;
    clearLogTimer();
    syncLogStreamToDrawerState();
  }

  function stopLogStream() {
    logAutoRefreshEnabled = false;
    clearLogTimer();
  }

  async function clearLogs() {
    stopLogStream();

    const logContent = $("logContent");
    if (logContent) setText(logContent, "Logs cleared.");

    try {
      await fetch(`${MODEL_PREFIX}/clear_logs`, {
        method: "POST",
        headers: { "X-CSRFToken": window.CSRF_TOKEN },
        cache: "no-store",
        credentials: "same-origin"
      });
    } catch (err) {
      console.error("clear_logs failed:", err);
      if (logContent) setText(logContent, `❌ Clear logs failed: ${err.message || String(err)}`);
    }
  }

  const GPU_HISTORY_MAX_POINTS = 12; // 12 points @ 5s polling ~= 1 minute
  const gpuHistory = {}; // { [gpuIndex]: { util: number[], mem: number[] } }

  function _gpuClamp(num, min, max) {
    const n = Number(num);
    if (!Number.isFinite(n)) return min;
    return Math.max(min, Math.min(max, n));
  }

  function _gpuPercentClass(pct) {
    const p = Number(pct || 0);
    if (p >= 90) return "max";
    if (p >= 75) return "high";
    if (p >= 50) return "mid";
    return "low";
  }

  function _gpuUtilClass(pct) {
    const p = Number(pct || 0);
    if (p >= 85) return "max";
    if (p >= 60) return "high";
    if (p >= 20) return "mid";
    return "low";
  }

  function _pushGpuHistory(gpuIndex, utilPct, memPct) {
    const key = String(gpuIndex);
    if (!gpuHistory[key]) gpuHistory[key] = { util: [], mem: [] };

    if (utilPct !== null && utilPct !== undefined) {
      gpuHistory[key].util.push(_gpuClamp(utilPct, 0, 100));
    }
    if (memPct !== null && memPct !== undefined) {
      gpuHistory[key].mem.push(_gpuClamp(memPct, 0, 100));
    }

    if (gpuHistory[key].util.length > GPU_HISTORY_MAX_POINTS) gpuHistory[key].util.shift();
    if (gpuHistory[key].mem.length > GPU_HISTORY_MAX_POINTS) gpuHistory[key].mem.shift();
  }

  function _buildMiniSparkSvg(values, type) {
    const width = 220;
    const height = 38;
    const pad = 2;
    const arr = Array.isArray(values) ? values.slice(-GPU_HISTORY_MAX_POINTS) : [];
    const safe = arr
      .map((v) => _toNumberOrNull(v))
      .filter((v) => v !== null);

    if (!safe.length) {
      return `<div class="gpu-metric-text">\u2014</div>`;
    }

    const step = safe.length > 1 ? (width - pad * 2) / (safe.length - 1) : 0;

    let d = "";
    safe.forEach((v, i) => {
      const x = pad + (i * step);
      const y = pad + ((100 - _gpuClamp(v, 0, 100)) / 100) * (height - pad * 2);
      d += `${i === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)} `;
    });

    return `
      <svg class="gpu-mini-chart-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true">
        <rect class="gpu-mini-chart-bg" x="0" y="0" width="${width}" height="${height}" rx="8"></rect>
        <line class="gpu-mini-chart-grid" x1="0" y1="${(height / 2).toFixed(2)}" x2="${width}" y2="${(height / 2).toFixed(2)}"></line>
        <path class="gpu-mini-chart-line ${_escape(type)}" d="${d.trim()}"></path>
      </svg>
    `;
  }

  function isSMIDrawerOpen() {
    const drawer = $("smiDrawer");
    return !!(drawer && drawer.classList.contains("open"));
  }

  function setSmiTitle(text) {
    const title = $("smiDrawerTitle");
    if (title) title.textContent = text;
  }

  function setSmiRawSummary(text) {
    const summary = $("smiRawSummary");
    if (summary) summary.textContent = text;
  }

  function clearSmiTimer() {
    if (!smiIntervalId) return;
    clearTimeout(smiIntervalId);
    smiIntervalId = null;
  }

  function cancelSmiFetch() {
    if (smiFetchController && typeof smiFetchController.abort === "function") {
      try {
        smiFetchController.abort();
      } catch (err) {
        console.warn("Unable to abort GPU telemetry request:", err);
      }
    }
    smiFetchController = null;
  }

  function scheduleNextSMIPoll(delayMs = 5000) {
    clearSmiTimer();
    if (!smiAutoRefreshEnabled || !isSMIDrawerOpen()) return;
    smiIntervalId = setTimeout(() => {
      smiIntervalId = null;
      runSMIPoll();
    }, delayMs);
  }

  function syncSMIAutoRefreshToDrawerState() {
    const section = document.querySelector(".gpu-stats-section");
    const hasData = !!(section && section.style.display !== "none");

    if (!isSMIDrawerOpen()) {
      stopSMIAutoRefresh();
      return;
    }

    if (!smiAutoRefreshEnabled) {
      smiAutoRefreshEnabled = true;
      clearSmiTimer();
    }

    if (smiAutoRefreshEnabled) {
      setSmiTitle("Auto-refresh 5s");
      if (!smiPollInFlight && !smiIntervalId) {
        runSMIPoll();
      }
      return;
    }

    setSmiTitle(hasData ? "Manual" : "Monitor Idle");
  }

  function ensureSMIDrawerObserver() {
    if (smiDrawerObserver || typeof MutationObserver === "undefined") return;
    const drawer = $("smiDrawer");
    if (!drawer) return;

    smiDrawerObserver = new MutationObserver(() => {
      syncSMIAutoRefreshToDrawerState();
    });
    smiDrawerObserver.observe(drawer, {
      attributes: true,
      attributeFilter: ["class"]
    });
    syncSMIAutoRefreshToDrawerState();
  }

  function getGpuCardRefs(card) {
    if (card && card._gpuRefs) return card._gpuRefs;
    if (!card) return null;

    card._gpuRefs = {
      title: card.querySelector(".gpu-card-title"),
      subtitle: card.querySelector(".gpu-card-subtitle"),
      memoryText: card.querySelector(".gpu-memory-text"),
      memoryPill: card.querySelector(".gpu-memory-pill"),
      memoryBar: card.querySelector(".gpu-memory-bar"),
      utilText: card.querySelector(".gpu-util-text"),
      utilBar: card.querySelector(".gpu-util-bar"),
      utilMiniValue: card.querySelector(".gpu-util-mini-value"),
      utilMiniChart: card.querySelector(".gpu-util-mini-chart"),
      memMiniValue: card.querySelector(".gpu-mem-mini-value"),
      memMiniChart: card.querySelector(".gpu-mem-mini-chart"),
      powerPill: card.querySelector(".gpu-power-pill"),
      tempPill: card.querySelector(".gpu-temp-pill")
    };
    return card._gpuRefs;
  }

  function ensureGpuCard(host, gpuIndex) {
    const key = String(gpuIndex);
    let card = host.querySelector(`.gpu-card[data-gpu-index="${key}"]`);
    if (card) return card;

    card = document.createElement("div");
    card.className = "gpu-card";
    card.dataset.gpuIndex = key;
    card.innerHTML = `
      <div class="gpu-card-top">
        <div class="gpu-card-title"></div>
        <div class="gpu-card-subtitle"></div>
      </div>

      <div class="gpu-metric-row">
        <div class="gpu-metric-label">Memory</div>
        <div class="gpu-metric-value">
          <span class="gpu-metric-text gpu-memory-text"></span>
          <span class="gpu-stat-pill vram gpu-memory-pill"></span>
        </div>
      </div>
      <div class="gpu-bar">
        <div class="gpu-bar-fill gpu-bar-memory gpu-memory-bar"></div>
      </div>

      <div class="gpu-metric-row" style="margin-top:12px;">
        <div class="gpu-metric-label">Utilization</div>
        <div class="gpu-metric-value">
          <span class="gpu-metric-text gpu-util-text"></span>
        </div>
      </div>
      <div class="gpu-bar">
        <div class="gpu-bar-fill gpu-bar-util gpu-util-bar"></div>
      </div>

      <div class="gpu-history-row">
        <div class="gpu-mini-chart-card">
          <div class="gpu-mini-chart-top">
            <span class="gpu-mini-chart-label">GPU 1 min</span>
            <span class="gpu-mini-chart-value gpu-util-mini-value"></span>
          </div>
          <div class="gpu-util-mini-chart"></div>
        </div>

        <div class="gpu-mini-chart-card">
          <div class="gpu-mini-chart-top">
            <span class="gpu-mini-chart-label">MEM 1 min</span>
            <span class="gpu-mini-chart-value gpu-mem-mini-value"></span>
          </div>
          <div class="gpu-mem-mini-chart"></div>
        </div>
      </div>

      <div class="gpu-card-footer">
        <span class="gpu-stat-pill power gpu-power-pill"></span>
        <span class="gpu-stat-pill temp gpu-temp-pill"></span>
      </div>
    `;
    getGpuCardRefs(card);
    host.appendChild(card);
    return card;
  }

  function ensureProcessTable(procCont) {
    let table = procCont.querySelector(".gpu-proc-table");
    if (!table) {
      procCont.textContent = "";
      table = document.createElement("table");
      table.className = "tbl gpu-proc-table";
      table.innerHTML = `
        <thead>
          <tr>
            <th>PID</th>
            <th>Executable</th>
            <th>GPU Usage</th>
            <th>VRAM Total</th>
          </tr>
        </thead>
        <tbody></tbody>
      `;
      procCont.appendChild(table);
    }

    return table.querySelector("tbody");
  }

  function renderProcessUsageCell(cell, gpuList) {
    cell.replaceChildren();

    if (!Array.isArray(gpuList) || gpuList.length === 0) {
      const emptyLine = document.createElement("div");
      emptyLine.className = "gpu-proc-subline";
      emptyLine.textContent = "—";
      cell.appendChild(emptyLine);
      return;
    }

    gpuList.forEach((g) => {
      const rawIndex = g?.gpu_index;
      const gpuIndex = (rawIndex === null || rawIndex === undefined) ? null : Number(rawIndex);
      const gpuName = String(g?.gpu_name || "Unknown GPU");
      const gpuLabel = gpuIndex === null ? "Unknown GPU" : `GPU${gpuIndex}`;
      const mem = Number(g?.used_memory_MB || 0);

      const line = document.createElement("div");
      line.className = "gpu-proc-subline";
      line.title = gpuName;

      const gpuLabelEl = document.createElement("span");
      gpuLabelEl.className = "gpu-proc-gpu";
      gpuLabelEl.textContent = gpuLabel;

      const gpuMemEl = document.createElement("span");
      gpuMemEl.className = "gpu-proc-mem";
      gpuMemEl.textContent = `${mem.toFixed(0)} MB`;

      line.appendChild(gpuLabelEl);
      line.appendChild(gpuMemEl);
      cell.appendChild(line);
    });
  }

  async function runSMIPoll() {
    if (smiPollInFlight || !isSMIDrawerOpen()) return;
    smiPollInFlight = true;
    try {
      await fetchSMI();
    } finally {
      smiPollInFlight = false;
      if (smiAutoRefreshEnabled && isSMIDrawerOpen()) {
        scheduleNextSMIPoll(5000);
      }
    }
  }

  async function fetchSMI() {
    const smiRaw = $("smiContent");
    const smiTimestamp = $("smiTimestamp");
    if (!smiRaw) return;

    const controller = (typeof AbortController !== "undefined")
      ? new AbortController()
      : null;
    smiFetchController = controller;

    try {
      const res = await fetch(`${MODEL_PREFIX}/nvidia_smi`, {
        cache: "no-store",
        credentials: "same-origin",
        signal: controller ? controller.signal : undefined
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const data = await res.json();
      if (!isSMIDrawerOpen()) return;

      setText(smiRaw, data.raw_text || "");
      setSmiRawSummary(`Show ${data.raw_command || "GPU telemetry"} output`);

      if (smiTimestamp && data.timestamp_iso8601) {
        const d = new Date(data.timestamp_iso8601);
        smiTimestamp.textContent = isNaN(d.getTime()) ? data.timestamp_iso8601 : d.toLocaleString();
      }

      renderGPUCards(data);
      renderProcessTable(data);

      const section = document.querySelector(".gpu-stats-section");
      if (section) section.style.display = "";
    } catch (err) {
      if (err && err.name === "AbortError") return;
      setText(smiRaw, `❌ GPU telemetry error: ${err.message || String(err)}`);
      console.error("fetchSMI failed:", err);
    } finally {
      if (smiFetchController === controller) {
        smiFetchController = null;
      }
    }
  }

  function renderGPUCards(data) {
    const host = $("gpuCards");
    const powerEl = $("powerUsage");
    if (!host) return;
    renderSystemRam(data);

    const gpus = Array.isArray(data?.gpus) ? data.gpus : [];
    {
      if (gpus.length === 0) {
        host.textContent = "";
        const empty = document.createElement("div");
        empty.className = "gpu-empty";
        empty.textContent = "No GPUs detected.";
        host.appendChild(empty);
        if (powerEl) powerEl.textContent = "\u2014";
        return;
      }

      const emptyState = host.querySelector(".gpu-empty");
      if (emptyState) emptyState.remove();

      const totalPowerValues = gpus
        .map((g) => _toNumberOrNull(g?.power_draw_watts ?? g?.power_draw_W))
        .filter((value) => value !== null);
      if (powerEl) {
        powerEl.textContent = totalPowerValues.length
          ? `${totalPowerValues.reduce((sum, value) => sum + value, 0).toFixed(1)}W`
          : "\u2014";
      }

      const seenKeys = new Set();

      gpus.forEach((g) => {
        const gpuIndex = Number(g?.index ?? 0);
        const gpuKey = String(gpuIndex);
        const gpuFullName = String(g?.name || `GPU ${gpuIndex}`);
        const shortDisplay = gpuFullName.replace(/-\w+-/g, " ");

        let totalGB = _toNumberOrNull(g?.memory_total_gb);
        if (totalGB === null) {
          const totalMB = _toNumberOrNull(g?.memory_total_MB);
          if (totalMB !== null) totalGB = totalMB / 1024;
        }

        let usedGB = _toNumberOrNull(g?.memory_used_gb);
        if (usedGB === null) {
          const usedMB = _toNumberOrNull(g?.memory_used_MB);
          if (usedMB !== null) usedGB = usedMB / 1024;
        }

        const utilPctRaw = _toNumberOrNull(g?.utilization_gpu_pct ?? g?.utilization_gpu_percent);
        const utilPct = utilPctRaw === null ? null : _gpuClamp(utilPctRaw, 0, 100);
        const powerW = _toNumberOrNull(g?.power_draw_watts ?? g?.power_draw_W);
        const tempC = _toNumberOrNull(g?.temperature_c ?? g?.temperature_gpu);

        const memPct = (totalGB !== null && usedGB !== null && totalGB > 0)
          ? _gpuClamp((usedGB / totalGB) * 100, 0, 100)
          : null;
        const memClass = memPct === null ? "" : _gpuPercentClass(memPct);
        const utilClass = utilPct === null ? "" : _gpuUtilClass(utilPct);

        _pushGpuHistory(gpuIndex, utilPct, memPct);

        const hist = gpuHistory[String(gpuIndex)] || { util: [], mem: [] };
        const title = `${gpuFullName} (GPU ${gpuIndex})`;
        const card = ensureGpuCard(host, gpuIndex);
        const refs = getGpuCardRefs(card);
        seenKeys.add(gpuKey);
        const subtitleParts = [`GPU ${gpuIndex}`];
        if (g?.vendor || g?.backend) {
          const vendorText = g?.vendor ? String(g.vendor).toUpperCase() : "";
          const backendText = g?.backend ? String(g.backend) : "";
          subtitleParts.push([vendorText, backendText].filter(Boolean).join(" / "));
        }
        const memoryText = (usedGB !== null && totalGB !== null)
          ? `${usedGB.toFixed(1)}/${totalGB.toFixed(1)}GB`
          : (usedGB !== null ? `${usedGB.toFixed(1)}GB used` : "\u2014");

        if (refs.title) {
          refs.title.textContent = shortDisplay;
          refs.title.title = title;
        }
        if (refs.subtitle) refs.subtitle.textContent = subtitleParts.join(" \u00B7 ");
        if (refs.memoryText) refs.memoryText.textContent = memoryText;
        if (refs.memoryPill) {
          refs.memoryPill.className = memClass
            ? `gpu-stat-pill vram gpu-memory-pill ${memClass}`
            : "gpu-stat-pill vram gpu-memory-pill";
          refs.memoryPill.textContent = memPct === null ? "VRAM \u2014" : `${memPct.toFixed(0)}% VRAM`;
        }
        if (refs.memoryBar) {
          refs.memoryBar.className = memClass
            ? `gpu-bar-fill gpu-bar-memory gpu-memory-bar ${memClass}`
            : "gpu-bar-fill gpu-bar-memory gpu-memory-bar";
          refs.memoryBar.style.width = memPct === null ? "0%" : `${memPct.toFixed(1)}%`;
        }
        if (refs.utilText) refs.utilText.textContent = utilPct === null ? "\u2014" : `${utilPct.toFixed(0)}%`;
        if (refs.utilBar) {
          refs.utilBar.className = utilClass
            ? `gpu-bar-fill gpu-bar-util gpu-util-bar ${utilClass}`
            : "gpu-bar-fill gpu-bar-util gpu-util-bar";
          refs.utilBar.style.width = utilPct === null ? "0%" : `${utilPct.toFixed(1)}%`;
        }
        if (refs.utilMiniValue) refs.utilMiniValue.textContent = utilPct === null ? "\u2014" : `${utilPct.toFixed(0)}%`;
        if (refs.utilMiniChart) setHtml(refs.utilMiniChart, utilPct === null ? `<div class="gpu-metric-text">\u2014</div>` : _buildMiniSparkSvg(hist.util, "util"));
        if (refs.memMiniValue) refs.memMiniValue.textContent = memPct === null ? "\u2014" : `${memPct.toFixed(0)}%`;
        if (refs.memMiniChart) setHtml(refs.memMiniChart, memPct === null ? `<div class="gpu-metric-text">\u2014</div>` : _buildMiniSparkSvg(hist.mem, "mem"));
        if (refs.powerPill) refs.powerPill.textContent = powerW === null ? "Power \u2014" : `Power ${powerW.toFixed(1)}W`;
        if (refs.tempPill) {
          refs.tempPill.style.display = "";
          refs.tempPill.textContent = tempC === null ? "Temp \u2014" : `Temp ${tempC.toFixed(0)}\u00B0C`;
        }

        host.appendChild(card);
      });

      host.querySelectorAll(".gpu-card").forEach((card) => {
        if (!seenKeys.has(card.dataset.gpuIndex || "")) {
          card.remove();
        }
      });

      return;
    }
  }

  function renderSystemRam(data) {
    const ramEl = $("systemRamUsage");
    if (!ramEl) return;

    const ram = data?.system_ram || {};
    const usedGB = _toNumberOrNull(ram.used_gb);
    const totalGB = _toNumberOrNull(ram.total_gb);
    const percentUsed = _toNumberOrNull(ram.percent_used);

    if (!ram.available || usedGB === null || totalGB === null || totalGB <= 0) {
      ramEl.textContent = "\u2014";
      ramEl.title = "System RAM unavailable";
      return;
    }

    ramEl.textContent = `${usedGB.toFixed(1)}/${totalGB.toFixed(1)}GB`;
    ramEl.title = percentUsed === null
      ? `${usedGB.toFixed(1)} GB used of ${totalGB.toFixed(1)} GB`
      : `${usedGB.toFixed(1)} GB used of ${totalGB.toFixed(1)} GB (${percentUsed.toFixed(0)}% used)`;
  }

  function renderProcessTable(data) {
    const procCont = $("processList");
    if (!procCont) return;

    if (data?.processes_supported === false) {
      procCont.textContent = "";
      const empty = document.createElement("div");
      empty.className = "gpu-empty";
      empty.textContent = String(data?.processes_message || "GPU process telemetry is unavailable.");
      procCont.appendChild(empty);
      return;
    }
  
    const processes = Array.isArray(data?.processes) ? data.processes : [];
    {
      if (processes.length === 0) {
        procCont.textContent = "";
        const empty = document.createElement("div");
        empty.className = "gpu-empty";
        empty.textContent = "No active GPU compute processes.";
        procCont.appendChild(empty);
        return;
      }

      const emptyState = procCont.querySelector(".gpu-empty");
      if (emptyState) emptyState.remove();

      const tbodyLive = ensureProcessTable(procCont);
      const seenKeys = new Set();

      processes.forEach((p) => {
        const pid = String(p?.pid ?? "");
        const processName = String(p?.process_name ?? "");
        const gpuList = Array.isArray(p?.gpus) ? p.gpus : [];
        const totalMem = Number(p?.total_used_memory_MB || 0);
        const rowKey = `${pid}::${processName}`;

        let row = Array.from(tbodyLive.querySelectorAll("tr[data-proc-key]"))
          .find((candidate) => candidate.dataset.procKey === rowKey);
        if (!row) {
          row = document.createElement("tr");
          row.dataset.procKey = rowKey;
          row.innerHTML = `
            <td class="gpu-proc-pid"></td>
            <td class="mono gpu-proc-exe"></td>
            <td class="gpu-proc-usage"></td>
            <td class="num gpu-proc-total"></td>
          `;
          tbodyLive.appendChild(row);
        }

        const pidCell = row.querySelector(".gpu-proc-pid");
        const exeCell = row.querySelector(".gpu-proc-exe");
        const usageCell = row.querySelector(".gpu-proc-usage");
        const totalCell = row.querySelector(".gpu-proc-total");

        if (pidCell) pidCell.textContent = pid;
        if (exeCell) {
          exeCell.textContent = processName;
          exeCell.title = processName;
        }
        if (usageCell) renderProcessUsageCell(usageCell, gpuList);
        if (totalCell) totalCell.textContent = `${totalMem.toFixed(0)} MB`;

        seenKeys.add(rowKey);
        tbodyLive.appendChild(row);
      });

      tbodyLive.querySelectorAll("tr[data-proc-key]").forEach((row) => {
        if (!seenKeys.has(row.dataset.procKey || "")) {
          row.remove();
        }
      });

      return;
    }
  }

  function startSMIAutoRefresh() {
    smiAutoRefreshEnabled = true;
    clearSmiTimer();
    syncSMIAutoRefreshToDrawerState();
  }

  function stopSMIAutoRefresh() {
    smiAutoRefreshEnabled = false;
    clearSmiTimer();
    cancelSmiFetch();
    setSmiTitle("Manual");
  }

  async function copySMIOutput() {
    const smiCont = $("smiContent");
    if (!smiCont) return;

    try {
      await navigator.clipboard.writeText(smiCont.innerText || "");
      if (typeof window.showCustomAlert === "function") {
        window.showCustomAlert("GPU telemetry output copied!");
      }
    } catch (e) {
      console.error("copySMIOutput failed:", e);
    }
  }

  async function adminRescanModels() {
    setAdminStatus("Rescanning models…", null);
    try {
      const res = await fetch(`${MODEL_PREFIX}/registry/rescan`, {
        method: "POST",
        headers: { "X-CSRFToken": window.CSRF_TOKEN },
        credentials: "same-origin"
      });

      const data = await safeJson(res);
      if (!res.ok || !data || data.status !== "success") {
        const msg = (data && data.message) ? data.message : ("HTTP " + res.status);
        setAdminStatus("Rescan failed: " + msg, false);
        return;
      }

      const scanned = data.info && typeof data.info.scanned === "number" ? data.info.scanned : null;
      setAdminStatus(scanned !== null ? `Rescan complete. Found ${scanned} model(s).` : "Rescan complete.", true);

      if (typeof window.loadModelDropdown === "function") window.loadModelDropdown();
      if ($("adminRegistryTbody")) adminRegistryRefresh();
    } catch (e) {
      setAdminStatus("Rescan error: " + (e && e.message ? e.message : e), false);
    }
  }

  async function adminRegistryFetchList() {
    const tbody = $("adminRegistryTbody");
    if (!tbody) return null;

    setRegistryStatus("Loading registry…", null);

    const res = await fetch(`${MODEL_PREFIX}/registry/list`, {
      method: "GET",
      cache: "no-store",
      credentials: "same-origin"
    });

    const data = await safeJson(res);

    if (!res.ok || !data) {
      setRegistryStatus("Registry load failed: HTTP " + res.status, false);
      return null;
    }

    if (data.status !== "success" || !Array.isArray(data.models)) {
      setRegistryStatus("Registry load failed: " + (data.message || "Invalid response"), false);
      return null;
    }

    _registryCache = data.models;
    setRegistryStatus(`Loaded ${data.models.length} model(s).`, true);
    return data.models;
  }

  function _fmtBytesToGb(bytes) {
    const n = Number(bytes);
    if (!Number.isFinite(n) || n <= 0) return "—";
    const gb = n / (1024 ** 3);
    return (Math.round(gb * 100) / 100).toLocaleString(undefined, { maximumFractionDigits: 2 });
  }

  function _fmtMtime(ts) {
    const n = Number(ts);
    if (!Number.isFinite(n) || n <= 0) return "—";
    const d = new Date(n * 1000);
    if (isNaN(d.getTime())) return "—";
    return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "2-digit" });
  }

  function adminRegistryRowHtml(row) {
    const id = Number(row.id);
    const name = String(row.model_name ?? "Unknown");

    const present = as01(row.is_present);
    const enabled = as01(row.is_enabled);
    const fav = as01(row.is_favorite);
    const bench = as01(row.allow_benchmark);
    const projector = as01(row.is_projector);
    const speech = as01(row.is_s2t);
    const languageModel = !speech && String(row.model_path || "").toLowerCase().endsWith(".gguf");

    const sizeGb = _fmtBytesToGb(row.file_size);
    const mtime = _fmtMtime(row.mtime);
    const safeName = _escape(name);
    const friendlyName = _escape(String(row.friendly_name || "").slice(0, 255));
    const notes = _escape(String(row.notes || "").slice(0, 512));
    const mmprojPath = _escape(String(row.mmproj_path || ""));
    const maxTps = Number(row.max_tps);

    const GREEN = "#7CFC90";
    const YELLOW = "#f1c40f";
    const RED = "#ff6b6b";

    let enabledColor = YELLOW;
    let enabledTitle = "Present but disabled";

    if (!present) {
      enabledColor = RED;
      enabledTitle = "Missing";
    } else if (enabled) {
      enabledColor = GREEN;
      enabledTitle = "Enabled";
    }

    const enabledBadge = `<span class="admin-registry-state-dot" style="color:${enabledColor};">●</span>`;

    const profileControls = REGISTRY_PROFILE_FIELDS.map(([field, label]) => `
      <label class="registry-profile-toggle" title="${label} profile">
        <input type="checkbox" data-metadata-field="${field}" ${as01(row[field]) ? "checked" : ""}>
        <span>${label}</span>
      </label>
    `).join("");

    const favBadge = fav ? "⭐" : "☆";
    const benchButtonHtml = projector || !languageModel
      ? `
          <button type="button" class="user-action-btn" disabled
            title="Only language models can be benchmarked">
            Benchmark Disabled
          </button>
        `
      : enabled
      ? `
          <button type="button" class="user-action-btn registry-btn"
            data-action="toggle" data-id="${id}" data-field="allow_benchmark" data-value="${bench ? 0 : 1}"
            title="${bench ? "Disable benchmarking for this model" : "Enable benchmarking for this model"}">
            ${bench ? "Disable Bench" : "Enable Bench"}
          </button>
        `
      : `
          <button type="button" class="user-action-btn" disabled
            title="Enable this model before benchmarking"
            style="opacity:0.55; cursor:not-allowed;">
            Enable Model First
          </button>
        `;

    return `
      <tr data-registry-id="${id}">
        <td style="text-align:center;" title="${enabledTitle}">${enabledBadge}</td>
        <td>
          <div class="admin-registry-model-name" title="${safeName}">
            <span class="admin-registry-model-filename">${safeName}</span>
            <div class="admin-registry-profiles" aria-label="Model profiles">${profileControls}</div>
          </div>
          <div class="admin-registry-metadata-row">
            <div class="registry-special-capabilities" role="group" aria-label="Special model capabilities">
            <label class="registry-profile-toggle registry-projector-state-toggle" title="Treat this registry row as an MMPROJ projector file">
              <input type="checkbox" class="registry-projector-toggle" data-id="${id}" ${projector ? "checked" : ""}>
              <span>MMPROJ File</span>
            </label>
            <label class="registry-profile-toggle" title="Eligible for Speech-to-Text selection">
              <input type="checkbox" class="registry-speech-toggle" data-id="${id}" ${speech ? "checked" : ""}>
              <span>Speech-to-Text</span>
            </label>
            </div>
            <label class="registry-inline-field registry-friendly-name-field">
              <span>Friendly Name</span>
              <input type="text" data-metadata-field="friendly_name" value="${friendlyName}" maxlength="255" placeholder="Optional display title">
            </label>
            <label class="registry-inline-field registry-projector-field">
              <span>Projector file</span>
              <input type="text" data-metadata-field="mmproj_path" value="${mmprojPath}" maxlength="1024" placeholder="filename.gguf" title="Filename in the configured Models directory">
            </label>
            <label class="registry-inline-field registry-notes-field">
              <span>Notes</span>
              <input type="text" data-metadata-field="notes" value="${notes}" maxlength="512" placeholder="Short operator note">
            </label>
          </div>
        </td>
        <td class="admin-registry-tps-cell">${Number.isFinite(maxTps) && maxTps > 0 ? `<span class="model-tps is-measured">Max ${Math.round(maxTps)} TPS</span>` : `<span class="model-tps is-missing">—</span>`}</td>
        <td class="admin-registry-file-cell"><strong>${sizeGb} GB</strong><span>${mtime}</span></td>
        <td class="admin-registry-actions-cell">
          ${projector
            ? `<button type="button" class="user-action-btn" disabled title="MMPROJ files cannot be enabled">Projector</button>`
            : `<button type="button" class="user-action-btn registry-btn"
                data-action="toggle" data-id="${id}" data-field="is_enabled" data-value="${enabled ? 0 : 1}">
                ${enabled ? "Disable" : "Enable"}
              </button>`}
          <button type="button" class="user-action-btn registry-btn"
            data-action="toggle" data-id="${id}" data-field="is_favorite" data-value="${fav ? 0 : 1}"
            title="Favorite">
            ${favBadge}
          </button>
          ${benchButtonHtml}
          <button type="button" class="user-action-btn registry-metadata-save" data-id="${id}">Save Metadata</button>
        </td>
      </tr>
    `;
  }

  function adminRegistryRender(list) {
    const tbody = $("adminRegistryTbody");
    if (!tbody) return;

    if (!Array.isArray(list) || list.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5">No models found.</td></tr>`;
      return;
    }

    tbody.innerHTML = list.map(adminRegistryRowHtml).join("");
  }

  async function adminRegistryRefresh() {
    const list = await adminRegistryFetchList();
    if (!list) return;
    adminRegistryRender(list);
  }

  async function adminRegistryToggleById(id, field, value01) {
    if (id === undefined || id === null || id === "" || !field) return false;

    const v = (Number(value01) === 1) ? 1 : 0;

    setRegistryStatus(`Updating ${field}…`, null);

    const payload = {
      id: Number(id),
      field: String(field),
      value: v
    };

    const res = await fetch(`${MODEL_PREFIX}/registry/toggle`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": window.CSRF_TOKEN
      },
      credentials: "same-origin",
      body: JSON.stringify(payload)
    });

    const data = await safeJson(res);
    if (!res.ok || !data) {
      setRegistryStatus(`Toggle failed: HTTP ${res.status}`, false);
      return false;
    }
    if (data.status !== "success") {
      setRegistryStatus("Toggle failed: " + (data.message || "Unknown error"), false);
      return false;
    }

    await adminRegistryRefresh();
    if (typeof window.loadModelDropdown === "function") window.loadModelDropdown();
    setRegistryStatus("Updated.", true);
    return true;
  }

  async function adminRegistrySaveMetadata(rowElement) {
    if (!rowElement) return;
    const id = Number(rowElement.dataset.registryId);
    if (!Number.isFinite(id)) return;

    const field = (name) => rowElement.querySelector(`[data-metadata-field="${name}"]`);
    const friendlyName = String(field("friendly_name")?.value || "").trim();
    if (friendlyName.length > 255) {
      setRegistryStatus("Friendly Name must be 255 characters or fewer.", false);
      return;
    }
    const notes = String(field("notes")?.value || "").trim();
    if (notes.length > 512) {
      setRegistryStatus("Notes must be 512 characters or fewer.", false);
      return;
    }

    const payload = {
      id,
      friendly_name: friendlyName,
      notes,
      mmproj_path: String(field("mmproj_path")?.value || "").trim()
    };
    REGISTRY_PROFILE_FIELDS.forEach(([name]) => {
      payload[name] = Boolean(field(name)?.checked);
    });

    const saveButton = rowElement.querySelector(".registry-metadata-save");
    if (saveButton) saveButton.disabled = true;
    setRegistryStatus("Saving model metadata…", null);

    try {
      const res = await fetch(`${MODEL_PREFIX}/registry/metadata`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": window.CSRF_TOKEN
        },
        credentials: "same-origin",
        body: JSON.stringify(payload)
      });
      const data = await safeJson(res);
      if (!res.ok || !data || data.status !== "success") {
        throw new Error((data && (data.message || data.error)) || `HTTP ${res.status}`);
      }

      await adminRegistryRefresh();
      if (typeof window.loadModelDropdown === "function") await window.loadModelDropdown();
      setRegistryStatus("Model metadata saved.", true);
    } catch (error) {
      setRegistryStatus(`Metadata save failed: ${error?.message || error}`, false);
      if (saveButton) saveButton.disabled = false;
    }
  }

  async function adminRegistrySetAllEnabled(enable) {
    const list = Array.isArray(_registryCache) ? _registryCache : [];
    if (list.length === 0) {
      await adminRegistryRefresh();
    }

    const list2 = Array.isArray(_registryCache) ? _registryCache : [];
    if (list2.length === 0) return;

    const target = enable ? 1 : 0;
    const toChange = list2.filter(r => (!enable || !as01(r.is_projector)) && as01(r.is_enabled) !== target);

    if (toChange.length === 0) {
      setRegistryStatus(enable ? "All models already enabled." : "All models already disabled.", true);
      return;
    }

    setRegistryStatus(`${enable ? "Enabling" : "Disabling"} ${toChange.length} model(s)…`, null);

    for (let i = 0; i < toChange.length; i++) {
      const r = toChange[i];
      await adminRegistryToggleById(r.id, "is_enabled", target);
    }

    setRegistryStatus(`Done. ${enable ? "Enabled" : "Disabled"} ${toChange.length} model(s).`, true);
  }

  function wireRegistryButtons() {
    const refreshBtn = $("adminRegistryRefreshBtn");
    const enableAllBtn = $("adminRegistryEnableAllBtn");
    const disableAllBtn = $("adminRegistryDisableAllBtn");
    const tbody = $("adminRegistryTbody");

    if (refreshBtn && !refreshBtn._wired) {
      refreshBtn._wired = true;
      refreshBtn.addEventListener("click", (e) => { e.preventDefault(); adminRegistryRefresh(); });
    }
    if (enableAllBtn && !enableAllBtn._wired) {
      enableAllBtn._wired = true;
      enableAllBtn.addEventListener("click", (e) => { e.preventDefault(); adminRegistrySetAllEnabled(true); });
    }
    if (disableAllBtn && !disableAllBtn._wired) {
      disableAllBtn._wired = true;
      disableAllBtn.addEventListener("click", (e) => { e.preventDefault(); adminRegistrySetAllEnabled(false); });
    }

    if (tbody && !tbody._wired) {
      tbody._wired = true;
      tbody.addEventListener("change", (e) => {
        const toggle = e.target?.closest?.(".registry-projector-toggle, .registry-speech-toggle");
        if (!toggle) return;

        toggle.disabled = true;
        const requestedState = toggle.checked;
        adminRegistryToggleById(toggle.getAttribute("data-id"), toggle.classList.contains("registry-speech-toggle") ? "is_s2t" : "is_projector", requestedState ? 1 : 0)
          .then((updated) => {
            if (!updated && toggle.isConnected) toggle.checked = !requestedState;
          })
          .catch((error) => {
            if (toggle.isConnected) toggle.checked = !requestedState;
            setRegistryStatus(`Toggle failed: ${error?.message || error}`, false);
          })
          .finally(() => { toggle.disabled = false; });
      });
      tbody.addEventListener("click", (e) => {
        const metadataButton = e.target?.closest?.(".registry-metadata-save");
        if (metadataButton) {
          e.preventDefault();
          adminRegistrySaveMetadata(metadataButton.closest("tr[data-registry-id]"));
          return;
        }
        const btn = e.target?.closest?.(".registry-btn");
        if (!btn) return;
        e.preventDefault();

        const id = btn.getAttribute("data-id");
        const field = btn.getAttribute("data-field");
        const value = btn.getAttribute("data-value");
        adminRegistryToggleById(id, field, value);
      });
    }
  }

  function wireButtons() {
    const btnLogStart = $("btnLogStart");
    const btnLogStop = $("btnLogStop");
    const btnLogClear = $("btnLogClear");

    if (btnLogStart && !btnLogStart._wired) {
      btnLogStart._wired = true;
      btnLogStart.addEventListener("click", startLogStream);
    }
    if (btnLogStop && !btnLogStop._wired) {
      btnLogStop._wired = true;
      btnLogStop.addEventListener("click", stopLogStream);
    }
    if (btnLogClear && !btnLogClear._wired) {
      btnLogClear._wired = true;
      btnLogClear.addEventListener("click", clearLogs);
    }

    const btnSmiPoll = $("btnSmiPoll");
    const btnSmiStop = $("btnSmiStop");
    const btnSmiCopy = $("btnSmiCopy");

    if (btnSmiPoll && !btnSmiPoll._wired) {
      btnSmiPoll._wired = true;
      btnSmiPoll.addEventListener("click", startSMIAutoRefresh);
    }
    if (btnSmiStop && !btnSmiStop._wired) {
      btnSmiStop._wired = true;
      btnSmiStop.addEventListener("click", stopSMIAutoRefresh);
    }
    if (btnSmiCopy && !btnSmiCopy._wired) {
      btnSmiCopy._wired = true;
      btnSmiCopy.addEventListener("click", copySMIOutput);
    }

    ensureLogsDrawerObserver();
    ensureSMIDrawerObserver();
  }

  function wireAdminButtons() {
    const rsBtn = $("adminRescanModelsBtn");
    const saveBtn = $("saveSettingsBtn");

    if (rsBtn && !rsBtn._wired) {
      rsBtn._wired = true;
      rsBtn.addEventListener("click", (e) => { e.preventDefault(); adminRescanModels(); });
    }

    if (saveBtn && !saveBtn._wired) {
      saveBtn._wired = true;
      saveBtn.addEventListener("click", (e) => {
        e.preventDefault();
        if (typeof saveSettings === "function") saveSettings();
      });
    }

    wireRegistryButtons();
  }

  function stopAllSystemStreams() {
    stopLogStream();
    stopSMIAutoRefresh();
  }

  window.SystemDrawer = {
    renderLogLines,
    fetchLogs,
    startLogStream,
    stopLogStream,
    clearLogs,
    fetchSMI,
    startSMIAutoRefresh,
    stopSMIAutoRefresh,
    copySMIOutput,
    stopAllSystemStreams,
    adminRegistryRefresh,
    adminRegistryToggleById,
    adminRegistrySetAllEnabled
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      wireButtons();
      wireAdminButtons();
      if ($("adminRegistryTbody")) adminRegistryRefresh();
    });
  } else {
    wireButtons();
    wireAdminButtons();
    if ($("adminRegistryTbody")) adminRegistryRefresh();
  }
})();
