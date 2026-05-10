/* analytics_ui.js
   Analytics drawer UI: fetch, table render/sort.
   Depends on:
     - window.ANALYTICS_PREFIX (or const ANALYTICS_PREFIX in global scope)
     - window.formatTime(seconds) (from _utils.js)
*/

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
            // stored as formatted string for display
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
  
          // ---- TABLE (MUST STAY) ----
          let html = "<div align='center'><table id='analyticsTable' cellpadding='5' cellspacing='0' class='analyticsTable'><thead><tr>";
          html += `<th data-title="Model" onclick="sortTable('analyticsTable', 0, false)">Model</th>`;
          html += `<th data-title="Total Sent" onclick="sortTable('analyticsTable', 1, true)">Total Sent</th>`;
          html += `<th data-title="Avg Time" onclick="sortTable('analyticsTable', 2, false)">Avg Time</th>`;
          html += `<th data-title="Min TPS" onclick="sortTable('analyticsTable', 3, true)">Min TPS</th>`;
          html += `<th data-title="Max TPS" onclick="sortTable('analyticsTable', 4, true)">Max TPS</th>`;
          html += `<th data-title="Avg TPS" onclick="sortTable('analyticsTable', 5, true)">Avg TPS</th>`;
          html += `<th data-title="TPS Viz" onclick="sortTable('analyticsTable', 6, true)">TPS Viz</th>`;
          html += `<th data-title="# Tokens" onclick="sortTable('analyticsTable', 7, true)"># Tokens</th>`;
          html += "</tr></thead><tbody>";
  
          let overallTokens = 0;
          
          for (let model in models) {
            // Only require these two fields to render a row.
            // Missing TPS/avg time should not delete the entire model from analytics.
            if (
              typeof models[model].total_requests === "undefined" ||
              typeof models[model].total_tokens === "undefined"
            ) {
              continue;
            }

            // Find max Avg TPS for bar scaling
            let maxAvgTps = 0;
            for (let m in models) {
            const v = parseFloat(models[m]?.avg_tps);
            if (!isNaN(v) && v > maxAvgTps) maxAvgTps = v;
            }
            if (maxAvgTps <= 0) maxAvgTps = 1;

            html += "<tr>";
            html += `<td>${escapeHtml(prettifyModelName(model))}</td>`;
            html += `<td>${models[model].total_requests.toLocaleString()}</td>`;
            html += `<td>${models[model].avg_response_time}</td>`;
            html += `<td>${models[model].min_tps}</td>`;
            html += `<td>${models[model].max_tps}</td>`;
            const avg = parseFloat(models[model].avg_tps) || 0;
            const pct = Math.max(2, Math.min(100, (avg / maxAvgTps) * 100));
            
            html += `<td class="tpsval">${models[model].avg_tps}</td>`;
            html += `<td data-sort="${avg.toFixed(6)}"><div class="tpsbar" title="Avg TPS: ${avg.toFixed(2)}"><i style="width:${pct}%;"></i></div></td>`;
            html += `<td>${models[model].total_tokens.toLocaleString()}</td>`;
            
            html += "</tr>";
  
            let tokens = parseFloat(models[model].total_tokens);
            if (!isNaN(tokens)) overallTokens += tokens;
          }
  
          html += `<tr><td colspan="7" style="text-align:right;"><strong>Total Tokens: </strong></td><td><strong> ${overallTokens.toLocaleString()}</strong></td></tr>`;
          html += "</tbody></table></div>";
  
          const dash = document.getElementById("analyticsDashboard");
          if (dash) dash.innerHTML = html;
  
          // Default sort (preserved)
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
      
        // Reset header labels (remove arrows)
        for (let i = 0; i < headers.length; i++) {
          headers[i].innerHTML = headers[i].getAttribute("data-title");
        }
      
        const header = headers[colIndex];
        let sortDir = header.getAttribute("data-sort-dir");
        if (!sortDir) {
          sortDir = (initial && colIndex === 4) ? "desc" : "asc";
        } else {
          sortDir = sortDir === "asc" ? "desc" : "asc";
        }
      
        header.setAttribute("data-sort-dir", sortDir);
        const arrow = sortDir === "asc" ? " ▲" : " ▼";
        header.innerHTML = header.getAttribute("data-title") + arrow;
      
        // Split normal rows vs "summary" rows (e.g., Total Tokens row)
        const allRows = Array.from(tbody.getElementsByTagName("tr"));
        const dataRows = [];
        const tailRows = [];
      
        for (const r of allRows) {
          const tdCount = r.getElementsByTagName("td").length;
          // Summary rows usually have fewer cells because of colspan,
          // and/or contain <strong>Total Tokens:</strong>
          const isSummary =
            tdCount !== headers.length ||
            (r.innerText && r.innerText.toLowerCase().includes("total tokens"));
      
          (isSummary ? tailRows : dataRows).push(r);
        }
      
        dataRows.sort((a, b) => {
          const aCells = a.getElementsByTagName("td");
          const bCells = b.getElementsByTagName("td");
          if (aCells.length <= colIndex || bCells.length <= colIndex) return 0;
      
          const aCell = aCells[colIndex];
          const bCell = bCells[colIndex];
      
          // NEW: if data-sort exists, use it (perfect for TPS Viz)
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
      
          // Special case: Avg Time column (index 2)
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
            // Case-insensitive text compare for stability
            aText = aText.toLowerCase();
            bText = bText.toLowerCase();
          }
      
          if (aText < bText) return sortDir === "asc" ? -1 : 1;
          if (aText > bText) return sortDir === "asc" ? 1 : -1;
          return 0;
        });
      
        // Rebuild tbody: sorted data rows + summary rows at the end
        dataRows.forEach(row => tbody.appendChild(row));
        tailRows.forEach(row => tbody.appendChild(row));
      }
  
    // Expose globals so existing onclick handlers and toggleDrawer keep working
    window.fetchAnalytics = fetchAnalytics;
    window.sortTable = sortTable;
  
    // Optional export
    window.prettifyModelName = prettifyModelName;
  })();
  
