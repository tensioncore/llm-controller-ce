(function () {
  window.models = window.models || [];
  window.currentModelSort = window.currentModelSort || { by: "name", dir: "asc" };
  window.selectedModelValue = window.selectedModelValue || null;
  window.titleModelValue = window.titleModelValue || "";

  let mainPicker = null;
  let titlePicker = null;
  let modelsLoaded = false;
  let pickerStatus = null;
  let resolvedSelection = null;
  let showFriendlyNames = false;

  function resolveMainSelection() {
    if (!modelsLoaded || !pickerStatus || !mainPicker) return;
    const running = pickerStatus.status === "running";
    const preferred = running
      ? window.models.find(model => model.path_key === pickerStatus.current_model_key)
      : window.models.find(model => yn(model.is_favorite));
    const value = preferred ? String(preferred.value || "") : "";
    const selection = JSON.stringify([pickerStatus.status, pickerStatus.current_model_key || "", value]);
    // Repeated status polls/list refreshes must not erase a manual choice for the next Start.
    if (selection !== resolvedSelection || (value && !window.selectedModelValue)) {
      mainPicker.setValue(value);
      resolvedSelection = selection;
    }
  }

  window.syncModelPickerStatus = function syncModelPickerStatus(data) {
    if (!data || !["running", "stopped"].includes(data.status)) return;
    pickerStatus = data;
    resolveMainSelection();
  };

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

  function formatMaxTps(model) {
    const value = asNum(model && model.max_tps, 0);
    return value > 0 ? `Max ${Math.round(value)} TPS` : "Max TPS —";
  }

  function appendModelSummary(target, model, useFriendlyNames = false) {
    target.textContent = "";
    const primary = document.createElement("div");
    primary.className = "model-picker-primary";

    const name = document.createElement("span");
    name.className = "model-name";
    const friendlyName = useFriendlyNames ? String(model.friendly_name || "").trim() : "";
    name.textContent = `${yn(model.is_favorite) ? "⭐ " : ""}${friendlyName || String(model.name || model.value || "Unknown model")}`;
    if (String(model.mmproj_path || "").trim()) {
      const imageMarker = document.createElement("span");
      imageMarker.className = "model-img-marker";
      imageMarker.setAttribute("role", "img");
      imageMarker.setAttribute("aria-label", "Image-capable model");
      imageMarker.title = "Image-capable model";
      imageMarker.innerHTML = `
        <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <rect x="1.5" y="2" width="13" height="12" rx="1.5"></rect>
          <circle cx="5.2" cy="5.4" r="1.1"></circle>
          <path d="m3 12 3.2-3 2.2 2 1.7-1.7L13 12.5"></path>
        </svg>
      `;
      name.appendChild(imageMarker);
    }
    primary.appendChild(name);

    const tps = document.createElement("span");
    const measured = asNum(model.max_tps, 0) > 0;
    tps.className = `model-tps ${measured ? "is-measured" : "is-missing"}`;
    tps.textContent = formatMaxTps(model);
    primary.appendChild(tps);

    const size = document.createElement("span");
    size.className = "model-size";
    const sizeValue = asNum(model.size_gb, 0);
    size.textContent = sizeValue > 0 ? `${sizeValue} GB` : "Size —";
    primary.appendChild(size);
    target.appendChild(primary);
  }

  function createModelPicker(config) {
    const selected = document.getElementById(config.selectedId);
    const list = document.getElementById(config.listId);
    if (!selected || !list) return null;

    let pickerModels = [];
    let isOpen = false;

    selected.setAttribute("role", "combobox");
    selected.setAttribute("tabindex", "0");
    selected.setAttribute("aria-haspopup", "listbox");
    selected.setAttribute("aria-autocomplete", "none");
    selected.setAttribute("aria-label", config.ariaLabel || "Model");
    selected.setAttribute("aria-controls", config.listId);
    list.setAttribute("role", "listbox");

    function currentValue() {
      return String(config.getValue() || "");
    }

    function close(options = {}) {
      isOpen = false;
      list.hidden = true;
      selected.classList.remove("open");
      selected.setAttribute("aria-expanded", "false");
      if (options.focus) selected.focus();
    }

    function getOptions() {
      return Array.from(list.querySelectorAll('[role="option"]'));
    }

    function focusOption(index) {
      const options = getOptions();
      if (!options.length) return;
      const bounded = (index + options.length) % options.length;
      options[bounded].focus();
    }

    function open(focusSelected = false) {
      if (!list.childElementCount) return;
      isOpen = true;
      list.hidden = false;
      selected.classList.add("open");
      selected.setAttribute("aria-expanded", "true");
      if (focusSelected) {
        const options = getOptions();
        const selectedIndex = Math.max(0, options.findIndex(option => option.getAttribute("aria-selected") === "true"));
        focusOption(selectedIndex);
      }
    }

    function choose(value) {
      config.setValue(String(value || ""));
      render();
      close({ focus: true });
      if (typeof config.onSelect === "function") config.onSelect(String(value || ""));
    }

    function onOptionKeydown(event) {
      const options = getOptions();
      const index = options.indexOf(event.currentTarget);
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        focusOption(index + (event.key === "ArrowDown" ? 1 : -1));
      } else if (event.key === "Home" || event.key === "End") {
        event.preventDefault();
        focusOption(event.key === "Home" ? 0 : options.length - 1);
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        choose(event.currentTarget.dataset.value || "");
      } else if (event.key === "Escape") {
        event.preventDefault();
        close({ focus: true });
      } else if (event.key === "Tab") {
        close();
      }
    }

    function makeOption(value, model, label) {
      const option = document.createElement("div");
      option.className = "dropdown-item";
      option.dataset.value = String(value || "");
      option.setAttribute("role", "option");
      option.setAttribute("tabindex", "-1");
      option.setAttribute("aria-selected", currentValue() === String(value || "") ? "true" : "false");
      if (model) {
        appendModelSummary(option, model, config.useFriendlyNames && showFriendlyNames);
      } else {
        const prompt = document.createElement("span");
        prompt.className = "model-picker-placeholder";
        prompt.textContent = label;
        option.appendChild(prompt);
      }
      option.addEventListener("click", () => choose(value));
      option.addEventListener("keydown", onOptionKeydown);
      return option;
    }

    function render() {
      const value = currentValue();
      const current = pickerModels.find(model => String(model.value || "") === value);
      list.textContent = "";

      if (config.allowBlank) {
        list.appendChild(makeOption("", null, config.blankLabel));
      }
      pickerModels.forEach(model => list.appendChild(makeOption(model.value, model)));

      if (current) {
        appendModelSummary(selected, current, config.useFriendlyNames && showFriendlyNames);
      } else {
        selected.textContent = "";
        const prompt = document.createElement("span");
        prompt.className = "model-picker-placeholder";
        prompt.textContent = config.allowBlank ? config.blankLabel : config.placeholder;
        selected.appendChild(prompt);
      }
    }

    selected.addEventListener("click", (event) => {
      event.stopPropagation();
      if (isOpen) close(); else open(false);
    });
    selected.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        open(true);
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        if (isOpen) close(); else open(true);
      } else if (event.key === "Escape") {
        event.preventDefault();
        close();
      } else if (event.key === "Tab") {
        close();
      }
    });
    list.addEventListener("click", event => event.stopPropagation());
    document.addEventListener("click", () => close());

    close();
    render();

    return {
      close,
      open,
      render,
      setModels(models) {
        pickerModels = Array.isArray(models) ? [...models] : [];
        if (typeof config.sortModels === "function") pickerModels.sort(config.sortModels);
        const value = currentValue();
        if (value && !pickerModels.some(model => String(model.value || "") === value)) {
          config.setValue("");
          if (typeof config.onInvalid === "function") config.onInvalid(value);
        }
        render();
      },
      setValue(value, options = {}) {
        const normalized = String(value || "");
        if (options.validate !== false && normalized && !pickerModels.some(model => String(model.value || "") === normalized)) {
          config.setValue("");
          if (typeof config.onInvalid === "function") config.onInvalid(normalized);
        } else {
          config.setValue(normalized);
        }
        render();
      }
    };
  }

  window.createModelPicker = createModelPicker;
  window.formatModelMaxTps = formatMaxTps;

  function compareModels(a, b, by, asc, options = {}) {
    if (options.favoritesFirst !== false) {
      const favA = yn(a.is_favorite) ? 1 : 0;
      const favB = yn(b.is_favorite) ? 1 : 0;
      if (favA !== favB) return favB - favA;
    }

    let result = 0;
    if (by === "name") result = String(a.name).localeCompare(String(b.name));
    if (by === "size") result = asNum(a.size_gb) - asNum(b.size_gb);
    if (by === "tps") result = asNum(a.max_tps, 0) - asNum(b.max_tps, 0);

    if (result !== 0) return asc ? result : -result;
    return options.nameTiebreak ? String(a.name).localeCompare(String(b.name)) : 0;
  }

  // Sorting utility
  window.sortModels = function sortModels(by, asc) {
    window.models.sort((a, b) => compareModels(a, b, by, asc));
  };

  function setHiddenValue(id, value) {
    const input = document.getElementById(id);
    if (input) input.value = String(value || "");
  }

  function setTitleStatus(text, state = "") {
    const status = document.getElementById("titleModelStatus");
    if (!status) return;
    status.textContent = text;
    status.dataset.state = state;
  }

  function ensurePickers() {
    if (!mainPicker) {
      mainPicker = createModelPicker({
        selectedId: "dropdown-selected",
        listId: "dropdownList",
        ariaLabel: "Chat model",
        useFriendlyNames: true,
        placeholder: "Select a model…",
        getValue: () => window.selectedModelValue || "",
        setValue: (value) => {
          window.selectedModelValue = value || null;
          setHiddenValue("modelSelect", value);
        }
      });
    }

    if (!titlePicker) {
      titlePicker = createModelPicker({
        selectedId: "titleModelSelected",
        listId: "titleModelList",
        ariaLabel: "Title generation model",
        allowBlank: true,
        blankLabel: "Select Title Generation Model",
        sortModels: (a, b) => compareModels(a, b, "tps", false, {
          favoritesFirst: false,
          nameTiebreak: true
        }),
        getValue: () => window.titleModelValue || "",
        setValue: (value) => {
          window.titleModelValue = String(value || "");
          setHiddenValue("titleModelPathInput", value);
        },
        onSelect: (value) => {
          window.updateTitleModelStatus();
          if (!value) setTitleStatus("Using main model fallback.", "fallback");
        },
        onInvalid: () => setTitleStatus("Selected title model is unavailable; using main model fallback.", "warning")
      });
    }
  }

  window.setTitleModelSelection = function setTitleModelSelection(value) {
    window.titleModelValue = String(value || "");
    setHiddenValue("titleModelPathInput", window.titleModelValue);
    ensurePickers();
    if (titlePicker) titlePicker.setValue(window.titleModelValue, { validate: window.models.length > 0 });
    window.updateTitleModelStatus();
  };

  window.getTitleModelSelection = function getTitleModelSelection() {
    return String(window.titleModelValue || "");
  };

  window.updateTitleModelStatus = function updateTitleModelStatus(statusData) {
    const selectedPath = String(window.titleModelValue || "");
    if (!selectedPath) {
      setTitleStatus("Using main model fallback.", "fallback");
      return;
    }

    const root = statusData && typeof statusData === "object" ? statusData : {};
    const info = root.title_generation && typeof root.title_generation === "object"
      ? root.title_generation
      : null;

    if (!info) {
      setTitleStatus("Dedicated title model selected. Save settings to apply it.", "selected");
    } else if (String(info.selected_model_path || "") !== selectedPath) {
      setTitleStatus("Dedicated title model selected. Save settings to apply it.", "selected");
    } else if (String(info.dedicated_runtime || "").toLowerCase() === "starting") {
      setTitleStatus("Dedicated title runtime starting; using main model fallback for now.", "warning");
    } else if (String(info.dedicated_runtime || "").toLowerCase() === "stopped") {
      setTitleStatus("Dedicated title model configured for the next eligible main-model launch; using main model fallback now.", "selected");
    } else if (info.fallback_active) {
      setTitleStatus("Dedicated title model unavailable; using main model fallback.", "warning");
    } else if (String(info.dedicated_runtime || "").toLowerCase() === "running") {
      setTitleStatus("Dedicated title model active.", "active");
    } else if (String(info.mode || "").toLowerCase() === "dedicated") {
      setTitleStatus("Dedicated title model configured.", "selected");
    } else {
      setTitleStatus("Using main model fallback until the dedicated title runtime is available.", "fallback");
    }
  };

  window.loadModelDropdown = async function loadModelDropdown() {
    try {
      const data = await fetchJsonOrThrow("/model/registry/dropdown");
      window.models = (data && Array.isArray(data.models)) ? data.models : [];
      showFriendlyNames = yn(data.show_friendly_names);
      modelsLoaded = true;
      window.sortModels(window.currentModelSort.by, window.currentModelSort.dir === "asc");
      ensurePickers();
      if (mainPicker) mainPicker.setModels(window.models);
      if (titlePicker) titlePicker.setModels(window.models);
      resolveMainSelection();
    } catch (err) {
      console.error("[model_module] loadModelDropdown failed:", err);
    }
  };

  window.renderModelDropdown = function renderModelDropdown() {
    ensurePickers();
    if (mainPicker) mainPicker.setModels(window.models);
    if (titlePicker) titlePicker.setModels(window.models);
  };

  window.openDropdown = function openDropdown() {
    ensurePickers();
    if (mainPicker) mainPicker.open();
  };

  window.closeDropdown = function closeDropdown() {
    if (mainPicker) mainPicker.close();
  };

  window.initModelDropdownUI = function initModelDropdownUI() {
    const showControlsBar = document.getElementById("showControlsBar");
    if (showControlsBar && typeof window.isMobile === "function") {
      showControlsBar.style.display = window.isMobile() ? "" : "none";
    }

    ensurePickers();
    if (mainPicker) mainPicker.close();
    if (titlePicker) titlePicker.close();
    window.loadModelDropdown();

    const sortSelect = document.getElementById("modelSortSelect");
    if (sortSelect) {
      sortSelect.value = `${window.currentModelSort.by}:${window.currentModelSort.dir}`;
      sortSelect.addEventListener("change", () => {
        const [by, dir] = sortSelect.value.split(":");
        window.currentModelSort = { by, dir };
        window.sortModels(by, dir === "asc");
        window.renderModelDropdown();
      });
    }

    fetch("/model/model_status", { credentials: "same-origin" })
      .then(res => res.json())
      .then(data => {
        window.updateTitleModelStatus(data);
        window.syncModelPickerStatus(data);
        if (!data || data.status !== "running") {
          const drawer = document.getElementById("modelDrawer");
          if (drawer && !drawer.classList.contains("open")) {
            drawer.classList.add("open");

            if (mainPicker) mainPicker.close();

            if (typeof window.updateChatForSidebar === "function") window.updateChatForSidebar();

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
