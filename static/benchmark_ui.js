(function () {
  const BENCH_PREFIX = window.BENCH_PREFIX || "/benchmark";

  function byId(id) { return document.getElementById(id); }

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

  function detailAnchorToken(value, fallback = "item") {
    const token = String(value ?? "").trim()
      .replace(/[^A-Za-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "");
    return token || String(fallback);
  }

  function runDetailAnchorId(runId, part) {
    return `bench-run-${detailAnchorToken(runId, "unknown")}-${detailAnchorToken(part)}`;
  }

  function renderRunDetailNavigation(runId, results) {
    const nav = byId("benchDetailNav");
    if (!nav) return;

    const links = [`<a href="#${runDetailAnchorId(runId, "summary")}">Summary</a>`];
    (Array.isArray(results) ? results : []).forEach((rp, index) => {
      const promptNumber = index + 1;
      const ordering = rp && rp.ordering !== undefined && rp.ordering !== null
        ? rp.ordering
        : promptNumber;
      const promptKey = rp && rp.prompt_id !== undefined && rp.prompt_id !== null
        ? rp.prompt_id
        : ordering;
      const anchorId = runDetailAnchorId(runId, `prompt-${promptKey}`);
      const label = `Prompt ${promptNumber}`;
      links.push(`<a href="#${anchorId}" title="${escapeHtml(label)}">${escapeHtml(label)}</a>`);
    });

    nav.innerHTML = links.join("");
    nav.hidden = false;
  }

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

  let pollTimer = null;
  let pollInFlightPromise = null;
  let pollActive = false;
  let lastState = null;
  let lastDetailsTrigger = null;
  let suites = [], categories = [], editing = null, detailData = null;
  let nextHistory = null, historyEpoch = 0, bestEpoch = 0, detailEpoch = 0;
  let managementBusy = false, runStartPending = false;

  function action(fn) {
    return async (...args) => {
      try { await fn(...args); }
      catch (error) { await showCustomAlert(error.message || "Benchmark operation failed."); }
    };
  }

  async function benchGet(path) {
    const data = await window.ApiHttp.requestJSON(`${BENCH_PREFIX}${path}`, { cache: "no-store" });
    if (data.status !== "success") throw new Error(data.message || "Benchmark operation failed.");
    return data;
  }

  async function benchPost(path, payload) {
    const data = await window.ApiHttp.postJSONRequest(`${BENCH_PREFIX}${path}`, payload);
    if (data.status !== "success") throw new Error(data.message || "Benchmark operation failed.");
    return data;
  }

  function selectedSuite(id = byId("benchSuiteSelect")?.value) {
    return suites.find(suite => String(suite.id) === String(id));
  }

  function resultQuery() {
    return new URLSearchParams({ suite_id: byId("benchHistoryRevision")?.value || byId("benchSuiteSelect")?.value || "",
      profile: byId("benchProfileSelect")?.value || "Default v1" }).toString();
  }

  function metric(value, digits = 2) {
    return value === null || value === undefined || !Number.isFinite(Number(value)) ? "Unavailable" : Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
  }

  function runLabel(status, reason) {
    if (reason === "cancelled") return "Cancelled";
    if (reason === "ce_prompts_updated") return "Legacy / stale";
    return ({ PASS: "Run passed", FAILED: "Run failed", RUNNING: "Running / not finalized", STALE: "Legacy / stale" })[status] || status;
  }

  function outcomeLabel(value) {
    return ({ completed: "Answer completed", tool_call: "Tool call returned (not executed)", token_limit: "Token limit reached",
      empty_response: "Empty response", reasoning_only: "Reasoning only", unknown: "Completion unknown", request_error: "Request/runtime failure",
      cancelled: "Cancelled", interrupted: "Interrupted", not_recorded: "No result recorded" })[value] || "Completion unknown";
  }

  function setBenchmarkView(view) {
    const resultsView = byId("benchResultsView");
    const detailsView = byId("benchDetailsView");
    const showDetails = view === "details";

    if (resultsView) resultsView.hidden = showDetails;
    if (detailsView) detailsView.hidden = !showDetails;

    const content = document.querySelector("#benchmarksDrawer .benchmarks-content");
    if (content) content.scrollTop = 0;
  }

  function returnToBenchmarkResults() {
    setBenchmarkView("results");
    if (lastDetailsTrigger && document.contains(lastDetailsTrigger)) {
      lastDetailsTrigger.focus();
    }
  }

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
    const wasRunning = !!lastState?.running;
    lastState = state || {};

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

    const runBtn    = pickEl(["benchRunBtn", "benchStartBtn", "benchmarkRunBtn"]);
    const stopBtn   = pickEl(["benchStopBtn"]);
    const refreshBtn= pickEl(["benchRefreshBtn", "benchRefreshBestBtn", "benchRefreshBtnLegacy"]);
    const status = (state.status || "idle");
    const isRunning = !!state.running;
    const isStopping = (status === "stopping");

    setText(statusEl, status);
    setText(msgEl, state.message || "");

    const totalModels = safeNum(state.total_models, 0);
    const pass = safeNum(state.benchmarked_models, 0);
    const fail = safeNum(state.failed_models, 0);
    const skip = safeNum(state.skipped_models, 0);

    setText(countsEl, `Models: ${totalModels} total / ${pass} execution passed / ${fail} failed / ${skip} skipped`);
    setText(startedEl, `Started: ${fmtLocal(state.started_at)}`);
    setText(endedEl, `Ended: ${state.ended_at ? fmtLocal(state.ended_at) : "—"}`);

    setText(modelEl, `Model: ${state.current_model || "—"}`);

    const p = computeProgress(state);
    setText(promptEl, p.promptText);

    if (pctEl) setText(pctEl, `${Math.round(p.pct)}%`);

    if (fillEl) fillEl.style.width = `${p.pct}%`;

    if (progEl && typeof progEl.value !== "undefined") {
      progEl.max = 100;
      progEl.value = p.pct;
    }

    if (runBtn) {
      runBtn.disabled = isRunning || !selectedSuite();
      runBtn.textContent = isRunning ? "Benchmark running..." : "Run Benchmark";
    }
    if (stopBtn) {
      stopBtn.disabled = !isRunning;
      stopBtn.textContent = isStopping ? "Stopping..." : "Stop";
    }
    if (refreshBtn) {
      refreshBtn.disabled = false;
    }
    updateSuiteControls();
    if (wasRunning && !isRunning) { refreshBestRuns(); loadHistory(); }

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

  function suiteStatus(message, kind = "") {
    const el = byId("benchSuiteEditorStatus");
    if (el) { el.textContent = message; el.className = `bench-prompt-editor-status ${kind}`; }
  }

  function updateSuiteControls() {
    const locked = !!lastState?.running || managementBusy || runStartPending;
    if (byId("benchSuiteManagement")) byId("benchSuiteManagement").disabled = locked;
    for (const id of ["benchSuiteSelect", "benchProfileSelect"]) if (byId(id)) byId(id).disabled = !!lastState?.running || runStartPending;
    if (byId("benchRunBtn")) byId("benchRunBtn").disabled = locked || !selectedSuite();
    const selected = selectedSuite();
    setText(byId("benchSuiteDescription"), selected ? `${selected.prompt_count} prompts / revision ${selected.revision} / ${selected.description || selected.name}` : "No active suites available. The operator must apply the Benchmarking v2 migration and seeds.");
    if (lastState?.running && !editing) suiteStatus("Suite management is locked while benchmarks are running.");
    else if (byId("benchSuiteEditorStatus")?.textContent === "Suite management is locked while benchmarks are running.") suiteStatus("");
  }

  async function loadSuites(preferredId = null) {
    const data = await benchGet("/suites");
    const previous = preferredId || byId("benchSuiteSelect").value;
    const historyPrevious = preferredId || byId("benchHistoryRevision").value;
    const managePrevious = preferredId || byId("benchManageSuite").value;
    suites = data.suites || [];
    categories = data.categories || [];
    const latest = new Map();
    suites.forEach(row => { if (!latest.has(row.suite_key) || Number(latest.get(row.suite_key).revision) < Number(row.revision)) latest.set(row.suite_key, row); });
    const active = [...latest.values()].filter(row => !row.is_archived);
    const option = row => `<option value="${row.id}">${escapeHtml(row.name)} / r${row.revision}${row.is_archived ? " (archived)" : ""}</option>`;
    const selection = byId("benchSuiteSelect");
    selection.innerHTML = active.map(option).join("") || '<option value="">No active suites</option>';
    selection.value = String(active.find(row => String(row.id) === String(previous))?.id || active.find(row => row.suite_key === "ce.core")?.id || active[0]?.id || "");
    for (const [id, previousId] of [["benchHistoryRevision", historyPrevious], ["benchManageSuite", managePrevious]]) {
      const el = byId(id); el.innerHTML = suites.map(option).join("");
      el.value = String(suites.find(row => String(row.id) === String(previousId))?.id || selection.value || suites[0]?.id || "");
    }
    const profile = byId("benchProfileSelect");
    const oldProfile = profile.value;
    profile.innerHTML = (data.profiles || []).map(row => `<option value="${escapeHtml(row.name)}">${escapeHtml(row.name)}</option>`).join("");
    if ((data.profiles || []).some(row => row.name === oldProfile)) profile.value = oldProfile;
    else if ((data.profiles || []).some(row => row.name === "Default v1")) profile.value = "Default v1";
    updateSuiteControls();
  }

  function blankPrompt() {
    return { prompt_text: "", category: categories[0] || "knowledge", max_tokens: 800, request_options_json: null };
  }

  function renderSuitePrompts(prompts) {
    byId("benchSuitePromptList").innerHTML = prompts.map((prompt, i) => `
      <details class="bench-suite-prompt" ${i === 0 ? "open" : ""}>
        <summary>Prompt ${i + 1}</summary>
        <div class="bench-suite-toolbar">
          <button type="button" data-prompt-move="${i}" data-direction="-1" ${i === 0 ? "disabled" : ""}>Move Up</button>
          <button type="button" data-prompt-move="${i}" data-direction="1" ${i === prompts.length - 1 ? "disabled" : ""}>Move Down</button>
          <button type="button" data-prompt-remove="${i}" ${prompts.length === 1 ? "disabled" : ""}>Remove</button>
        </div>
        <label>Prompt text<textarea class="bench-edit-text" rows="5">${escapeHtml(prompt.prompt_text || "")}</textarea></label>
        <div class="bench-suite-toolbar">
          <label>Category<select class="bench-edit-category">${categories.map(c => `<option value="${escapeHtml(c)}" ${c === prompt.category ? "selected" : ""}>${escapeHtml(c.replaceAll("_", " "))}</option>`).join("")}</select></label>
          <label>Token limit<input class="bench-edit-limit" type="number" min="1" max="32768" value="${escapeHtml(prompt.max_tokens || 800)}"></label>
        </div>
        <details class="bench-tool-options"><summary>Tool request options (advanced)</summary>
          <p>JSON: tools, tool_choice, parallel_tool_calls only. Calls are validated, never executed. Leave blank for text prompts.</p>
          <textarea class="bench-edit-tools" rows="7" aria-label="Tool request JSON for prompt ${i + 1}">${escapeHtml(prompt.request_options_json ? JSON.stringify(prompt.request_options_json, null, 2) : "")}</textarea>
        </details>
      </details>`).join("");
  }

  function collectSuitePrompts() {
    return [...byId("benchSuitePromptList").querySelectorAll(".bench-suite-prompt")].map((el, i) => {
      let options = null;
      const raw = el.querySelector(".bench-edit-tools").value;
      try { options = raw.trim() ? JSON.parse(raw) : null; }
      catch (_) { throw new Error(`Prompt ${i + 1}: tool options must be valid JSON.`); }
      return { ordering: i + 1, prompt_text: el.querySelector(".bench-edit-text").value,
        category: el.querySelector(".bench-edit-category").value, max_tokens: Number(el.querySelector(".bench-edit-limit").value), request_options_json: options };
    });
  }

  async function openSuiteEditor(mode) {
    if (lastState?.running || managementBusy) return;
    if (editing && !await showDecisionDialog({ title: "Replace unsaved edits", message: "Discard the current unsaved suite edits?", actionLabel: "Discard" })) return;
    let source = null;
    if (mode !== "create") source = (await benchGet(`/suites/${byId("benchManageSuite").value}`)).suite;
    const newer = source && suites.some(row => row.suite_key === source.suite_key && Number(row.revision) > Number(source.revision));
    const readonly = mode === "edit" && (source.is_archived || newer || (source.is_builtin && source.suite_key !== "ce.core"));
    editing = { mode, source_id: source?.id, readonly };
    byId("benchSuiteEditPanel").hidden = false;
    byId("benchSuiteFields").disabled = !!readonly;
    byId("benchSuiteSave").disabled = !!readonly;
    byId("benchSuiteSave").textContent = mode === "edit" ? "Save New Revision" : "Create Suite";
    byId("benchSuiteName").value = source ? source.name + (mode === "duplicate" ? " (copy)" : "") : "";
    byId("benchSuiteDescriptionInput").value = source?.description || "";
    renderSuitePrompts(source?.prompts || [blankPrompt()]);
    suiteStatus(readonly ? "Read-only revision. Duplicate it to make an editable custom suite." : "Saving creates a new revision. Previous prompts and results are preserved.");
    byId("benchSuiteManager").open = true;
    if (!readonly) byId("benchSuiteName").focus();
  }

  async function closeSuiteEditor() {
    if (editing && !editing.readonly && !await showDecisionDialog({ title: "Close suite editor", message: "Discard unsaved suite changes?", actionLabel: "Discard" })) return;
    editing = null; byId("benchSuiteEditPanel").hidden = true; suiteStatus("");
  }

  async function saveSuite() {
    if (!editing || editing.readonly || managementBusy) return;
    const payload = { ...editing, name: byId("benchSuiteName").value, description: byId("benchSuiteDescriptionInput").value, prompts: collectSuitePrompts() };
    managementBusy = true; updateSuiteControls();
    try {
      const result = await benchPost("/suites", payload);
      editing = null; byId("benchSuiteEditPanel").hidden = true;
      await loadSuites(result.suite_id);
      suiteStatus(result.message, "success");
      await refreshBestRuns(); await loadHistory();
    } catch (error) { suiteStatus(error.message, "error"); }
    finally { managementBusy = false; updateSuiteControls(); }
  }

  async function archiveSuite() {
    const suite = selectedSuite(byId("benchManageSuite").value);
    if (!suite || managementBusy) return;
    if (suite.is_builtin) throw new Error("Built-in suites cannot be archived.");
    if (!await showDecisionDialog({ title: "Archive suite", actionLabel: "Archive", message: `Remove "${suite.name}" from new runs? Its revisions and results will remain available.` })) return;
    managementBusy = true; updateSuiteControls();
    try {
      const result = await benchPost(`/suites/${suite.id}/archive`, {});
      editing = null; byId("benchSuiteEditPanel").hidden = true;
      await loadSuites(); suiteStatus(result.message, "success");
      await refreshBestRuns(); await loadHistory();
    } finally { managementBusy = false; updateSuiteControls(); }
  }

  async function loadHistory(append = false) {
    if (!byId("benchRunHistory")) return;
    const epoch = ++historyEpoch;
    const query = resultQuery() + (append && nextHistory ? `&before=${nextHistory}` : "");
    try {
      const data = await benchGet(`/runs?${query}`);
      if (epoch !== historyEpoch) return;
      const html = data.rows.map(row => `<button type="button" class="bench-details-btn bench-history-entry" data-run-id="${row.id}">#${row.id} / ${escapeHtml(row.model_name)} / ${escapeHtml(runLabel(row.status, row.fail_reason))} / ${escapeHtml(row.started_at || "")}${row.legacy ? " / legacy" : ""}</button>`).join("");
      if (append) byId("benchRunHistory").insertAdjacentHTML("beforeend", html);
      else byId("benchRunHistory").innerHTML = html || '<p>No model runs for this revision and profile.</p>';
      nextHistory = data.next_before;
      byId("benchHistoryMore").hidden = !nextHistory;
    } catch (error) { if (epoch === historyEpoch) setText(byId("benchRunHistory"), error.message); }
  }

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

  async function runBench() {
    if (runStartPending || lastState?.running) return;
    const suite = selectedSuite();
    if (!suite) throw new Error("Select an available suite first.");
    runStartPending = true; updateSuiteControls();
    try {
      await benchPost("/run", { profile: byId("benchProfileSelect").value, suite_id: suite.id, force: !!byId("benchForceCheckbox").checked });
      await pollTick(); ensurePolling(); await refreshBestRuns();
    } finally { runStartPending = false; updateSuiteControls(); }
  }

  let benchmarkStopPending = false;
  const pendingBenchmarkResets = new Set();

  async function stopBench() {
    if (benchmarkStopPending) return;
    if (!await showDecisionDialog({ title: "Stop benchmarks", actionLabel: "Stop Benchmarks",
      message: "Stop benchmarks now? This will cancel the current run." })) return;

    benchmarkStopPending = true;
    try {
      const r = await fetch(`${BENCH_PREFIX}/stop`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": window.CSRF_TOKEN },
        credentials: "same-origin",
        body: JSON.stringify({})
      });

      const { json: j } = await readJsonOrText(r);
      if (!r.ok || j.status !== "success") {
        await showCustomAlert(j.message || `Stop failed (HTTP ${r.status})`);
        return;
      }

      await pollTick();
    } finally {
      benchmarkStopPending = false;
    }
  }

  async function resetBenchmarkModel(modelName, fingerprint) {
    const fp = (fingerprint || "").trim().toLowerCase();
    if (pendingBenchmarkResets.has(fp)) return;
  
    if (!/^[0-9a-f]{40}$/.test(fp)) {
      await showCustomAlert("Reset failed: invalid fingerprint (must be 40 hex chars).");
      return;
    }
  
    const label = modelName ? `"${modelName}"` : "this model";
    if (!await showDecisionDialog({ title: "Clear benchmark history", actionLabel: "Clear History",
      message: `Clear ALL benchmark history for ${label}?` })) return;
  
    pendingBenchmarkResets.add(fp);
    try {
      const payload = { fingerprint: fp };
    
      const r = await fetch(`${BENCH_PREFIX}/reset_model`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": window.CSRF_TOKEN },
        credentials: "same-origin",
        body: JSON.stringify(payload)
      });
    
      const { json: j, text } = await readJsonOrText(r);
    
      if (!r.ok || j.status !== "success") {
        await showCustomAlert(j.message || `Reset failed (HTTP ${r.status})`);
        console.error("reset_model response:", { status: r.status, body: text });
        return;
      }
    
      const cleared = (j.cleared_runs !== undefined) ? Number(j.cleared_runs) : null;
      if (cleared !== null && Number.isFinite(cleared)) {
        await showCustomAlert(`${j.message || "Reset complete."}\nCleared runs: ${cleared}`);
      } else if (j.message) {
        await showCustomAlert(j.message);
      }
    
      await refreshBestRuns();
      await pollTick();
    } finally {
      pendingBenchmarkResets.delete(fp);
    }
  }

  async function refreshBestRuns() {
    const tableBody = pickEl(["benchBestTbody", "benchBestTableBody"]);
    const countEl = byId("benchResultsCount");
    if (!tableBody) return;

    setText(countEl, "Loading...");

    const epoch = ++bestEpoch;
    let j;
    try { j = await benchGet(`/best?${resultQuery()}`); }
    catch (error) {
      if (epoch === bestEpoch) { setText(countEl, "Unavailable"); tableBody.innerHTML = `<tr><td colspan="10">${escapeHtml(error.message)}</td></tr>`; }
      return;
    }
    if (epoch !== bestEpoch) return;

    const rows = j.rows || [];
    setText(countEl, `${rows.length} model${rows.length === 1 ? "" : "s"}`);
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
      const statusDisplay = status;
      const executionLabel = runLabel(status, row.fail_reason);
      const completionLabel = row.legacy_metrics ? "Legacy completion unknown" : `${row.completed_answers || 0}/${row.total_prompts} completed`;
      const incomplete = status === "PASS" && (row.legacy_metrics || Number(row.completed_answers) !== Number(row.total_prompts));
      const statusClass = (incomplete ? "stale" : statusDisplay.toLowerCase()).replace(/[^a-z0-9_-]/g, "-") || "unknown";

      const model = row.model_name || "";
      const modelDisplay = allowBench ? model : `${model} (benchmark disabled)`;

      const size = (row.size_gb !== undefined) ? row.size_gb : "";

      const avg = row.avg_eval_tps == null ? "N/A" : metric(row.avg_eval_tps);
      const min = row.min_eval_tps == null ? "N/A" : metric(row.min_eval_tps);
      const max = row.max_eval_tps == null ? "N/A" : metric(row.max_eval_tps);

      const tokens = row.total_tokens_generated == null ? "N/A" : metric(row.total_tokens_generated, 0);

      const totalMs = safeNum(row.total_ms, 0);
      const totalSec = row.total_ms == null ? "N/A" : (totalMs / 1000).toFixed(2) + "s";

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
        <td class="bench-status-cell bench-status-${statusClass}"><span>${escapeHtml(executionLabel)}</span><small>${escapeHtml(completionLabel)}</small></td>
        <td class="bench-model-cell" title="${escapeHtml(modelDisplay)}"><span class="bench-model-name">${escapeHtml(modelDisplay)}</span></td>
        <td class="bench-number">${escapeHtml(String(size))}</td>
        <td class="bench-number">${escapeHtml(String(avg))}</td>
        <td class="bench-number">${escapeHtml(`${min} / ${max}`)}</td>
        <td class="bench-number">${escapeHtml(String(tokens))}</td>
        <td class="bench-number">${escapeHtml(String(totalSec))}</td>
        <td class="bench-ended-cell">${escapeHtml(String(ended))}</td>
        <td class="bench-actions-cell">
          <div class="bench-actions">
            <button class="bench-details-btn"
                    data-run-id="${row.run_id}"
                    title="Saved model run details">
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
  function resultText(data, result = null) {
    const run = data.run;
    const value = v => v === null || v === undefined ? "Unavailable" : typeof v === "object" ? JSON.stringify(v, null, 2) : String(v);
    const parts = [
      `Model run #${run.id}`, `Model: ${value(run.model_name)} (registry ID ${run.model_id})`,
      `Path: ${value(run.model_path)}`, `Fingerprint: ${value(run.fingerprint)}`, `File size: ${value(run.file_size)}`,
      `Suite: ${run.promptset_name} / ${run.suite_key} / revision ${run.revision} / ${run.promptset_version}`,
      `Description: ${value(run.suite_description)}`, `Profile: ${run.profile_name}`,
      `Execution: ${runLabel(run.status, run.fail_reason)}`, `Run failure: ${value(run.fail_reason)}`,
      `Started: ${value(run.started_at)}`, `Ended: ${value(run.ended_at)}`, `Run configuration: ${value(run.context_json)}`,
    ];
    if (data.legacy_notice) parts.push(data.legacy_notice);
    for (const row of result ? [result] : data.results) {
      parts.push("", `Prompt ${row.ordering} (ID ${row.prompt_id}, result ${value(row.id)})`,
        `Category: ${row.category}`, `Configured / effective token limit: ${row.max_tokens} / ${value(row.effective_max_tokens)}`,
        `Request options: ${value(row.request_options_json)}`, "Prompt:", row.prompt_text,
        `Request execution: ${!row.recorded ? "No result recorded" : row.success ? "Succeeded" : "Failed"}`,
        `Completion: ${outcomeLabel(row.outcome)}`, `Finish reason: ${value(row.details_json?.finish_reason)}`,
        `Prompt tokens: ${value(row.tokens_prompt)}`, `Generated tokens: ${value(row.tokens_generated)}`,
        `TTFT (ms): ${row.legacy ? "Unavailable (legacy stored value: " + value(row.ttft_ms) + ")" : value(row.ttft_ms)}`,
        `Prompt evaluation TPS: ${row.legacy ? "Unavailable (legacy stored value: " + value(row.prompt_eval_tps) + ")" : value(row.prompt_eval_tps)}`,
        `${row.legacy ? "Legacy request throughput" : "Generation TPS"}: ${value(row.eval_tps)}`,
        `Request duration (ms): ${value(row.total_ms)}`, "Response:", row.response_text ?? "",
        "Reasoning:", value(row.details_json?.reasoning_content), "Tool calls (not executed):", value(row.details_json?.tool_calls),
        `Error: ${value(row.error)}`, `Completion and metric details: ${value(row.details_json)}`);
    }
    return parts.join("\n");
  }

  async function downloadExport(format, resultId = null) {
    if (!detailData) throw new Error("Open a model run first.");
    const runId = detailData.run.id;
    const path = `/run/${runId}${resultId === null ? "" : `/result/${resultId}`}/export?format=${format}`;
    const response = await fetch(`${BENCH_PREFIX}${path}`, { credentials: "same-origin", cache: "no-store" });
    const expected = format === "json" ? "application/json" : "text/csv";
    if (!response.ok || response.redirected || !(response.headers.get("Content-Type") || "").includes(expected)) {
      const parsed = await window.ApiHttp.readApiResponse(response);
      throw new Error(window.ApiHttp.getApiErrorMessage(parsed, "Export failed or session expired."));
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `benchmark-run-${runId}${resultId === null ? "" : `-result-${resultId}`}.${format}`;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function setRunActions(disabled) {
    for (const id of ["benchCopyRun", "benchExportRunJson", "benchExportRunCsv"]) if (byId(id)) byId(id).disabled = disabled;
  }

  async function loadRunDetails(runId) {
    const out = pickEl(["benchDetails", "benchRunDetails"]);
    const nav = byId("benchDetailNav");
    const epoch = ++detailEpoch;
    detailData = null; setRunActions(true);
    if (out) out.innerHTML = '<div class="bench-empty-details">Loading model run...</div>';
    if (nav) { nav.innerHTML = ""; nav.hidden = true; }
    let data;
    try { data = await benchGet(`/run/${runId}`); }
    catch (error) { if (epoch === detailEpoch) setText(out, error.message); return; }
    if (epoch !== detailEpoch) return;
    detailData = data; setRunActions(false);
    const run = data.run, results = data.results || [];
    renderRunDetailNavigation(runId, results);
    const pretty = value => escapeHtml(JSON.stringify(value, null, 2));
    const summary = `
      <div id="${runDetailAnchorId(runId, "summary")}" class="bench-run-summary">
        <h4>Model run #${run.id}</h4>
        <div class="bench-run-summary-grid">
          <div><b>Model:</b> ${escapeHtml(run.model_name || "")}</div>
          <div><b>Execution:</b> ${escapeHtml(runLabel(run.status, run.fail_reason))}</div>
          <div><b>Profile:</b> ${escapeHtml(run.profile_name)}</div>
          <div><b>Suite:</b> ${escapeHtml(run.promptset_name)} / revision ${run.revision}</div>
          <div><b>Started:</b> ${escapeHtml(run.started_at || "")}</div>
          <div><b>Ended:</b> ${escapeHtml(run.ended_at || "Not finalized")}</div>
        </div>
        <p>Execution success does not grade answer quality. Tool calls are never executed.</p>
        ${run.fail_reason ? `<p class="bench-prompt-error">Run detail: ${escapeHtml(run.fail_reason)}</p>` : ""}
        ${data.legacy_notice ? `<p class="bench-legacy-notice">${escapeHtml(data.legacy_notice)}</p>` : ""}
        <details><summary>Model identity and runtime configuration</summary><pre>${pretty({ model_id: run.model_id, model_name: run.model_name, model_path: run.model_path, fingerprint: run.fingerprint, file_size: run.file_size, suite_key: run.suite_key, revision: run.revision, context: run.context_json })}</pre></details>
      </div>`;
    const cards = results.map((row, index) => {
      const metrics = row.legacy ? `<span><b>Legacy request throughput:</b> ${metric(row.eval_tps)}</span>` : `<span><b>Generation TPS:</b> ${metric(row.eval_tps)}</span>`;
      const details = row.details_json;
      return `<div id="${runDetailAnchorId(runId, `prompt-${row.prompt_id}`)}" class="bench-prompt-card">
        <div class="bench-suite-toolbar"><h4>Prompt ${row.ordering}</h4>
          <button type="button" data-copy-result="${index}">Copy Result</button>
          <button type="button" data-export-result="${row.id || ""}" data-format="json" ${row.recorded ? "" : "disabled"}>JSON</button>
          <button type="button" data-export-result="${row.id || ""}" data-format="csv" ${row.recorded ? "" : "disabled"}>CSV</button>
        </div>
        <div class="bench-prompt-meta">
          <span><b>Request:</b> ${!row.recorded ? "No result recorded" : row.success ? "Succeeded" : "Failed"}</span>
          <span><b>Completion:</b> ${escapeHtml(outcomeLabel(row.outcome))}</span>
          ${metrics}<span><b>Generated tokens${row.legacy ? " (legacy)" : ""}:</b> ${metric(row.tokens_generated, 0)}</span>
          <span><b>Request time:</b> ${row.total_ms == null ? "Unavailable" : metric(row.total_ms / 1000) + "s"}</span>
        </div>
        <details><summary>Prompt and request settings</summary><pre>${escapeHtml(row.prompt_text)}</pre><pre>${pretty({ category: row.category, max_tokens: row.max_tokens, effective_max_tokens: row.effective_max_tokens, request_options: row.request_options_json })}</pre></details>
        <details class="collapsible-section bench-response-disclosure" open><summary>Generated response</summary>
          <div class="bench-response-bubble">${renderBenchmarkResponseHtml(row.response_text || "")}</div>
        </details>
        ${details?.reasoning_content ? `<details><summary>Reasoning output</summary><pre>${escapeHtml(typeof details.reasoning_content === "string" ? details.reasoning_content : JSON.stringify(details.reasoning_content, null, 2))}</pre></details>` : ""}
        ${details?.tool_calls ? `<details><summary>Tool calls (not executed)</summary><pre>${pretty(details.tool_calls)}</pre></details>` : ""}
        ${details?.tool_validation?.status === "invalid" ? `<p class="bench-prompt-error">Tool validation failed: ${escapeHtml(details.tool_validation.errors.join("; "))}</p>` : ""}
        <details><summary>Completion, token and timing details</summary><pre>${pretty({ prompt_tokens: row.tokens_prompt, generated_tokens: row.tokens_generated, ttft_ms: row.legacy ? null : row.ttft_ms, prompt_eval_tps: row.legacy ? null : row.prompt_eval_tps, legacy_measurements: row.legacy ? { ttft_ms: row.ttft_ms, prompt_eval_tps: row.prompt_eval_tps, eval_tps: row.eval_tps } : null, details })}</pre></details>
        ${row.error ? `<div class="bench-prompt-error">${escapeHtml(row.error)}</div>` : ""}
      </div>`;
    }).join("");
    if (out) { out.innerHTML = summary + cards; typesetBenchmarkMath(out); }
  }

  function wireButtons() {
    const on = (id, fn) => byId(id)?.addEventListener("click", action(fn));
    on("benchRunBtn", runBench);
    on("benchStopBtn", stopBench);
    on("benchBackToResultsBtn", returnToBenchmarkResults);
    on("benchRefreshBtn", async () => { await loadSuites(); await refreshBestRuns(); await loadHistory(); await pollTick(); });
    on("benchManageBtn", () => { byId("benchSuiteManager").open = !byId("benchSuiteManager").open; });
    on("benchSuiteNew", () => openSuiteEditor("create"));
    on("benchSuiteEdit", () => openSuiteEditor("edit"));
    on("benchSuiteDuplicate", () => openSuiteEditor("duplicate"));
    on("benchSuiteArchive", archiveSuite);
    on("benchSuiteSave", saveSuite);
    on("benchSuiteCancel", closeSuiteEditor);
    on("benchSuiteAddPrompt", () => {
      const prompts = collectSuitePrompts();
      if (prompts.length >= 100) throw new Error("A suite may contain at most 100 prompts.");
      renderSuitePrompts([...prompts, blankPrompt()]);
    });
    on("benchHistoryMore", () => loadHistory(true));
    on("benchCopyRun", event => { if (detailData) copyTextToClipboard(resultText(detailData), event.currentTarget, { normalizeCode: false }); });
    on("benchExportRunJson", () => downloadExport("json"));
    on("benchExportRunCsv", () => downloadExport("csv"));
    byId("benchSuiteSelect").addEventListener("change", action(async () => {
      byId("benchHistoryRevision").value = byId("benchSuiteSelect").value;
      byId("benchManageSuite").value = byId("benchSuiteSelect").value;
      updateSuiteControls(); await refreshBestRuns(); await loadHistory();
    }));
    for (const id of ["benchProfileSelect", "benchHistoryRevision"]) byId(id).addEventListener("change", action(async () => { await refreshBestRuns(); await loadHistory(); }));
    byId("benchSuitePromptList").addEventListener("click", action(event => {
      const button = event.target.closest("button");
      if (!button || !editing || editing.readonly || lastState?.running) return;
      const prompts = collectSuitePrompts();
      if (button.hasAttribute("data-prompt-remove")) prompts.splice(Number(button.dataset.promptRemove), 1);
      else if (button.hasAttribute("data-prompt-move")) {
        const from = Number(button.dataset.promptMove), to = from + Number(button.dataset.direction);
        if (to < 0 || to >= prompts.length) return;
        [prompts[from], prompts[to]] = [prompts[to], prompts[from]];
      } else return;
      renderSuitePrompts(prompts);
    }));
    byId("benchmarksDrawer").addEventListener("click", action(async event => {
      const button = event.target.closest("button");
      if (!button) return;
      if (button.classList.contains("bench-details-btn")) {
        lastDetailsTrigger = button; setBenchmarkView("details"); byId("benchBackToResultsBtn").focus();
        await loadRunDetails(button.dataset.runId);
      } else if (button.classList.contains("bench-reset-btn")) {
        await resetBenchmarkModel(button.dataset.modelName || "", button.dataset.fingerprint || "");
        await loadHistory();
      } else if (button.hasAttribute("data-copy-result") && detailData) {
        copyTextToClipboard(resultText(detailData, detailData.results[Number(button.dataset.copyResult)]), button, { normalizeCode: false });
      } else if (button.hasAttribute("data-export-result")) {
        await downloadExport(button.dataset.format, button.dataset.exportResult);
      }
    }));
  }

  async function init() {
    if (!byId("benchSuiteSelect")) return;
    setBenchmarkView("results"); setRunActions(true); wireButtons();
    try { await loadSuites(); await refreshBestRuns(); await loadHistory(); }
    catch (error) { setText(byId("benchSuiteDescription"), error.message); suiteStatus(error.message, "error"); byId("benchRunBtn").disabled = true; }
    await pollTick();
    if (lastState?.running) ensurePolling();
    wireTableSorting();
  }

  document.addEventListener("DOMContentLoaded", init);
  window.fetchBenchStatus = pollTick;
  window.refreshBestRuns = refreshBestRuns;
})();
