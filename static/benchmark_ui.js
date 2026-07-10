// benchmark_ui.js
(function () {
  const BENCH_PREFIX = window.BENCH_PREFIX || "/benchmark";

  // ----------------------------
  // DOM helpers
  // ----------------------------
  function byId(id) { return document.getElementById(id); }

  // Try multiple possible IDs (keeps this resilient to small HTML changes)
  function pickEl(ids) {
    for (const id of ids) {
      const el = byId(id);
      if (el) return el;
    }
    return null;
  }

  function setText(el, txt) {
    if (!el) return;
    el.textContent = (txt === undefined || txt === null) ? "" : String(txt);
  }

  function clamp(n, a, b) {
    return Math.max(a, Math.min(b, n));
  }

  function safeNum(v, def = 0) {
    const n = Number(v);
    return Number.isFinite(n) ? n : def;
  }

  function fmtLocal(tsSeconds) {
    try {
      if (!tsSeconds) return "—";
      const d = new Date(Number(tsSeconds) * 1000);
      if (isNaN(d.getTime())) return "—";
      return d.toLocaleString();
    } catch (e) {
      return "—";
    }
  }

  // Escape HTML in table/details
  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (m) {
      return ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\"": "&quot;",
        "'": "&#39;"
      })[m];
    });
  }

  // Robust JSON helper (handles HTML/CSRF pages too)
  async function readJsonOrText(resp) {
    const text = await resp.text().catch(() => "");
    let j = null;
    try {
      j = text ? JSON.parse(text) : {};
    } catch (_) {
      j = { status: "error", message: text || "Non-JSON response" };
    }
    return { text, json: j };
  }

  // ----------------------------
  // State / Polling
  // ----------------------------
  let pollTimer = null;
  let pollInFlightPromise = null;
  let pollActive = false;
  let lastState = null;

  async function fetchBenchStatus() {
    const r = await fetch(`${BENCH_PREFIX}/status`, { method: "GET", credentials: "same-origin" });
    if (!r.ok) throw new Error(`status HTTP ${r.status}`);
    const j = await r.json();
    if (j.status !== "success") throw new Error(j.message || "status error");
    return j.state || {};
  }

  function computeProgress(state) {
    const totalPrompts = safeNum(state.total_prompts, 0);
    const currentPrompt = safeNum(state.current_prompt_idx, 0);

    let pct = 0;
    if (totalPrompts > 0) pct = (currentPrompt / totalPrompts) * 100;
    pct = clamp(pct, 0, 100);

    return {
      promptText: `Prompt: ${currentPrompt}/${totalPrompts}`,
      pct
    };
  }

  function renderState(state) {
    lastState = state || {};

    // --- CURRENT HTML IDs (with fallbacks) ---
    const statusEl  = pickEl(["benchStatusLine", "benchStatusText"]);
    const msgEl     = pickEl(["benchMessageLine"]);
    const countsEl  = pickEl(["benchCountsLine"]);
    const startedEl = pickEl(["benchStartedLine"]);
    const endedEl   = pickEl(["benchEndedLine"]);

    const modelEl   = pickEl(["benchModel", "benchCurrentModel"]);
    const promptEl  = pickEl(["benchPromptProgress", "benchCurrentPrompt"]);

    const pctEl     = pickEl(["benchProgressPct", "benchProgressLabel"]);
    const fillEl    = pickEl(["benchProgressFill"]);
    const progEl    = pickEl(["benchProgressBar"]);

    // Buttons (your current HTML uses benchRunBtn/benchStopBtn/benchRefreshBtn)
    const runBtn    = pickEl(["benchRunBtn", "benchStartBtn", "benchmarkRunBtn"]);
    const stopBtn   = pickEl(["benchStopBtn"]);
    const refreshBtn= pickEl(["benchRefreshBtn", "benchRefreshBestBtn", "benchRefreshBtnLegacy"]);
    const status = (state.status || "idle");
    const isRunning = !!state.running;
    const isStopping = (status === "stopping");

    // Status lines
    setText(statusEl, status);
    setText(msgEl, state.message || "");

    const totalModels = safeNum(state.total_models, 0);
    const pass = safeNum(state.benchmarked_models, 0);
    const fail = safeNum(state.failed_models, 0);
    const skip = safeNum(state.skipped_models, 0);

    setText(countsEl, `Models: ${totalModels} total | ${pass} pass | ${fail} failed | ${skip} skipped`);
    setText(startedEl, `Started: ${fmtLocal(state.started_at)}`);
    setText(endedEl, `Ended: ${state.ended_at ? fmtLocal(state.ended_at) : "—"}`);

    // Current model + prompt progress
    setText(modelEl, `Model: ${state.current_model || "—"}`);

    const p = computeProgress(state);
    setText(promptEl, p.promptText);

    if (pctEl) setText(pctEl, `${Math.round(p.pct)}%`);

    // Progress bar (your HTML uses a DIV fill)
    if (fillEl) fillEl.style.width = `${p.pct}%`;

    // Legacy <progress> support if it exists somewhere
    if (progEl && typeof progEl.value !== "undefined") {
      progEl.max = 100;
      progEl.value = p.pct;
    }

    // Button states
    if (runBtn) {
      runBtn.disabled = isRunning; // disable while running/stopping
      runBtn.textContent = isRunning ? "Benchmark running..." : "Run Benchmark";
    }
    if (stopBtn) {
      stopBtn.disabled = !isRunning;
      stopBtn.textContent = isStopping ? "Stopping..." : "Stop";
    }
    if (refreshBtn) {
      refreshBtn.disabled = false;
    }
    setBenchPromptEditorLocked(isRunning);

    // Poll while running
    if (isRunning) ensurePolling();
    else stopPolling();
  }

  async function pollTick() {
    if (pollInFlightPromise) return pollInFlightPromise;

    pollInFlightPromise = (async () => {
      try {
        const st = await fetchBenchStatus();
        renderState(st);
        return st;
      } catch (e) {
        return null;
      } finally {
        pollInFlightPromise = null;
        if (pollActive) scheduleNextPoll(1500);
      }
    })();

    return pollInFlightPromise;
  }

  function clearPollTimer() {
    if (!pollTimer) return;
    clearTimeout(pollTimer);
    pollTimer = null;
  }

  function scheduleNextPoll(delayMs = 1500) {
    clearPollTimer();
    if (!pollActive) return;
    pollTimer = setTimeout(() => {
      pollTimer = null;
      pollTick();
    }, delayMs);
  }

  function ensurePolling() {
    pollActive = true;
    if (pollInFlightPromise || pollTimer) return;
    scheduleNextPoll(1500);
  }

  function stopPolling() {
    pollActive = false;
    clearPollTimer();
  }

  function setBenchPromptEditorStatus(message, kind = "") {
    const statusEl = byId("benchPromptEditorStatus");
    if (!statusEl) return;
    statusEl.textContent = message || "";
    statusEl.className = `bench-prompt-editor-status${kind ? ` ${kind}` : ""}`;
  }

  function ensureBenchPromptLockOverlay() {
    const editor = document.querySelector(".bench-prompt-editor");
    if (!editor) return null;

    let overlay = editor.querySelector(".bench-prompt-editor-lock-overlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.className = "bench-prompt-editor-lock-overlay";
      overlay.innerHTML = `
        <div class="bench-prompt-editor-lock-message">
          <strong>Prompts are locked while a benchmark is running.</strong>
          <span>Stop the benchmark to edit benchmark questions.</span>
        </div>
      `;
      editor.appendChild(overlay);
    }
    return overlay;
  }

  function setBenchPromptEditorLocked(isLocked) {
    const editor = document.querySelector(".bench-prompt-editor");
    if (!editor) return;

    const locked = Boolean(isLocked);
    editor.classList.toggle("is-locked", locked);

    const overlay = ensureBenchPromptLockOverlay();
    if (overlay) {
      overlay.hidden = !locked;
      overlay.setAttribute("aria-hidden", locked ? "false" : "true");
    }

    const savePromptsBtn = byId("benchPromptsSaveBtn");
    if (savePromptsBtn) {
      savePromptsBtn.disabled = locked;
    }

    Array.from(document.querySelectorAll(".bench-prompt-editor-input")).forEach((input) => {
      input.disabled = locked;
    });
  }

  function renderBenchPromptEditor(prompts) {
    const listEl = byId("benchPromptEditorList");
    if (!listEl) return;

    const rows = Array.isArray(prompts) ? prompts.slice(0, 5) : [];
    if (!rows.length) {
      listEl.innerHTML = `<div class="bench-prompt-editor-empty">No CE benchmark prompts found.</div>`;
      return;
    }

    listEl.innerHTML = rows.map((prompt, idx) => `
      <div class="bench-prompt-editor-item">
        <label class="bench-prompt-editor-label" for="benchPromptInput${idx + 1}">Prompt ${idx + 1}</label>
        <textarea id="benchPromptInput${idx + 1}"
                  class="bench-prompt-editor-input"
                  data-prompt-id="${escapeHtml(String(prompt.id || ""))}"
                  rows="1">${escapeHtml(prompt.prompt_text || "")}</textarea>
      </div>
    `).join("");
    setBenchPromptEditorLocked(Boolean(lastState && lastState.running));
  }

  function collectBenchPromptPayload() {
    const inputs = Array.from(document.querySelectorAll(".bench-prompt-editor-input"));
    return inputs.map((input, idx) => ({
      id: input.dataset.promptId || "",
      ordering: idx + 1,
      prompt_text: input.value || ""
    }));
  }

  async function loadBenchPrompts() {
    const listEl = byId("benchPromptEditorList");
    if (listEl) {
      listEl.innerHTML = `<div class="bench-prompt-editor-empty">Loading benchmark prompts...</div>`;
    }
    setBenchPromptEditorStatus("");

    const r = await fetch(`${BENCH_PREFIX}/ce_prompts`, { method: "GET", credentials: "same-origin" });
    const { json: j } = await readJsonOrText(r);
    if (!r.ok || j.status !== "success") {
      renderBenchPromptEditor([]);
      setBenchPromptEditorStatus(j.message || `Could not load CE benchmark prompts (HTTP ${r.status})`, "error");
      return;
    }

    renderBenchPromptEditor(j.prompts || []);
  }

  async function saveBenchPrompts() {
    const payload = { prompts: collectBenchPromptPayload() };
    setBenchPromptEditorStatus("Saving CE benchmark questions...");

    const r = await fetch(`${BENCH_PREFIX}/ce_prompts`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": window.CSRF_TOKEN },
      credentials: "same-origin",
      body: JSON.stringify(payload)
    });

    const { json: j } = await readJsonOrText(r);
    if (!r.ok || j.status !== "success") {
      setBenchPromptEditorStatus(j.message || `Could not save CE benchmark questions (HTTP ${r.status})`, "error");
      return;
    }

    await loadBenchPrompts();
    setBenchPromptEditorStatus(j.message || "CE benchmark questions saved.", "success");
  }

  // ----------------------------
  // Best table + sorting
  // ----------------------------
  function injectBasicThStyling() {
    if (document.getElementById("benchInlineStyle")) return;
  
    const style = document.createElement("style");
    style.id = "benchInlineStyle";
    style.textContent = `
      #benchmarksDrawer table thead th {
        cursor: pointer;
        user-select: none;
        padding: 6px 8px;
        border-bottom: 1px solid rgba(255,255,255,0.15);
      }
      #benchmarksDrawer table thead th:hover {
        background: rgba(255,255,255,0.06);
      }
      .bench-th-sort {
        opacity: 0.8;
        font-size: 0.9em;
        margin-left: 6px;
      }
      .bench-actions {
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
        align-items: center;
        justify-content: flex-end;
      }
      .bench-reset-btn { opacity: 0.95; }
      .bench-present { text-align: center; font-weight: 700; }
  
      /* Benchmark details renderer */
      .bench-details-render {
        margin-top: 12px;
        padding: 14px;
        border: 1px solid rgba(255,255,255,0.12);
        border-radius: 12px;
        background: rgba(0,0,0,0.22);
        min-height: 80px;
      }
      .bench-run-summary {
        margin-bottom: 16px;
        padding-bottom: 12px;
        border-bottom: 1px solid rgba(255,255,255,0.10);
      }
      .bench-run-summary h4,
      .bench-prompt-card h4 {
        margin: 0 0 8px 0;
      }
      .bench-run-summary-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 8px 16px;
      }
      .bench-run-summary-item {
        opacity: 0.95;
      }
      .bench-run-summary-item b {
        display: inline-block;
        min-width: 88px;
      }
      .bench-prompt-card {
        margin-top: 14px;
        padding: 12px;
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 12px;
        background: #525252;
      }
      .bench-prompt-meta {
        margin-bottom: 10px;
        opacity: 0.9;
        font-size: 0.95em;
      }
      .bench-prompt-error {
        margin-top: 10px;
        color: #ff8f8f;
        font-weight: 600;
      }
      .bench-response-bubble {
        margin-top: 10px;
        padding: 14px 16px;
        border-radius: 16px;
        background: rgb(117, 117, 117);
        border: 1px solid rgba(255,255,255,0.08);
        overflow-x: auto;
      }
      .bench-response-bubble pre {
        white-space: pre-wrap;
        word-break: break-word;
      }
      .bench-response-bubble code {
        white-space: pre-wrap;
        word-break: break-word;
      }
      .bench-empty-details {
        opacity: 0.8;
      }
    `;
    document.head.appendChild(style);
  }

  function parseEndedToNumber(s) {
    try {
      const t = Date.parse(s);
      return Number.isFinite(t) ? t : 0;
    } catch (e) {
      return 0;
    }
  }

  function sortTableByColumn(tableEl, colIndex, type, dir) {
    const tbody = tableEl.tBodies && tableEl.tBodies[0];
    if (!tbody) return;

    const rows = Array.from(tbody.rows);
    rows.sort((a, b) => {
      const av = a.cells[colIndex] ? a.cells[colIndex].innerText.trim() : "";
      const bv = b.cells[colIndex] ? b.cells[colIndex].innerText.trim() : "";

      let aa = av, bb = bv;

      if (type === "num") {
        aa = parseFloat(av) || 0;
        bb = parseFloat(bv) || 0;
      } else if (type === "num_left") {
        aa = parseFloat(String(av).split("/")[0].trim()) || 0;
        bb = parseFloat(String(bv).split("/")[0].trim()) || 0;
      } else if (type === "date") {
        aa = parseEndedToNumber(av);
        bb = parseEndedToNumber(bv);
      } else {
        aa = av.toLowerCase();
        bb = bv.toLowerCase();
      }

      if (aa < bb) return dir === "asc" ? -1 : 1;
      if (aa > bb) return dir === "asc" ? 1 : -1;
      return 0;
    });

    rows.forEach(r => tbody.appendChild(r));
  }

  function wireTableSorting() {
    const tbody = pickEl(["benchBestTbody", "benchBestTableBody"]);
    if (!tbody) return;

    const table = tbody.closest("table");
    if (!table) return;

    if (table.dataset.benchSortWired === "1") return;
    table.dataset.benchSortWired = "1";

    injectBasicThStyling();

    const headers = Array.from(table.querySelectorAll("thead th"));
    if (!headers.length) return;

    const colTypes = {
      0: "text",
      1: "text",
      2: "text",
      3: "num",
      4: "num",
      5: "num_left",
      6: "num",
      7: "num",
      8: "date",
      9: null
    };

    let sortState = { col: null, dir: "asc" };

    headers.forEach((th, idx) => {
      if (idx >= 9) return;

      th.addEventListener("click", () => {
        headers.forEach(h => {
          const tag = h.querySelector(".bench-th-sort");
          if (tag) tag.remove();
        });

        let dir = "asc";
        if (sortState.col === idx) dir = sortState.dir === "asc" ? "desc" : "asc";
        sortState = { col: idx, dir };

        sortTableByColumn(table, idx, colTypes[idx] || "text", dir);

        const badge = document.createElement("span");
        badge.className = "bench-th-sort";
        badge.textContent = dir === "asc" ? "▲" : "▼";
        th.appendChild(badge);
      });
    });
  }

  // ----------------------------
  // Actions: Run, Stop, Refresh, Details, Reset Model
  // ----------------------------
  async function runBench() {
    const forceCb    = pickEl(["benchForceCheckbox"]);

    const payload = {
      profile: "Default v1",
      promptset: "Core Suite",
      version: "v1",
      force: forceCb ? !!forceCb.checked : false
    };

    const r = await fetch(`${BENCH_PREFIX}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": window.CSRF_TOKEN },
      credentials: "same-origin",
      body: JSON.stringify(payload)
    });

    const { json: j } = await readJsonOrText(r);
    if (!r.ok || j.status !== "success") {
      alert(j.message || `Benchmark start failed (HTTP ${r.status})`);
      return;
    }

    await pollTick();
    ensurePolling();
    refreshBestRuns();
  }

  async function stopBench() {
    if (!confirm("Stop benchmarks now? This will cancel the current run.")) return;

    const r = await fetch(`${BENCH_PREFIX}/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": window.CSRF_TOKEN },
      credentials: "same-origin",
      body: JSON.stringify({})
    });

    const { json: j } = await readJsonOrText(r);
    if (!r.ok || j.status !== "success") {
      alert(j.message || `Stop failed (HTTP ${r.status})`);
      return;
    }

    await pollTick();
  }

  async function resetBenchmarkModel(modelName, fingerprint) {
    const fp = (fingerprint || "").trim().toLowerCase();
  
    // STRICT: must be 40 hex chars
    if (!/^[0-9a-f]{40}$/.test(fp)) {
      alert("Reset failed: invalid fingerprint (must be 40 hex chars).");
      return;
    }
  
    const label = modelName ? `"${modelName}"` : "this model";
    if (!confirm(`Clear ALL benchmark history for ${label}?`)) return;
  
    // fingerprint ONLY
    const payload = { fingerprint: fp };
  
    const r = await fetch(`${BENCH_PREFIX}/reset_model`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": window.CSRF_TOKEN },
      credentials: "same-origin",
      body: JSON.stringify(payload)
    });
  
    const { json: j, text } = await readJsonOrText(r);
  
    if (!r.ok || j.status !== "success") {
      alert(j.message || `Reset failed (HTTP ${r.status})`);
      console.error("reset_model response:", { status: r.status, body: text });
      return;
    }
  
    const cleared = (j.cleared_runs !== undefined) ? Number(j.cleared_runs) : null;
    if (cleared !== null && Number.isFinite(cleared)) {
      alert(`${j.message || "Reset complete."}\nCleared runs: ${cleared}`);
    } else if (j.message) {
      alert(j.message);
    }
  
    await refreshBestRuns();
    await pollTick();
  }

  async function refreshBestRuns() {
    const tableBody = pickEl(["benchBestTbody", "benchBestTableBody"]);
    if (!tableBody) return;

    const r = await fetch(`${BENCH_PREFIX}/best`, { method: "GET", credentials: "same-origin" });
    if (!r.ok) return;
    const j = await r.json().catch(() => ({}));
    if (j.status !== "success") return;

    const rows = j.rows || [];
    tableBody.innerHTML = "";

    if (!rows.length) {
      tableBody.innerHTML = `<tr><td colspan="10">No benchmark results yet.</td></tr>`;
      wireTableSorting();
      return;
    }

    rows.forEach(row => {
      const tr = document.createElement("tr");

      const allowBench = (Number(row.allow_benchmark) === 1);
      const status = (row.status || "").toUpperCase();
      const statusDisplay = allowBench ? status : "DISABLED";

      const model = row.model_name || "";
      const modelDisplay = allowBench ? model : `${model} (benchmark disabled)`;

      const size = (row.size_gb !== undefined) ? row.size_gb : "";

      // Eliminate decimals in TPS.
      const avg = Math.round(safeNum(row.avg_eval_tps, 0));
      const min = Math.round(safeNum(row.min_eval_tps, 0));
      const max = Math.round(safeNum(row.max_eval_tps, 0));

      const tokens = safeNum(row.total_tokens_generated, 0);

      const totalMs = safeNum(row.total_ms, 0);
      const totalSec = (totalMs / 1000).toFixed(2) + "s";

      const ended = row.ended_at ? new Date(row.ended_at).toLocaleString() : "";

      const modelId = (row.model_id !== undefined && row.model_id !== null) ? row.model_id : "";
      const fingerprint = (row.fingerprint !== undefined && row.fingerprint !== null) ? row.fingerprint : "";

      const presentRaw =
        (row.present !== undefined) ? row.present :
        (row.is_present !== undefined) ? row.is_present :
        (row.exists !== undefined) ? row.exists :
        null;

      const presentIcon =
        (presentRaw === true || presentRaw === 1) ? "✅" :
        (presentRaw === false || presentRaw === 0) ? "❌" :
        "—";

      const presentTitle =
        (presentRaw === true || presentRaw === 1) ? "Model is present in scan directory" :
        (presentRaw === false || presentRaw === 0) ? "Model is NOT present in scan directory" :
        "Presence unknown (backend not providing flag)";

      tr.innerHTML = `
        <td class="bench-present" title="${escapeHtml(presentTitle)}">${escapeHtml(String(presentIcon))}</td>
        <td>${escapeHtml(statusDisplay)}</td>
        <td>${escapeHtml(modelDisplay)}</td>
        <td>${escapeHtml(String(size))}</td>
        <td>${escapeHtml(String(avg))}</td>
        <td>${escapeHtml(`${min} / ${max}`)}</td>
        <td>${escapeHtml(String(tokens))}</td>
        <td>${escapeHtml(String(totalSec))}</td>
        <td>${escapeHtml(String(ended))}</td>
        <td>
          <div class="bench-actions">
            <button class="bench-details-btn"
                    data-run-id="${row.run_id}"
                    ${allowBench ? "" : "disabled"}
                    title="${allowBench ? "Details" : "Benchmark disabled for this model"}">
              Details
            </button>
            <button class="bench-reset-btn"
                    title="${allowBench ? "Clear ALL benchmark results for this model" : "Benchmark disabled for this model"}"
                    data-model-id="${escapeHtml(String(modelId))}"
                    data-model-name="${escapeHtml(String(model))}"
                    data-fingerprint="${escapeHtml(String(fingerprint))}"
                    ${allowBench ? "" : "disabled"}>
              Reset
            </button>
          </div>
        </td>
      `;

      tableBody.appendChild(tr);
    });

    wireTableSorting();
  }
  function renderBenchmarkResponseHtml(text) {
    const raw = String(text || "");
    if (!raw) return "<em>No response captured.</em>";

    if (window.renderSafeMarkdown && typeof window.renderSafeMarkdown === "function") {
      return window.renderSafeMarkdown(raw);
    }
  
    return `<pre>${escapeHtml(raw)}</pre>`;
  }
  
  function typesetBenchmarkMath(container) {
    try {
      if (window.MathJax && typeof window.MathJax.typesetPromise === "function") {
        window.MathJax.typesetPromise([container]).catch(() => {});
      }
    } catch (_) {}
  }
  async function loadRunDetails(runId) {
    const out = pickEl(["benchDetails", "benchRunDetails"]);
    if (out) out.innerHTML = `<div class="bench-empty-details">Loading run details...</div>`;
  
    const r = await fetch(`${BENCH_PREFIX}/run/${runId}`, { method: "GET", credentials: "same-origin" });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.status !== "success") {
      if (out) out.innerHTML = `<div class="bench-empty-details">${escapeHtml(j.message || `Failed to load run ${runId}`)}</div>`;
      return;
    }
  
    const run = j.run || {};
    const results = j.results || [];
  
    const summaryHtml = `
      <div class="bench-run-summary">
        <h4>Run #${escapeHtml(String(runId))}</h4>
        <div class="bench-run-summary-grid">
          <div class="bench-run-summary-item"><b>Model:</b> ${escapeHtml(run.model_name || "")}</div>
          <div class="bench-run-summary-item"><b>Status:</b> ${escapeHtml(run.status || "")}</div>
          <div class="bench-run-summary-item"><b>Profile:</b> ${escapeHtml(run.profile_name || "")}</div>
          <div class="bench-run-summary-item"><b>Prompt Set:</b> ${escapeHtml(run.promptset_name || "")}</div>
          <div class="bench-run-summary-item"><b>Started:</b> ${escapeHtml(run.started_at || "")}</div>
          <div class="bench-run-summary-item"><b>Ended:</b> ${escapeHtml(run.ended_at || "")}</div>
        </div>
      </div>
    `;
  
    const cardsHtml = results.length
      ? results.map((rp) => {
          const ok = rp.success ? "✅ PASS" : "❌ FAIL";
          const tps = Math.round(safeNum(rp.eval_tps, 0));
          const gen = rp.tokens_generated || 0;
          const totalS = (safeNum(rp.total_ms, 0) / 1000).toFixed(2);
  
          return `
            <div class="bench-prompt-card">
              <h4>#${escapeHtml(String(rp.ordering || ""))} ${escapeHtml(rp.category || "")}</h4>
              <div class="bench-prompt-meta">
                <div><b>Status:</b> ${ok}</div>
                <div><b>Eval TPS:</b> ${escapeHtml(String(tps))}</div>
                <div><b>Generated Tokens:</b> ${escapeHtml(String(gen))}</div>
                <div><b>Total:</b> ${escapeHtml(String(totalS))}s</div>
              </div>
              <div class="bench-response-bubble">
                ${renderBenchmarkResponseHtml(rp.response_text || "")}
              </div>
              ${rp.error ? `<div class="bench-prompt-error">Error: ${escapeHtml(rp.error)}</div>` : ""}
            </div>
          `;
        }).join("")
      : `<div class="bench-empty-details">No per-prompt results found for this run.</div>`;
  
    if (out) {
      out.innerHTML = summaryHtml + cardsHtml;
      typesetBenchmarkMath(out);
    }
  }

  function wireButtons() {
    const runBtn = pickEl(["benchRunBtn", "benchStartBtn", "benchRunBtnLegacy", "benchmarkRunBtn"]);
    const stopBtn = pickEl(["benchStopBtn"]);
    const refreshBtn = pickEl(["benchRefreshBtn", "benchRefreshBestBtn", "benchRefreshBtnLegacy"]);
    const savePromptsBtn = byId("benchPromptsSaveBtn");

    if (runBtn) runBtn.addEventListener("click", runBench);
    if (stopBtn) stopBtn.addEventListener("click", stopBench);
    if (savePromptsBtn) savePromptsBtn.addEventListener("click", saveBenchPrompts);
    if (refreshBtn) refreshBtn.addEventListener("click", () => {
      refreshBestRuns();
      pollTick();
      loadBenchPrompts();
    });

    // Details buttons (event delegation)
    document.addEventListener("click", (e) => {
      const btn = e.target && e.target.closest && e.target.closest(".bench-details-btn");
      if (!btn) return;
      const runId = btn.getAttribute("data-run-id");
      if (!runId) return;
      loadRunDetails(runId);
    });

    // Reset buttons (event delegation)
    document.addEventListener("click", (e) => {
      const btn = e.target && e.target.closest && e.target.closest(".bench-reset-btn");
      if (!btn) return;

      const modelName = btn.getAttribute("data-model-name") || "";
      const fingerprint = btn.getAttribute("data-fingerprint") || "";

      resetBenchmarkModel(modelName, fingerprint);
    });
  }

  function init() {
    wireButtons();
    refreshBestRuns();
    loadBenchPrompts();

    pollTick().then(() => {
      if (lastState && lastState.running) ensurePolling();
    });

    wireTableSorting();
  }

  document.addEventListener("DOMContentLoaded", init);

  window.fetchBenchStatus = pollTick;
  window.refreshBestRuns = refreshBestRuns;

})();
