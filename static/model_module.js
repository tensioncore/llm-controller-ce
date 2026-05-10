/* /model_module.js
   Model dropdown + sorting + drawer auto-open behavior.
   Adds registry merge (is_enabled/is_favorite) so dropdown can hide disabled and float favorites.
*/

(function () {
  // Keep same globals as before (other code expects these names)
  window.models = window.models || [];
  window.currentModelSort = window.currentModelSort || { by: "name", dir: "asc" };
  window.selectedModelValue = window.selectedModelValue || null;

  function asNum(v, def = 0) {
    const n = Number(v);
    return Number.isFinite(n) ? n : def;
  }

  function yn(v) {
    if (v === true || v === false) return v;
    if (typeof v === "number") return v !== 0;
    const s = String(v ?? "").toLowerCase().trim();
    if (s === "1" || s === "true" || s === "yes") return true;
    if (s === "0" || s === "false" || s === "no") return false;
    return false;
  }

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

  function normKey(s) {
    // Normalize into a stable lookup key:
    // - basename
    // - strip .gguf
    // - lowercase
    if (!s) return "";
    const str = String(s).trim();
    const base = str.split(/[\\/]/).pop() || str;
    const noext = base.replace(/\.gguf$/i, "");
    return noext.toLowerCase();
  }

  async function fetchJsonOrThrow(url) {
    const res = await fetch(url, { credentials: "same-origin", cache: "no-store" });
    const ct = (res.headers.get("content-type") || "").toLowerCase();
    if (!ct.includes("application/json")) {
      const txt = await res.text().catch(() => "");
      throw new Error(`${url} did not return JSON. HTTP ${res.status}. ${txt.slice(0, 160)}`);
    }
    const data = await res.json();
    if (!res.ok) throw new Error(`${url} HTTP ${res.status}`);
    return data;
  }

  async function loadRegistryMap() {
    // Registry list is admin-only; if it 401/403 we just continue without registry features.
    try {
      const data = await fetchJsonOrThrow("/model/registry/list");
      if (!data || data.status !== "success" || !Array.isArray(data.models)) return null;

      const map = new Map();
      for (const r of data.models) {
        // registry has model_name and model_path
        const k1 = normKey(r.model_name);
        const k2 = normKey(r.model_path);
        if (k1) map.set(k1, r);
        if (k2) map.set(k2, r);
      }
      return map;
    } catch (e) {
      // Silent fallback: dropdown still works, just no registry filtering
      console.warn("[model_module] registry/list unavailable (ok for non-admin):", e.message || e);
      return null;
    }
  }

  function mergeRegistryFields(models, regMap) {
    if (!regMap) return models;

    for (const m of models) {
      // Try matching by value, name, etc.
      const kValue = normKey(m.value);
      const kName = normKey(m.name);
      const row = regMap.get(kValue) || regMap.get(kName);

      if (row) {
        m.is_enabled = row.is_enabled;
        m.is_favorite = row.is_favorite;
      }
    }
    return models;
  }

  // Fetch models and sort on load
  window.loadModelDropdown = function loadModelDropdown() {
    (async () => {
      try {
        // Primary list (already filtered by backend)
        const data = await fetchJsonOrThrow("/model/registry/dropdown");
        let list = (data && Array.isArray(data.models)) ? data.models : [];

        // Optional: enrich with registry flags if available (won't break for non-admin)
        const regMap = await loadRegistryMap();
        list = mergeRegistryFields(list, regMap);

        // Hide disabled ONLY when registry is available; otherwise treat as enabled
        if (regMap) {
          list = list.filter(m => yn(m.is_enabled) !== false);
        }

        // Keep current selection if still present; otherwise clear selection
        if (window.selectedModelValue) {
          const stillThere = list.some(m => m.value === window.selectedModelValue);
          if (!stillThere) window.selectedModelValue = null;
        }

        window.models = list;

        // Ensure default sort runs so favorites float immediately
        window.sortModels(window.currentModelSort.by, window.currentModelSort.dir === "asc");
        window.renderModelDropdown();
      } catch (err) {
        console.error("[model_module] loadModelDropdown failed:", err);
      }
    })();
  };

  // Sorting utility
  window.sortModels = function sortModels(by, asc) {
    window.models.sort((a, b) => {
      // Favorites float to top
      const favA = yn(a.is_favorite) ? 1 : 0;
      const favB = yn(b.is_favorite) ? 1 : 0;
      if (favA !== favB) return favB - favA;

      if (by === "name") return asc ? String(a.name).localeCompare(String(b.name)) : String(b.name).localeCompare(String(a.name));
      if (by === "size") return asc ? (asNum(a.size_gb) - asNum(b.size_gb)) : (asNum(b.size_gb) - asNum(a.size_gb));
      if (by === "tps") {
        const tpsA = asNum(a.max_tps, 0);
        const tpsB = asNum(b.max_tps, 0);
        return asc ? (tpsA - tpsB) : (tpsB - tpsA);
      }
      return 0;
    });
  };

  // Render dropdown list (and set "selected" row)
  window.renderModelDropdown = function renderModelDropdown() {
    const list = document.getElementById("dropdownList");
    const selectedDiv = document.getElementById("dropdown-selected");
    if (!list || !selectedDiv) return;

    list.innerHTML = "";

    window.models.forEach(model => {
      const div = document.createElement("div");
      div.className = "dropdown-item";
      div.setAttribute("data-value", model.value);
      div.setAttribute("data-name", model.name);
      div.setAttribute("data-size", model.size_gb);

      // Highlight background if selected
      if (window.selectedModelValue === model.value) {
        div.style.background = "#195a85";
        div.style.color = "#fff";
      }

      const tpsVal = asNum(model.max_tps, 0);
      const isFav = yn(model.is_favorite);

      const tpsHtml = (tpsVal > 0)
        ? `<span class="model-tps" style="float:right; color:lime; font-family:monospace; margin-left:8px; font-size:1em;">
             ${Math.round(tpsVal)} token/s
           </span>`
        : `<span class="model-tps" style="float:right; color:#85c1e9; font-size:0.9em; margin-left:8px;">No TPS</span>`;

      div.innerHTML = `
        <span class="model-name" style="float:left">${isFav ? "⭐ " : ""}${escapeHtml(model.name)}</span>
        <span class="model-size" style="float:right; opacity:0.7">${escapeHtml(model.size_gb)} GB</span>
        ${tpsHtml}
        <div style="clear:both"></div>
      `;

      div.onclick = function () {
        window.selectedModelValue = model.value;
        const sel = document.getElementById("modelSelect");
        if (sel) sel.value = model.value;

        // Re-render so highlight moves!
        window.renderModelDropdown();
        window.closeDropdown();
      };

      list.appendChild(div);
    });

    // When no model selected, show prompt
    const selectedModel = window.models.find(m => m.value === window.selectedModelValue);
    if (selectedModel) {
      const tpsVal = asNum(selectedModel.max_tps, 0);
      const isFav = yn(selectedModel.is_favorite);

      selectedDiv.innerHTML = `
        <span class="model-name">${isFav ? "⭐ " : ""}${escapeHtml(selectedModel.name)}</span>
        <span style="opacity:0.7">(${escapeHtml(selectedModel.size_gb)} GB)</span>
        <span style="color:lime; font-family:monospace; margin-left:8px;">
          ${tpsVal > 0 ? (Math.round(tpsVal) + " token/s") : "No TPS"}
        </span>
      `;
    } else {
      selectedDiv.innerHTML = `<span style="opacity:0.7">Select a model...</span>`;
    }
  };

  // Dropdown open/close logic
  window.openDropdown = function openDropdown() {
    const list = document.getElementById("dropdownList");
    const sel = document.getElementById("dropdown-selected");
    if (list) list.style.display = "block";
    if (sel) sel.classList.add("open");
  };

  window.closeDropdown = function closeDropdown() {
    const list = document.getElementById("dropdownList");
    const sel = document.getElementById("dropdown-selected");
    if (list) list.style.display = "none";
    if (sel) sel.classList.remove("open");
  };

  // Init/wiring (was the early DOMContentLoaded block)
  window.initModelDropdownUI = function initModelDropdownUI() {
    const showControlsBar = document.getElementById("showControlsBar");
    if (showControlsBar && typeof window.isMobile === "function") {
      showControlsBar.style.display = window.isMobile() ? "" : "none";
    }

    window.loadModelDropdown();

    // Open/close toggle
    const ddSelected = document.getElementById("dropdown-selected");
    const ddList = document.getElementById("dropdownList");

    if (ddSelected) {
      ddSelected.onclick = function (e) {
        e.stopPropagation();
        const isOpen = ddList && ddList.style.display === "block";
        if (isOpen) window.closeDropdown();
        else window.openDropdown();
      };
    }

    // Clicking outside closes dropdown
    document.addEventListener("click", function () {
      window.closeDropdown();
    });

    // Keep clicks inside dropdown from closing
    if (ddList) {
      ddList.onclick = function (e) {
        e.stopPropagation();
      };
    }

    // Sorting button handlers
    [
      { id: "sortNameAsc", by: "name", dir: "asc" },
      { id: "sortNameDesc", by: "name", dir: "desc" },
      { id: "sortSizeAsc", by: "size", dir: "asc" },
      { id: "sortSizeDesc", by: "size", dir: "desc" },
      { id: "sortTPSAsc", by: "tps", dir: "asc" },
      { id: "sortTPSDesc", by: "tps", dir: "desc" }
    ].forEach(({ id, by, dir }) => {
      const btn = document.getElementById(id);
      if (btn) btn.onclick = () => {
        window.currentModelSort = { by, dir };
        window.sortModels(by, dir === "asc");
        window.renderModelDropdown();
      };
    });

    // ---- Drawer open on no model loaded ----
    fetch("/model/model_status", { credentials: "same-origin" })
      .then(res => res.json())
      .then(data => {
        if (!data || data.status !== "running") {
          const drawer = document.getElementById("modelDrawer");
          if (drawer && !drawer.classList.contains("open")) {
            drawer.classList.add("open");

            // Force dropdown closed
            if (ddList) ddList.style.display = "none";
            if (ddSelected) ddSelected.classList.remove("open");

            if (typeof window.updateChatForSidebar === "function") window.updateChatForSidebar();

            // Auto-hide the drawer bar after opening the model drawer (mobile only)
            const drawerBar = document.querySelector(".drawer-bar");
            const chatInput = document.querySelector(".chat-input-container");
            const btn = document.getElementById("toggleDrawerBarBtn");
            const showBtn = document.getElementById("showControlsBtn");
            if (drawerBar && chatInput && btn && showBtn && !drawerBar.classList.contains("hidden")) {
              if (typeof window.isMobile === "function" && window.isMobile()) {
                if (typeof window.toggleDrawerBar === "function") window.toggleDrawerBar();
              }
            }
          }
        }
      })
      .catch(() => { /* quiet */ });

    // Drawer bar: hide when any panel button pressed (except toggle)
    document.querySelectorAll(".drawer-bar button").forEach(btn => {
      if (btn.id !== "toggleDrawerBarBtn") {
        btn.addEventListener("click", () => {
          if (typeof window.isMobile === "function" && window.isMobile()) {
            if (typeof window.toggleDrawerBar === "function") window.toggleDrawerBar();
          }
        });
      }
    });

    if (typeof window.handleDrawerBarOnResize === "function") {
      window.handleDrawerBarOnResize();
    }
  };

})();
