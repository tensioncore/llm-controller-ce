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
  
    function fetchAnalytics() {
      const prefix = (typeof window.ANALYTICS_PREFIX === "string"
        ? window.ANALYTICS_PREFIX
        : (typeof ANALYTICS_PREFIX === "string" ? ANALYTICS_PREFIX : "/analytics"));
  
        fetch(prefix, { cache: "no-store", credentials: "same-origin" })
        .then(response => response.json())
        .then(data => {
          let models = {};
  
          data.total_requests.forEach(item => {
            let model = item.model || "Unknown";
            models[model] = models[model] || {};
            models[model].total_requests = item.total_requests;
          });
  
          data.avg_response.forEach(item => {
            let model = item.model || "Unknown";
            models[model] = models[model] || {};
            models[model].avg_response_time = window.formatTime(parseFloat(item.avg_response_time));
          });
  
          data.tps_metrics.forEach(item => {
            let model = item.model || "Unknown";
            models[model] = models[model] || {};
            const minTps = parseFloat(item.min_tps);
            models[model].min_tps = isNaN(minTps) ? "0" : Math.floor(minTps).toString();
            const maxTps = parseFloat(item.max_tps);
            models[model].max_tps = isNaN(maxTps) ? "0" : Math.floor(maxTps).toString();
            const avgTps = parseFloat(item.avg_tps);
            models[model].avg_tps = isNaN(avgTps) ? "0" :  Math.floor(avgTps).toString();
          });
  
          data.tokens_sum.forEach(item => {
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
  
          window.sortTable('analyticsTable', 4, true, true);
  
        })
        .catch(error => {
          console.error("Error fetching analytics:", error);
          const dash = document.getElementById("analyticsDashboard");
          if (dash) dash.innerHTML = "<p>Error loading analytics.</p>";
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
  
    window.fetchAnalytics = fetchAnalytics;
    window.sortTable = sortTable;
  
    window.prettifyModelName = prettifyModelName;
  })();
  
