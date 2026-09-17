(function () {
    "use strict";

    function escapeHtml(value) {
      if (typeof window.escapeHtml === "function") {
        return window.escapeHtml(String(value ?? ""));
      }
      return String(value ?? "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\"": "&quot;",
        "'": "&#39;"
      }[ch]));
    }

    function asArray(value) {
      return Array.isArray(value) ? value : [];
    }

    function fetchChatAnalytics() {
      const prefix = (typeof window.ANALYTICS_PREFIX === "string"
        ? window.ANALYTICS_PREFIX
        : (typeof ANALYTICS_PREFIX === "string" ? ANALYTICS_PREFIX : "/analytics"));
  
        fetch(prefix, { cache: "no-store", credentials: "same-origin" })
        .then(response => response.json())
        .then(data => {
          data = data && typeof data === "object" ? data : {};
          let models = {};
  
          asArray(data.total_requests).forEach(item => {
            let model = item.model || "Unknown";
            models[model] = models[model] || {};
            models[model].total_requests = item.total_requests;
          });
  
          asArray(data.avg_response).forEach(item => {
            let model = item.model || "Unknown";
            models[model] = models[model] || {};
            models[model].avg_response_time = window.formatTime(parseFloat(item.avg_response_time));
          });
  
          asArray(data.tps_metrics).forEach(item => {
            let model = item.model || "Unknown";
            models[model] = models[model] || {};
            const minTps = parseFloat(item.min_tps);
            models[model].min_tps = isNaN(minTps) ? "0" : Math.floor(minTps).toString();
            const maxTps = parseFloat(item.max_tps);
            models[model].max_tps = isNaN(maxTps) ? "0" : Math.floor(maxTps).toString();
            const avgTps = parseFloat(item.avg_tps);
            models[model].avg_tps = isNaN(avgTps) ? "0" :  Math.floor(avgTps).toString();
          });
  
          asArray(data.tokens_sum).forEach(item => {
            let model = item.model || "Unknown";
            models[model] = models[model] || {};
            models[model].total_tokens = item.total_tokens;
          });
  
          const totalTokensPlaceholder = "__ANALYTICS_TOTAL_TOKENS__";
          let html = `<div class='analytics-table-wrap' role='region' aria-label='Analytics by model' tabindex='0'><div class='analytics-total-summary'><span>Total Tokens</span><strong>${totalTokensPlaceholder}</strong></div><table id='analyticsTable' class='analyticsTable'><colgroup><col class='analytics-model-col'><col><col><col><col><col><col class='analytics-viz-col'><col></colgroup><thead><tr>`;
          html += `<th scope="col" class="analytics-sortable" data-title="Model" aria-label="Sort by model" onclick="sortTable('analyticsTable', 0, false)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();sortTable('analyticsTable', 0, false)}" role="button" tabindex="0">Model</th>`;
          html += `<th scope="col" class="analytics-sortable analytics-number" data-title="Total Sent" aria-label="Sort by total sent" onclick="sortTable('analyticsTable', 1, true)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();sortTable('analyticsTable', 1, true)}" role="button" tabindex="0">Total Sent</th>`;
          html += `<th scope="col" class="analytics-sortable analytics-number" data-title="Avg Time" aria-label="Sort by average response time" onclick="sortTable('analyticsTable', 2, false)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();sortTable('analyticsTable', 2, false)}" role="button" tabindex="0">Avg Time</th>`;
          html += `<th scope="col" class="analytics-sortable analytics-number" data-title="Min TPS" aria-label="Sort by minimum tokens per second" onclick="sortTable('analyticsTable', 3, true)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();sortTable('analyticsTable', 3, true)}" role="button" tabindex="0">Min TPS</th>`;
          html += `<th scope="col" class="analytics-sortable analytics-number" data-title="Max TPS" aria-label="Sort by maximum tokens per second" onclick="sortTable('analyticsTable', 4, true)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();sortTable('analyticsTable', 4, true)}" role="button" tabindex="0">Max TPS</th>`;
          html += `<th scope="col" class="analytics-sortable analytics-number" data-title="Avg TPS" aria-label="Sort by average tokens per second" onclick="sortTable('analyticsTable', 5, true)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();sortTable('analyticsTable', 5, true)}" role="button" tabindex="0">Avg TPS</th>`;
          html += `<th scope="col" class="analytics-sortable analytics-viz-heading" data-title="TPS Viz" aria-label="Sort by tokens per second visualization" onclick="sortTable('analyticsTable', 6, true)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();sortTable('analyticsTable', 6, true)}" role="button" tabindex="0">TPS Viz</th>`;
          html += `<th scope="col" class="analytics-sortable analytics-number" data-title="# Tokens" aria-label="Sort by total tokens" onclick="sortTable('analyticsTable', 7, true)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();sortTable('analyticsTable', 7, true)}" role="button" tabindex="0"># Tokens</th>`;
          html += "</tr></thead><tbody>";
  
          let overallTokens = 0;
          
          for (let model in models) {

            if (
              typeof models[model].total_requests === "undefined" ||
              typeof models[model].total_tokens === "undefined"
            ) {
              continue;
            }

            let maxAvgTps = 0;
            for (let m in models) {
            const v = parseFloat(models[m]?.avg_tps);
            if (!isNaN(v) && v > maxAvgTps) maxAvgTps = v;
            }
            if (maxAvgTps <= 0) maxAvgTps = 1;

            const modelName = escapeHtml(prettifyModelName(model));
            html += "<tr>";
            html += `<td class="analytics-model-cell" title="${modelName}"><span class="analytics-model-name">${modelName}</span></td>`;
            html += `<td class="analytics-number">${models[model].total_requests.toLocaleString()}</td>`;
            html += `<td class="analytics-number">${models[model].avg_response_time}</td>`;
            html += `<td class="analytics-number">${models[model].min_tps}</td>`;
            html += `<td class="analytics-number">${models[model].max_tps}</td>`;
            const avg = parseFloat(models[model].avg_tps) || 0;
            const pct = Math.max(2, Math.min(100, (avg / maxAvgTps) * 100));
            
            html += `<td class="analytics-number tpsval">${models[model].avg_tps}</td>`;
            html += `<td class="analytics-tps-viz" data-sort="${avg.toFixed(6)}"><div class="tpsbar" title="Avg TPS: ${avg.toFixed(2)}" aria-label="Average ${avg.toFixed(2)} tokens per second" role="img"><i style="width:${pct}%;"></i></div></td>`;
            html += `<td class="analytics-number">${models[model].total_tokens.toLocaleString()}</td>`;
            
            html += "</tr>";
  
            let tokens = parseFloat(models[model].total_tokens);
            if (!isNaN(tokens)) overallTokens += tokens;
          }
  
          html += "</tbody></table></div>";
          html = html.replace(totalTokensPlaceholder, overallTokens.toLocaleString());
  
          const dash = document.getElementById("analyticsDashboard");
          if (dash) dash.innerHTML = html;
  
          if (document.getElementById("analyticsTable")) window.sortTable('analyticsTable', 4, true, true);
  
        })
        .catch(error => {
          console.error("Error fetching analytics:", error);
          const dash = document.getElementById("analyticsDashboard");
          if (dash) dash.innerHTML = "<p>Error loading analytics.</p>";
        });
    }

    function formatKnownInteger(value) {
      if (value === null || typeof value === "undefined" || value === "") return "—";
      const number = Number(value);
      return Number.isFinite(number) ? Math.trunc(number).toLocaleString() : "—";
    }

    function formatKnownDuration(value) {
      if (value === null || typeof value === "undefined" || value === "") return "—";
      const seconds = Number(value);
      if (!Number.isFinite(seconds) || seconds < 0) return "—";
      return typeof window.formatTime === "function" ? window.formatTime(seconds) : `${seconds.toFixed(2)}s`;
    }

    function formatEventTime(value) {
      const timestamp = Number(value);
      if (!Number.isFinite(timestamp) || timestamp <= 0) return "—";
      const date = new Date(timestamp * 1000);
      return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
    }

    function humanizeStatus(value) {
      const text = String(value || "unknown").trim().replace(/[_-]+/g, " ");
      return text.replace(/\b\w/g, character => character.toUpperCase());
    }

    function apiEndpointCell(event) {
      const endpoint = String(event.endpoint || "").trim();
      const knownEndpoints = {
        "/v1/models": { label: "Models", method: "GET" },
        "/v1/chat/completions": { label: "Chat Completions", method: "POST" }
      };
      const details = knownEndpoints[endpoint] || { label: endpoint || "Unknown", method: "" };
      const metadata = [details.method, endpoint].filter(Boolean).join(" ");
      const streaming = event.streaming ? `<span class="analytics-inline-badge">Streaming</span>` : "";
      return `<span class="api-analytics-primary">${escapeHtml(details.label)}</span><span class="api-analytics-secondary">${escapeHtml(metadata)}${streaming}</span>`;
    }

    function apiModelCell(event) {
      const requested = String(event.requested_model || "").trim();
      const active = String(event.active_model || "").trim();
      const primary = requested || active;
      if (!primary) return "—";

      let html = `<span class="api-analytics-primary" title="${escapeHtml(primary)}">${escapeHtml(prettifyModelName(primary))}</span>`;
      if (requested && active && requested !== active) {
        html += `<span class="api-analytics-secondary" title="${escapeHtml(active)}">Active: ${escapeHtml(prettifyModelName(active))}</span>`;
      }
      return html;
    }

    function apiResultCell(event) {
      const status = String(event.status || "unknown").trim().toLowerCase();
      const hasHttpStatus = event.http_status !== null && typeof event.http_status !== "undefined" && event.http_status !== "";
      const httpStatus = hasHttpStatus ? Number(event.http_status) : null;
      const isSuccess = status === "completed" && (!Number.isFinite(httpStatus) || httpStatus < 400);
      const result = `${humanizeStatus(status)}${Number.isFinite(httpStatus) ? ` · ${httpStatus}` : ""}`;
      return `<span class="analytics-status-badge ${isSuccess ? "is-success" : "is-error"}">${escapeHtml(result)}</span>`;
    }

    function renderApiAnalytics(data) {
      const dashboard = document.getElementById("apiAnalyticsDashboard");
      if (!dashboard) return;

      const summary = data && typeof data.summary === "object" ? data.summary : {};
      const recent = asArray(data?.recent);
      const page = Number(data?.pagination?.page) || 1;
      const totalPages = Number(data?.pagination?.total_pages) || 1;
      let html = `<div class="api-analytics-summary" aria-label="API analytics summary">`;
      html += `<div><span>API Requests</span><strong>${formatKnownInteger(summary.api_requests ?? 0)}</strong></div>`;
      html += `<div><span>Completion Requests</span><strong>${formatKnownInteger(summary.completion_requests ?? 0)}</strong></div>`;
      html += `<div><span>Errors</span><strong>${formatKnownInteger(summary.errors ?? 0)}</strong></div>`;
      html += `<div><span>Recorded Tokens</span><strong>${formatKnownInteger(summary.recorded_tokens)}</strong></div>`;
      html += `<div><span>Avg Completion Time</span><strong>${formatKnownDuration(summary.avg_completion_time)}</strong></div>`;
      html += `</div>`;
      html += `<div class="analytics-table-wrap" role="region" aria-label="Recent API activity" tabindex="0"><table id="apiAnalyticsTable" class="analyticsTable api-analytics-table responsive-table"><colgroup><col class="api-time-col"><col class="api-endpoint-col"><col class="api-model-col"><col class="api-result-col"><col class="api-duration-col"><col class="api-tokens-col"></colgroup><thead><tr><th scope="col">Time</th><th scope="col">Endpoint</th><th scope="col">Model</th><th scope="col">Result</th><th scope="col" class="analytics-number">Duration</th><th scope="col" class="analytics-number">Tokens</th></tr></thead><tbody>`;

      if (!recent.length) {
        html += `<tr><td colspan="6" class="api-analytics-empty">No API request activity has been recorded.</td></tr>`;
      } else {
        recent.forEach(event => {
          html += "<tr>";
          html += `<td data-label="Time" class="api-analytics-time">${escapeHtml(formatEventTime(event.timestamp))}</td>`;
          html += `<td data-label="Endpoint">${apiEndpointCell(event)}</td>`;
          html += `<td data-label="Model">${apiModelCell(event)}</td>`;
          html += `<td data-label="Result">${apiResultCell(event)}</td>`;
          html += `<td data-label="Duration" class="analytics-number">${formatKnownDuration(event.duration)}</td>`;
          html += `<td data-label="Tokens" class="analytics-number">${formatKnownInteger(event.total_tokens)}</td>`;
          html += "</tr>";
        });
      }

      html += "</tbody></table></div>";
      if (totalPages > 1) {
        html += `<nav class="api-analytics-pagination" aria-label="API request history pages">`;
        html += `<button type="button" data-api-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>Previous</button>`;
        html += `<span>Page ${page.toLocaleString()} / ${totalPages.toLocaleString()}</span>`;
        html += `<button type="button" data-api-page="${page + 1}" ${page >= totalPages ? "disabled" : ""}>Next</button>`;
        html += `</nav>`;
      }
      dashboard.innerHTML = html;
      dashboard.querySelectorAll("[data-api-page]").forEach(button => {
        button.addEventListener("click", () => fetchApiAnalytics(Number(button.dataset.apiPage)));
      });
    }

    function fetchApiAnalytics(page = 1) {
      const prefix = (typeof window.ANALYTICS_PREFIX === "string"
        ? window.ANALYTICS_PREFIX
        : (typeof ANALYTICS_PREFIX === "string" ? ANALYTICS_PREFIX : "/analytics"));
      const dashboard = document.getElementById("apiAnalyticsDashboard");
      if (!dashboard) return;

      dashboard.innerHTML = "Loading API analytics...";
      fetch(`${prefix}/api?page=${encodeURIComponent(page)}`, { cache: "no-store", credentials: "same-origin" })
        .then(async response => {
          const data = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(data.message || data.error || `HTTP ${response.status}`);
          return data;
        })
        .then(renderApiAnalytics)
        .catch(error => {
          console.error("Error fetching API analytics:", error);
          dashboard.innerHTML = "<p>Error loading API analytics.</p>";
        });
    }

    function selectedAnalyticsTab() {
      return document.getElementById("analyticsApiTab")?.getAttribute("aria-selected") === "true" ? "api" : "chat";
    }

    function fetchAnalytics() {
      if (selectedAnalyticsTab() === "api") {
        fetchApiAnalytics();
      } else {
        fetchChatAnalytics();
      }
    }

    function selectAnalyticsTab(name, focusTab = false) {
      const tabs = Array.from(document.querySelectorAll("[data-analytics-tab]"));
      if (!tabs.length) return;

      tabs.forEach(tab => {
        const selected = tab.dataset.analyticsTab === name;
        tab.classList.toggle("active", selected);
        tab.setAttribute("aria-selected", selected ? "true" : "false");
        tab.tabIndex = selected ? 0 : -1;
        if (selected && focusTab) tab.focus();
      });

      const chatPanel = document.getElementById("analyticsChatPanel");
      const apiPanel = document.getElementById("analyticsApiPanel");
      if (chatPanel) chatPanel.hidden = name !== "chat";
      if (apiPanel) apiPanel.hidden = name !== "api";
      fetchAnalytics();
    }

    function initializeAnalyticsTabs() {
      const tabs = Array.from(document.querySelectorAll("[data-analytics-tab]"));
      tabs.forEach((tab, index) => {
        tab.addEventListener("click", () => selectAnalyticsTab(tab.dataset.analyticsTab));
        tab.addEventListener("keydown", event => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          let targetIndex = index;
          if (event.key === "Home") targetIndex = 0;
          if (event.key === "End") targetIndex = tabs.length - 1;
          if (event.key === "ArrowLeft") targetIndex = (index - 1 + tabs.length) % tabs.length;
          if (event.key === "ArrowRight") targetIndex = (index + 1) % tabs.length;
          selectAnalyticsTab(tabs[targetIndex].dataset.analyticsTab, true);
        });
      });
    }

    function prettifyModelName(name) {
      const raw = String(name || "");
      const base = raw.split(/[\\/]/).pop(); // handles Windows + Linux paths
      return base.replace(/\.(gguf|bin|pth|ckpt|safetensors)$/i, "");
    }
    function sortTable(tableId, colIndex, numeric, initial = false) {
        const table = document.getElementById(tableId);
        if (!table) return;
      
        const tbody = table.getElementsByTagName("tbody")[0];
        if (!tbody) return;
      
        const headers = table.getElementsByTagName("th");
      
        for (let i = 0; i < headers.length; i++) {
          headers[i].textContent = headers[i].getAttribute("data-title");
          headers[i].setAttribute("aria-sort", "none");
          headers[i].removeAttribute("data-sort-active");
        }
      
        const header = headers[colIndex];
        let sortDir = header.getAttribute("data-sort-dir");
        if (!sortDir) {
          sortDir = (initial && colIndex === 4) ? "desc" : "asc";
        } else {
          sortDir = sortDir === "asc" ? "desc" : "asc";
        }
      
        header.setAttribute("data-sort-dir", sortDir);
        header.setAttribute("data-sort-active", "true");
        header.setAttribute("aria-sort", sortDir === "asc" ? "ascending" : "descending");
      
        const allRows = Array.from(tbody.getElementsByTagName("tr"));
        const dataRows = allRows;
      
        dataRows.sort((a, b) => {
          const aCells = a.getElementsByTagName("td");
          const bCells = b.getElementsByTagName("td");
          if (aCells.length <= colIndex || bCells.length <= colIndex) return 0;
      
          const aCell = aCells[colIndex];
          const bCell = bCells[colIndex];
      
          const aSort = aCell.getAttribute("data-sort");
          const bSort = bCell.getAttribute("data-sort");
          if (aSort !== null || bSort !== null) {
            const av = parseFloat((aSort || "0").replace(/,/g, "")) || 0;
            const bv = parseFloat((bSort || "0").replace(/,/g, "")) || 0;
            if (av < bv) return sortDir === "asc" ? -1 : 1;
            if (av > bv) return sortDir === "asc" ? 1 : -1;
            return 0;
          }
      
          let aText = (aCell.innerText || "").trim();
          let bText = (bCell.innerText || "").trim();
      
          if (colIndex === 2) {
            const parseTimeToSeconds = (text) => {
              let total = 0;
              const m = text.match(/(\d+)\s*m/);
              if (m) total += parseInt(m[1], 10) * 60;
              const s = text.match(/(\d+(?:\.\d+)?)\s*s/);
              if (s) total += parseFloat(s[1]);
              return total;
            };
            aText = parseTimeToSeconds(aText);
            bText = parseTimeToSeconds(bText);
          } else if (numeric) {
            aText = parseFloat(aText.replace(/,/g, "")) || 0;
            bText = parseFloat(bText.replace(/,/g, "")) || 0;
          } else {
            aText = aText.toLowerCase();
            bText = bText.toLowerCase();
          }
      
          if (aText < bText) return sortDir === "asc" ? -1 : 1;
          if (aText > bText) return sortDir === "asc" ? 1 : -1;
          return 0;
        });
      
        dataRows.forEach(row => tbody.appendChild(row));
      }
  
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", initializeAnalyticsTabs);
    } else {
      initializeAnalyticsTabs();
    }

    window.fetchAnalytics = fetchAnalytics;
    window.sortTable = sortTable;
  
    window.prettifyModelName = prettifyModelName;
  })();
  
