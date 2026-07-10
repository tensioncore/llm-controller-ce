document.addEventListener('DOMContentLoaded', function() {
  if (typeof window.initModelDropdownUI === 'function') window.initModelDropdownUI();
  restorePromptDraftAfterReload();
});

// Prefixes for our new blueprint-based endpoints
const CHAT_PREFIX       = '/chat';
window.MODEL_PREFIX = '/model';
const SETTINGS_PREFIX   = '/settings';
const ANALYTICS_PREFIX  = '/analytics';

const BENCH_PREFIX = '/benchmark';
window.BENCH_PREFIX = BENCH_PREFIX;

// Expose prefixes for other modules.
window.CHAT_PREFIX = CHAT_PREFIX;
window.SETTINGS_PREFIX = SETTINGS_PREFIX;
window.ANALYTICS_PREFIX = ANALYTICS_PREFIX;

const socket = io({
  path: '/socket.io'
});

// Expose socket for modules that expect window.socket.
window.socket = socket;

let currentSessionId = localStorage.getItem("currentSessionId") || null;
let isResponding = false;
let pendingSessionId = null;     // queue session switch during streaming
let pendingReloadCurrent = false; // optional: refresh current chat after stream ends
let modelLoaded = false;
let modelStartInProgress = false;
let pendingModelStartName = "";
let userHidControls = false;
let stopRequestedMessageId = null;
let currentGenerationMode = null;
const regenerateSnapshots = new Map();
const editPromptSnapshots = new Map();
let pendingPromptEdit = null;
let isPreparingSend = false;
let composerAttachments = [];
let lastNonZeroGpuLayersValue = null;
let fixedLayoutMetricsQueued = false;
let socketJoinedChatSessionId = null;
const STALE_PAGE_PROMPT_DRAFT_KEY = "llmcontroller.pendingPromptDraft";

const SUPPORTED_ATTACHMENT_EXTENSIONS = [
  ".txt", ".md", ".py", ".js", ".ts", ".html", ".css", ".json", ".xml",
  ".yaml", ".yml", ".csv", ".log", ".ini", ".cfg", ".bat", ".ps1", ".sh",
  ".sql", ".php", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs"
];
let MAX_ATTACHMENT_FILES = 8;
let MAX_ATTACHMENT_FILE_BYTES = 1048576;
let MAX_ATTACHMENT_TOTAL_BYTES = 4194304;
const SOCKET_SEND_MAX_BYTES = 10 * 1024 * 1024;

function savePromptDraftForReload(promptText) {
  const text = String(promptText || "").trim();
  if (!text) return;

  try {
    localStorage.setItem(STALE_PAGE_PROMPT_DRAFT_KEY, text);
  } catch (_) {
    // Ignore storage failures; keeping the text in the composer is still useful.
  }
}

function restorePromptDraftAfterReload() {
  const input = document.getElementById("chatInput");
  if (!input) return;

  let draft = "";
  try {
    draft = localStorage.getItem(STALE_PAGE_PROMPT_DRAFT_KEY) || "";
    if (draft) {
      localStorage.removeItem(STALE_PAGE_PROMPT_DRAFT_KEY);
    }
  } catch (_) {
    draft = "";
  }

  if (!draft || String(input.value || "").trim()) return;

  input.value = draft;
  autoResize(input);
}

function isStaleAsyncError(err) {
  return Boolean(window.ApiHttp && window.ApiHttp.isStaleAuthResult && window.ApiHttp.isStaleAuthResult(err?.apiResult));
}

function isRedirectingToLoginError(err) {
  return Boolean(err?.redirectingToLogin || err?.suppressUserMessage);
}

function getAsyncRequestErrorMessage(err, fallback) {
  if (window.ApiHttp && window.ApiHttp.getApiErrorMessage && err?.apiResult) {
    return window.ApiHttp.getApiErrorMessage(err.apiResult, fallback);
  }
  return err?.message || fallback;
}

function getActionErrorMessage(actionLabel, err, fallback, options = {}) {
  const message = getAsyncRequestErrorMessage(err, fallback);
  const draftWillBeRestored = Boolean(options.draftWillBeRestored);
  if (isStaleAsyncError(err) || draftWillBeRestored) {
    return draftWillBeRestored
      ? `${message} Your draft prompt will be restored after reload.`
      : message;
  }
  return actionLabel ? `${actionLabel}: ${message}` : message;
}

function waitForSocketConnection(timeoutMs = 5000) {
  return new Promise((resolve, reject) => {
    if (socket.connected) {
      resolve();
      return;
    }

    let settled = false;
    const finish = (err) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timerId);
      socket.off("connect", handleConnect);
      socket.off("connect_error", handleConnectError);
      if (err) {
        reject(err);
        return;
      }
      resolve();
    };

    const handleConnect = () => finish();
    const handleConnectError = (error) => {
      const message = error?.message || "Chat connection failed. Reload the page and try again.";
      const err = new Error(message);
      err.reloadRequired = true;
      finish(err);
    };

    const timerId = window.setTimeout(() => {
      const err = new Error("Chat connection timed out. Reload the page and try again.");
      err.reloadRequired = true;
      finish(err);
    }, timeoutMs);

    socket.on("connect", handleConnect);
    socket.on("connect_error", handleConnectError);
    socket.connect();
  });
}

function getVisibleElementHeight(element, options = {}) {
  if (!element) return 0;
  if (options.ignoreHiddenClass && element.classList && element.classList.contains("hidden")) {
    return 0;
  }

  const styles = window.getComputedStyle(element);
  if (styles.display === "none" || styles.visibility === "hidden") {
    return 0;
  }

  const rect = element.getBoundingClientRect();
  return rect.height > 0 ? Math.ceil(rect.height) : 0;
}

function getBottomControlsHeight() {
  const drawerBar = document.querySelector(".drawer-bar");
  const showControlsBar = document.getElementById("showControlsBar");
  const drawerBarHeight = getVisibleElementHeight(drawerBar, { ignoreHiddenClass: true });
  const mobileControlsHeight = getVisibleElementHeight(showControlsBar);

  if (isMobile()) {
    return Math.max(drawerBarHeight, mobileControlsHeight);
  }

  return drawerBarHeight;
}

function updateFixedLayoutMetrics() {
  const root = document.documentElement;
  const chatInput = document.querySelector(".chat-input-container");
  const topbar = document.querySelector(".topbar");
  const bottomControlsHeight = getBottomControlsHeight();
  const composerHeight = getVisibleElementHeight(chatInput);
  const topbarHeight = getVisibleElementHeight(topbar) || 48;
  const chatViewportBottom = composerHeight > 0
    ? (bottomControlsHeight + composerHeight)
    : (bottomControlsHeight + 120);

  root.style.setProperty("--bottom-controls-height", `${bottomControlsHeight}px`);
  root.style.setProperty("--chat-input-height", `${composerHeight}px`);
  root.style.setProperty("--chat-viewport-bottom", `${chatViewportBottom}px`);
  root.style.setProperty("--drawer-top-offset", `${topbarHeight}px`);
  root.style.setProperty("--drawer-bottom-offset", `${bottomControlsHeight}px`);
  root.style.setProperty("--drawer-open-max-height", `calc(100dvh - ${bottomControlsHeight}px - ${topbarHeight}px)`);
}

function queueFixedLayoutMetricsUpdate() {
  if (fixedLayoutMetricsQueued) return;
  fixedLayoutMetricsQueued = true;

  const runUpdate = () => {
    fixedLayoutMetricsQueued = false;
    updateFixedLayoutMetrics();
  };

  if (typeof window.requestAnimationFrame === "function") {
    window.requestAnimationFrame(runUpdate);
  } else {
    window.setTimeout(runUpdate, 0);
  }
}

window.updateFixedLayoutMetrics = updateFixedLayoutMetrics;

function parseGpuLayersValue(value) {
  const parsed = Number.parseInt(String(value ?? "").trim(), 10);
  return Number.isFinite(parsed) ? parsed : null;
}

function syncCpuOnlyToggleFromGpuLayers() {
  const gpuInput = document.getElementById("nGpuLayers");
  const toggle = document.getElementById("cpuOnlyToggle");
  if (!gpuInput || !toggle) return;

  const gpuLayersValue = parseGpuLayersValue(gpuInput.value);
  if (gpuLayersValue !== null && gpuLayersValue > 0) {
    lastNonZeroGpuLayersValue = String(gpuLayersValue);
  }

  const cpuOnlyEnabled = gpuLayersValue === 0;
  toggle.checked = cpuOnlyEnabled;
  gpuInput.disabled = cpuOnlyEnabled;
}

function setCpuOnlyEnabled(enabled) {
  const gpuInput = document.getElementById("nGpuLayers");
  const toggle = document.getElementById("cpuOnlyToggle");
  if (!gpuInput || !toggle) return;

  const currentGpuLayersValue = parseGpuLayersValue(gpuInput.value);
  if (enabled) {
    if (currentGpuLayersValue !== null && currentGpuLayersValue > 0) {
      lastNonZeroGpuLayersValue = String(currentGpuLayersValue);
    }
    gpuInput.value = "0";
  } else if (currentGpuLayersValue === 0 && lastNonZeroGpuLayersValue) {
    gpuInput.value = lastNonZeroGpuLayersValue;
  }

  gpuInput.disabled = enabled;
  toggle.checked = enabled;
}

function initializeCpuOnlyToggle() {
  const gpuInput = document.getElementById("nGpuLayers");
  const toggle = document.getElementById("cpuOnlyToggle");
  if (!gpuInput || !toggle || toggle.dataset.bound === "1") return;

  toggle.dataset.bound = "1";

  toggle.addEventListener("change", () => {
    setCpuOnlyEnabled(toggle.checked);
  });

  gpuInput.addEventListener("input", () => {
    const gpuLayersValue = parseGpuLayersValue(gpuInput.value);
    if (gpuLayersValue !== null && gpuLayersValue > 0) {
      lastNonZeroGpuLayersValue = String(gpuLayersValue);
    }
  });

  gpuInput.addEventListener("change", () => {
    syncCpuOnlyToggleFromGpuLayers();
  });

  syncCpuOnlyToggleFromGpuLayers();
}

function initializeFixedLayoutMetricsObserver() {
  if (window.__fixedLayoutMetricsObserverStarted) return;
  window.__fixedLayoutMetricsObserverStarted = true;

  if (typeof window.ResizeObserver !== "function") return;

  const observer = new window.ResizeObserver(() => {
    queueFixedLayoutMetricsUpdate();
  });

  const observedElements = [
    document.querySelector(".chat-input-container"),
    document.querySelector(".drawer-bar"),
    document.getElementById("showControlsBar"),
    document.querySelector(".topbar")
  ];

  observedElements.forEach((element) => {
    if (element) observer.observe(element);
  });
}

function syncModelDrawerRuntimeState(runtime) {
  if (!runtime || typeof runtime !== "object") return;

  const modelDrawer = document.getElementById("modelDrawer");
  if (!modelDrawer || !modelDrawer.classList.contains("open")) return;

  const gpuInput = document.getElementById("nGpuLayers");
  const cpuInput = document.getElementById("nCpuThreads");

  if (gpuInput && runtime.n_gpu_layers !== undefined && runtime.n_gpu_layers !== null) {
    gpuInput.value = String(runtime.n_gpu_layers);
  }
  if (cpuInput && runtime.n_threads !== undefined && runtime.n_threads !== null) {
    cpuInput.value = String(runtime.n_threads);
  }

  syncCpuOnlyToggleFromGpuLayers();
}

function requestLoadChat(sessionId, reason = "") {
  if (!sessionId) return;

    // If streaming, never mutate DOM. Queue it.
    if (isResponding) {
        pendingSessionId = sessionId;
        return;
    }

  loadChat(sessionId);
}


function syncSocketChatSessionSubscription(nextSessionId) {
  const targetSessionId = (nextSessionId || "").trim();

  if (socketJoinedChatSessionId && socketJoinedChatSessionId !== targetSessionId) {
    socket.emit("leave_chat_session", {
      session_id: socketJoinedChatSessionId
    });
    socketJoinedChatSessionId = null;
  }

  if (targetSessionId && socketJoinedChatSessionId !== targetSessionId) {
    socket.emit("join_chat_session", {
      session_id: targetSessionId
    });
    socketJoinedChatSessionId = targetSessionId;
  }
}


function isMobile() {
  return window.innerWidth <= 1080;
}

function getAttachmentExtension(filename) {
  const dot = (filename || "").lastIndexOf(".");
  return dot >= 0 ? filename.slice(dot).toLowerCase() : "";
}

function formatAttachmentSize(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function parseIntSettingValue(value, fallback, minValue = 0) {
  const parsed = Number.parseInt(String(value ?? "").trim(), 10);
  if (!Number.isFinite(parsed) || parsed < minValue) {
    return fallback;
  }
  return parsed;
}

function normalizeTurnAttachmentNames(attachments) {
  const seen = new Set();
  const names = [];

  (attachments || []).forEach((attachment) => {
    const name = typeof attachment === "string"
      ? attachment
      : (attachment && typeof attachment.name === "string" ? attachment.name : "");
    const trimmed = (name || "").trim();
    if (!trimmed || seen.has(trimmed)) return;
    seen.add(trimmed);
    names.push(trimmed);
  });

  return names;
}

function renderUserBubbleAttachments(bubble, attachments) {
  if (!bubble) return;

  const existing = bubble.querySelector(".turn-attachment-list");
  if (existing) {
    existing.remove();
  }

  const attachmentNames = normalizeTurnAttachmentNames(attachments);
  if (!attachmentNames.length) return;

  const list = document.createElement("div");
  list.className = "turn-attachment-list";

  attachmentNames.forEach((nameText) => {
    const chip = document.createElement("span");
    chip.className = "turn-attachment-chip";
    chip.innerText = nameText;
    chip.title = nameText;
    list.appendChild(chip);
  });

  const actions = bubble.querySelector(".message-actions");
  if (actions && actions.parentNode === bubble) {
    bubble.insertBefore(list, actions);
  } else {
    bubble.appendChild(list);
  }
}

function refreshComposerButtons() {
  const sendButton = document.getElementById("sendButton");
  const attachButton = document.getElementById("attachFilesBtn");
  const attachmentInput = document.getElementById("chatAttachmentInput");

  if (sendButton) {
    sendButton.classList.toggle("stop-generation-btn", isResponding);
    sendButton.onclick = function (event) {
      if (event) event.preventDefault();
      if (isResponding) {
        stopActiveResponse();
      } else {
        sendChat();
      }
    };

    if (isResponding) {
      sendButton.innerText = stopRequestedMessageId ? "Stopping..." : "Stop";
      sendButton.disabled = Boolean(stopRequestedMessageId);
    } else if (isPreparingSend) {
      sendButton.innerText = "Preparing...";
      sendButton.disabled = true;
    } else {
      sendButton.innerText = pendingPromptEdit ? "Save Edit" : "➡️ Send";
      sendButton.disabled = false;
    }
  }

  if (attachButton) {
    attachButton.disabled = isPreparingSend || isResponding;
  }

  if (attachmentInput) {
    attachmentInput.disabled = isPreparingSend || isResponding;
  }
}

function renderComposerAttachments() {
  const preview = document.getElementById("attachmentPreview");
  if (!preview) return;

  preview.innerHTML = "";
  composerAttachments.forEach((attachment) => {
    const chip = document.createElement("div");
    chip.className = "attachment-chip";

    const name = document.createElement("span");
    name.className = "attachment-chip-name";
    name.innerText = attachment.name;
    chip.appendChild(name);

    const meta = document.createElement("span");
    meta.className = "attachment-chip-meta";
    meta.innerText = formatAttachmentSize(attachment.size);
    chip.appendChild(meta);

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "attachment-chip-remove";
    removeBtn.innerText = "×";
    removeBtn.disabled = isPreparingSend || isResponding;
    removeBtn.onclick = function () {
      composerAttachments = composerAttachments.filter((item) => item.id !== attachment.id);
      renderComposerAttachments();
    };
    chip.appendChild(removeBtn);

    preview.appendChild(chip);
  });

  queueFixedLayoutMetricsUpdate();
}

function clearComposerAttachments() {
  composerAttachments = [];
  const attachmentInput = document.getElementById("chatAttachmentInput");
  if (attachmentInput) {
    attachmentInput.value = "";
  }
  renderComposerAttachments();
}

function updateScrollToBottomButtonVisibility() {
  const chatContainer = document.querySelector(".chat-container");
  const chatInput = document.querySelector(".chat-input-container");
  const button = document.getElementById("scrollToBottomBtn");
  if (!chatContainer || !button) return;

  const chatUiHidden = chatInput && chatInput.style.display === "none";
  if (chatUiHidden) {
    button.classList.remove("visible");
    button.setAttribute("aria-hidden", "true");
    return;
  }

  const distanceFromBottom = chatContainer.scrollHeight - chatContainer.scrollTop - chatContainer.clientHeight;
  const shouldShow = distanceFromBottom > 40;

  button.classList.toggle("visible", shouldShow);
  button.setAttribute("aria-hidden", shouldShow ? "false" : "true");
}

function scrollChatContainerToBottomSmooth() {
  const chatContainer = document.querySelector(".chat-container");
  if (!chatContainer) return;

  if (typeof chatContainer.scrollTo === "function") {
    chatContainer.scrollTo({
      top: chatContainer.scrollHeight,
      behavior: "smooth"
    });
  } else {
    chatContainer.scrollTop = chatContainer.scrollHeight;
  }

  window.setTimeout(updateScrollToBottomButtonVisibility, 200);
}

function scrollChatMessagesToBottom(chatMessages) {
  if (!chatMessages) return;
  const chatContainer = document.querySelector(".chat-container");
  if (!chatContainer) return;

  const lastRenderedNode = chatMessages.lastElementChild;

  const runScroll = () => {
    if (lastRenderedNode && typeof lastRenderedNode.scrollIntoView === "function") {
      lastRenderedNode.scrollIntoView({ behavior: "auto", block: "end" });
    }
    chatContainer.scrollTop = chatContainer.scrollHeight;
    updateScrollToBottomButtonVisibility();
  };

  queueFixedLayoutMetricsUpdate();

  if (typeof window.requestAnimationFrame === "function") {
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        runScroll();
        window.setTimeout(runScroll, 60);
      });
    });
  } else {
    window.setTimeout(() => {
      runScroll();
      window.setTimeout(runScroll, 60);
    }, 0);
  }
}

function handleAttachmentSelection(event) {
  const input = event.target;
  const pickedFiles = Array.from(input?.files || []);
  if (!pickedFiles.length) return;

  let totalBytes = composerAttachments.reduce((sum, attachment) => sum + (attachment.size || 0), 0);
  const existingKeys = new Set(composerAttachments.map((attachment) => `${attachment.name}|${attachment.size}|${attachment.lastModified}`));
  const rejected = [];

  for (const file of pickedFiles) {
    const ext = getAttachmentExtension(file.name);
    const fileKey = `${file.name}|${file.size}|${file.lastModified}`;

    if (!SUPPORTED_ATTACHMENT_EXTENSIONS.includes(ext)) {
      rejected.push(`${file.name} is not a supported text/code file.`);
      continue;
    }

    if (file.size <= 0) {
      rejected.push(`${file.name} is empty.`);
      continue;
    }

    if (file.size > MAX_ATTACHMENT_FILE_BYTES) {
      rejected.push(`${file.name} exceeds the ${Math.round(MAX_ATTACHMENT_FILE_BYTES / 1024)} KB per-file limit.`);
      continue;
    }

    if (existingKeys.has(fileKey)) {
      continue;
    }

    if (composerAttachments.length >= MAX_ATTACHMENT_FILES) {
      rejected.push(`You can attach up to ${MAX_ATTACHMENT_FILES} files per message.`);
      break;
    }

    if ((totalBytes + file.size) > MAX_ATTACHMENT_TOTAL_BYTES) {
      rejected.push(`Total attachments exceed the ${Math.round(MAX_ATTACHMENT_TOTAL_BYTES / (1024 * 1024))} MB limit.`);
      break;
    }

    composerAttachments.push({
      id: crypto.randomUUID ? crypto.randomUUID() : (Date.now() + "-" + Math.random().toString(16).slice(2)),
      name: file.name,
      size: file.size,
      lastModified: file.lastModified,
      file
    });
    existingKeys.add(fileKey);
    totalBytes += file.size;
  }

  input.value = "";
  renderComposerAttachments();

  if (rejected.length) {
    showCustomAlert(rejected.join("\n"));
  }
}

async function buildAttachmentPayload() {
  const payload = [];
  for (const attachment of composerAttachments) {
    let text = "";
    try {
      text = await attachment.file.text();
    } catch (err) {
      throw new Error(`Could not read ${attachment.name} as text.`);
    }

    if (!text) {
      throw new Error(`${attachment.name} is empty.`);
    }
    if (text.includes("\u0000")) {
      throw new Error(`${attachment.name} looks like a binary file and can't be used here.`);
    }

    payload.push({
      name: attachment.name,
      size: attachment.size,
      content: text
    });
  }
  return payload;
}

function getPayloadByteSize(payload) {
  const serialized = JSON.stringify(payload);

  if (typeof TextEncoder !== "undefined") {
    return new TextEncoder().encode(serialized).length;
  }

  if (typeof Blob !== "undefined") {
    return new Blob([serialized]).size;
  }

  return serialized.length;
}

// --- Handle tab/page focus or visibility change to repair chat display ---
document.addEventListener('visibilitychange', handleVisibilityOrFocusReturn);
window.addEventListener('focus', handleVisibilityOrFocusReturn);

function handleVisibilityOrFocusReturn() {
  // Only act if we're now visible or focused
  if (!(document.visibilityState === "visible" || document.hasFocus())) return;

  // Phase 3: NEVER reload/clear chat DOM mid-stream. Only repair socket.
  if (typeof socket?.connected === "boolean" && !socket.connected) {
    socket.connect();
  }

  // Optional (disabled): if you truly want a refresh when not responding, do it safely.
  /*
  if (!isResponding && currentSessionId) {
    loadChat(currentSessionId);
  }
  */
}

function sendDebugChat() {
  const input = document.getElementById("chatInput");
  const message = input.value.trim();
  if (!message) return;

  postJSON(`${CHAT_PREFIX}/debug_llama_response`, { message: message })
    .then(response => response.json())
    .then(data => {
      let outputText = "";
      if (data.full_response && Array.isArray(data.full_response)) {
        outputText = data.full_response
          .map(obj => JSON.stringify(obj, null, 2))
          .join("\n\n");
      } else {
        outputText = JSON.stringify(data, null, 2);
      }

      // Modal content with pre/code block and a button
      const modalHTML = `
        <h3 style="margin-top:0;">Debug Output</h3>
        <pre id="debugModalPre" style="max-height:600px; overflow:auto; background:#222; color:#fff; padding:10px; border-radius:8px; text-align:left; font-size:14px;">${escapeHtml(outputText)}</pre>
        <button id="copyDebugModalBtn" style="margin-top:10px;">📋 Copy All Text</button>
        <div style="margin-top:10px; font-size:12px; color:#666;">(Click anywhere outside to close)</div>
      `;
      showCustomAlert(modalHTML, true);

      // Attach copy handler after modal is added
      setTimeout(() => {
        const btn = document.getElementById("copyDebugModalBtn");
        const pre = document.getElementById("debugModalPre");
        if (btn && pre) {
          btn.onclick = function (event) {
            event.preventDefault();
            event.stopPropagation(); // Prevent modal close
            copyTextToClipboard(pre.textContent || "", btn);
          };
        }
      }, 10);

    })
    .catch(err => {
      showCustomAlert("❌ Error: " + err);
    });
}

async function createNewSession(options = {}) {
  try {
    const data = await window.ApiHttp.postJSONRequest(
      `${CHAT_PREFIX}/new_chat`,
      {},
      options,
      "Failed to create a new chat session."
    );

    if (data.status !== "success" || !data.session_id) {
      throw new Error("Failed to create a new chat session.");
    }

    currentSessionId = data.session_id;
    localStorage.setItem("currentSessionId", currentSessionId);
    syncSocketChatSessionSubscription(currentSessionId);
    loadChatHistory();
    return currentSessionId;
  } catch (err) {
    throw err;
  }
}

function groupSessions(sessions) {
  const grouped = { "Today": [], "Yesterday": [], "Beyond": [] };
  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterdayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  
  sessions.forEach(session => {
    const sessionDate = new Date(session.timestamp * 1000);
    if (sessionDate >= todayStart) {
      grouped.Today.push(session);
    } else if (sessionDate >= yesterdayStart) {
      grouped.Yesterday.push(session);
    } else {
      grouped.Beyond.push(session);
    }
  });
  return grouped;
}

function closeSidebarOnMobile() {
  if (window.innerWidth <= 1080) {
    document.querySelector('.sidebar').classList.remove('open');
    updateChatForSidebar();
  }
}

function displayGroupedSessions(sessions) {
  const grouped = groupSessions(sessions);
  const chatList = document.getElementById("chatHistory");
  chatList.innerHTML = "";

  ["Today", "Yesterday", "Beyond"].forEach(category => {
    if (grouped[category].length > 0) {
      const header = document.createElement("H5");
      header.innerHTML = `<strong>${category}</strong>`;
      header.style.cursor = "default";
      header.style.padding = "5px 10px";
      chatList.appendChild(header);
      
      grouped[category].forEach(session => {
        const li = document.createElement("li");
        const span = document.createElement("span");
        span.textContent = session.session_name;
        li.onclick = () => {
          requestLoadChat(session.session_id, "sidebar_click");
          closeSidebarOnMobile();
        };        
          
        const renameBtn = document.createElement("button");
        renameBtn.textContent = "✏️";
        renameBtn.onclick = (event) => {
          event.stopPropagation();
          renameSession(session.session_id, session.session_name);
        };

        const deleteBtn = document.createElement("button");
        deleteBtn.textContent = "❌";
        deleteBtn.onclick = (event) => {
          event.stopPropagation();
          deleteSession(session.session_id);
        };

        li.appendChild(span);
        li.appendChild(renameBtn);
        li.appendChild(deleteBtn);

        chatList.appendChild(li);
      });
    }
  });
}

function updateChatForSidebar() {
  const sidebar = document.querySelector(".sidebar");
  const chatContainer = document.querySelector(".chat-container");
  const chatInputContainer = document.querySelector(".chat-input-container");
  if (sidebar.classList.contains("open")) {
    chatContainer.style.left = "250px";
    chatContainer.style.width = "calc(100% - 250px)";
    chatInputContainer.style.left = "250px";
    chatInputContainer.style.width = "calc(100% - 250px)";
  } else {
    chatContainer.style.left = "0";
    chatContainer.style.width = "100%";
    chatInputContainer.style.left = "0";
    chatInputContainer.style.width = "100%";
  }
}

function updateThoughtsUI(bubble) {
  const thoughtsDiv = bubble.querySelector(".thoughts");
  const toggle = bubble.querySelector(".show-thoughts");
  const text = (bubble._thoughtsText || "").trim();
  const isExpanded = Boolean(bubble._thoughtsExpanded);

  if (!thoughtsDiv || !toggle) return;

  if (text.length > 0) {
    thoughtsDiv.innerText = text;
    toggle.style.display = "block";
    thoughtsDiv.style.display = isExpanded ? "block" : "none";
    toggle.innerText = isExpanded ? "Hide Thoughts" : "Show Thoughts";
  } else {
    bubble._thoughtsExpanded = false;
    thoughtsDiv.style.display = "none";
    toggle.style.display = "none";
    toggle.innerText = "Show Thoughts";
  }
}

function setThoughtsExpanded(bubble, expanded) {
  if (!bubble) return;
  bubble._thoughtsExpanded = Boolean(expanded);
  updateThoughtsUI(bubble);
}

function formatResponseFooter(metrics) {
  const parts = [];
  if (metrics && metrics.stopped) {
    parts.push("Stopped");
  }

  if (metrics && metrics.tps != null && metrics.response_time != null && metrics.total_tokens != null) {
    parts.push(`Tokens/s: ${metrics.tps}`);
    parts.push(`Response Tokens: ${metrics.total_tokens}`);
    parts.push(formatTime(metrics.response_time));
  }

  return parts.join(" | ");
}

function setUserBubbleText(bubble, message) {
  if (!bubble) return;
  const messageDiv = bubble.querySelector(".message");
  if (!messageDiv) return;
  messageDiv.textContent = message || "";
}

function captureBotBubbleSnapshot(bubble) {
  if (!bubble) return null;

  const messageDiv = bubble.querySelector(".message");
  const footer = bubble.querySelector(".footer");
  const thoughtsDiv = bubble.querySelector(".thoughts");
  const toggle = bubble.querySelector(".show-thoughts");

  return {
    messageId: bubble.dataset.messageId || "",
    messageHtml: messageDiv ? messageDiv.innerHTML : "",
    footerText: footer ? footer.innerText : "",
    thoughtsText: thoughtsDiv ? thoughtsDiv.innerText : "",
    thoughtsDisplay: thoughtsDiv ? thoughtsDiv.style.display : "none",
    toggleText: toggle ? toggle.innerText : "Show Thoughts",
    toggleDisplay: toggle ? toggle.style.display : "none"
  };
}

function captureUserBubbleSnapshot(bubble) {
  if (!bubble) return null;
  const messageDiv = bubble.querySelector(".message");
  return {
    messageId: bubble.dataset.messageId || "",
    messageText: messageDiv ? messageDiv.textContent : "",
    attachmentNames: Array.from(bubble.querySelectorAll(".turn-attachment-chip")).map((chip) => (chip.textContent || "").trim()).filter(Boolean)
  };
}

function restoreBotBubbleFromSnapshot(snapshot, targetBubble) {
  if (!snapshot || !targetBubble) return false;
  targetBubble.dataset.messageId = snapshot.messageId || "";

  const messageDiv = targetBubble.querySelector(".message");
  const footer = targetBubble.querySelector(".footer");
  const thoughtsDiv = targetBubble.querySelector(".thoughts");
  const toggle = targetBubble.querySelector(".show-thoughts");

  if (messageDiv) {
    messageDiv.classList.remove("streaming-plain");
    messageDiv.innerHTML = snapshot.messageHtml;
    enhanceCodeBlocks(messageDiv);
  }

  if (footer) footer.innerText = snapshot.footerText;
  if (thoughtsDiv) {
    targetBubble._thoughtsText = snapshot.thoughtsText || "";
    targetBubble._thoughtsExpanded = snapshot.thoughtsDisplay === "block";
    thoughtsDiv.innerText = snapshot.thoughtsText;
    thoughtsDiv.style.display = snapshot.thoughtsDisplay;
  }
  if (toggle) {
    toggle.innerText = snapshot.toggleText;
    toggle.style.display = snapshot.toggleDisplay;
  }

  if (window.MathJax && window.MathJax.typesetPromise && messageDiv) {
    window.MathJax.typesetPromise([messageDiv]);
  }

  return true;
}

function restoreBotBubbleSnapshot(messageId, bubble = null) {
  const snapshot = regenerateSnapshots.get(messageId);
  const targetBubble = bubble || findBotBubbleByMessageId(messageId);
  if (!snapshot || !targetBubble) return false;
  restoreBotBubbleFromSnapshot(snapshot, targetBubble);
  regenerateSnapshots.delete(messageId);
  return true;
}

function restoreEditedTurnSnapshot(messageId) {
  const snapshot = editPromptSnapshots.get(messageId);
  const userBubble = findUserBubbleByMessageId(messageId);
  const botBubble = findBotBubbleByMessageId(messageId);
  if (!snapshot || !userBubble || !botBubble) return false;

  userBubble.dataset.messageId = snapshot.userSnapshot.messageId || "";
  botBubble.dataset.messageId = snapshot.botSnapshot.messageId || "";
  setUserBubbleText(userBubble, snapshot.userSnapshot.messageText);
  renderUserBubbleAttachments(userBubble, snapshot.userSnapshot.attachmentNames || []);
  restoreBotBubbleFromSnapshot(snapshot.botSnapshot, botBubble);

  editPromptSnapshots.delete(messageId);
  return true;
}

function clearPromptEditState(options = {}) {
  const { clearInput = false } = options;
  pendingPromptEdit = null;

  if (clearInput) {
    const input = document.getElementById("chatInput");
    if (input) {
      input.value = "";
      autoResize(input);
    }
  }

  refreshComposerButtons();
  refreshAssistantBubbleControls();
}

function closeOpenDrawerWithTogglePath() {
  const openDrawer = document.querySelector(".drawer.open");
  if (!openDrawer || !openDrawer.id) return;
  toggleDrawer(openDrawer.id);
}

function startPromptEdit(messageId, messageText) {
  if (isResponding || !messageId) return;

  if (pendingPromptEdit && pendingPromptEdit.sourceMessageId === messageId) {
    clearPromptEditState();
    return;
  }

  const input = document.getElementById("chatInput");
  const sendButton = document.getElementById("sendButton");
  if (!input || !sendButton) return;

  closeOpenDrawerWithTogglePath();

  pendingPromptEdit = {
    sourceMessageId: messageId
  };
  input.value = messageText || "";
  autoResize(input);
  input.focus();
  refreshComposerButtons();
  refreshAssistantBubbleControls();
}

function prepareBubbleForStreaming(bubble, options = {}) {
  if (!bubble) return;

  const { preserveMessage = false } = options;
  const messageDiv = bubble.querySelector(".message");
  const footer = bubble.querySelector(".footer");
  const thoughtsDiv = bubble.querySelector(".thoughts");
  const toggle = bubble.querySelector(".show-thoughts");

  bubble._streamText = "";
  bubble._thoughtsText = "";
  bubble._thoughtsExpanded = false;

  if (messageDiv) {
    messageDiv.classList.add("streaming-plain");
    if (!preserveMessage) {
      messageDiv.textContent = "";
    }
  }

  if (footer) {
    footer.innerText = "Thinking...";
  }

  if (thoughtsDiv) {
    thoughtsDiv.innerText = "";
    thoughtsDiv.style.display = "none";
  }

  if (toggle) {
    toggle.innerText = "Show Thoughts";
    toggle.style.display = "none";
  }
}

function getLastRegeneratableBubble() {
  const chatMessages = document.getElementById("chatMessages");
  if (!chatMessages) return null;

  const bubbles = Array.from(chatMessages.querySelectorAll(".chat-bubble.bot[data-message-id]"));
  return bubbles.length ? bubbles[bubbles.length - 1] : null;
}

function getLastEditableUserBubble() {
  const chatMessages = document.getElementById("chatMessages");
  if (!chatMessages) return null;

  const bubbles = Array.from(chatMessages.querySelectorAll(".chat-bubble.user[data-message-id]"));
  return bubbles.length ? bubbles[bubbles.length - 1] : null;
}

function refreshAssistantBubbleControls() {
  const chatMessages = document.getElementById("chatMessages");
  if (!chatMessages) return;

  const lastEligibleBubble = getLastRegeneratableBubble();
  const lastEditableBubble = getLastEditableUserBubble();

  chatMessages.querySelectorAll(".chat-bubble.bot").forEach((bubble) => {
    const messageId = bubble.dataset.messageId || "";
    const stopBtn = bubble.querySelector(".stop-response-btn");
    const regenBtn = bubble.querySelector(".regenerate-response-btn");
    const pager = bubble.querySelector(".variant-pagination");
    const prevBtn = bubble.querySelector(".variant-prev-btn");
    const nextBtn = bubble.querySelector(".variant-next-btn");
    const label = bubble.querySelector(".variant-page-label");
    const variantCount = Number(bubble.dataset.variantCount || 0);
    const variantPosition = Number(bubble.dataset.variantPosition || 0);
    const prevVariantId = bubble.dataset.prevVariantId || "";
    const nextVariantId = bubble.dataset.nextVariantId || "";

    if (stopBtn) {
      const showStop = Boolean(isResponding && messageId && window.currentAssistantMessageId === messageId);
      stopBtn.style.display = showStop ? "inline-flex" : "none";
      stopBtn.disabled = !showStop || stopRequestedMessageId === messageId;
      stopBtn.innerText = stopRequestedMessageId === messageId ? "Stopping..." : "Stop";
    }

    if (regenBtn) {
      const showRegenerate = Boolean(!isResponding && lastEligibleBubble === bubble && messageId);
      regenBtn.style.display = showRegenerate ? "inline-flex" : "none";
      regenBtn.disabled = !showRegenerate;
    }

    if (pager) {
      const showPager = Boolean(!isResponding && lastEligibleBubble === bubble && variantCount > 1);
      pager.style.display = showPager ? "inline-flex" : "none";
    }

    if (prevBtn) {
      prevBtn.disabled = !prevVariantId || isResponding;
    }

    if (nextBtn) {
      nextBtn.disabled = !nextVariantId || isResponding;
    }

    if (label) {
      label.innerText = variantCount > 1 ? `${variantPosition}/${variantCount}` : "";
    }
  });

  chatMessages.querySelectorAll(".chat-bubble.user").forEach((bubble) => {
    const messageId = bubble.dataset.messageId || "";
    const editBtn = bubble.querySelector(".edit-prompt-btn");
    if (!editBtn) return;

    const isEditingThisBubble = Boolean(pendingPromptEdit && pendingPromptEdit.sourceMessageId === messageId);
    const showEdit = Boolean(!isResponding && lastEditableBubble === bubble && messageId);

    editBtn.style.display = showEdit ? "inline-flex" : "none";
    editBtn.disabled = !showEdit;
    editBtn.innerText = isEditingThisBubble ? "Cancel Edit" : "Edit Prompt";
  });
}

function stopActiveResponse() {
  if (!isResponding || !window.currentAssistantMessageId) return;

  const messageId = window.currentAssistantMessageId;
  stopRequestedMessageId = messageId;
  refreshComposerButtons();
  refreshAssistantBubbleControls();

  socket.emit("stop_generation", {
    session_id: currentSessionId,
    message_id: messageId,
    prompt_id: messageId
  }, function (ack = {}) {
    if (ack.status === "stopping") return;

    stopRequestedMessageId = null;
    if (ack.status === "idle") {
      isResponding = false;
      currentGenerationMode = null;
      if (!restoreEditedTurnSnapshot(messageId)) {
        restoreBotBubbleSnapshot(messageId);
      }
    }

    refreshComposerButtons();
    renderComposerAttachments();
    refreshAssistantBubbleControls();

    if (ack.message) {
      showCustomAlert(ack.message);
    }
  });
}

function regenerateResponse(messageId) {
  if (isResponding || !currentSessionId || !messageId) return;

  const bubble = findBotBubbleByMessageId(messageId);
  if (!bubble) return;

  clearPromptEditState();
  closeOpenDrawerWithTogglePath();

  const newMessageId = (crypto.randomUUID ? crypto.randomUUID() : (Date.now() + "-" + Math.random().toString(16).slice(2)));
  regenerateSnapshots.set(newMessageId, captureBotBubbleSnapshot(bubble));
  bubble.dataset.messageId = newMessageId;
  isResponding = true;
  currentGenerationMode = "regenerate";
  stopRequestedMessageId = null;
  pendingSessionId = null;
  pendingReloadCurrent = false;
  window.currentAssistantMessageId = newMessageId;

  prepareBubbleForStreaming(bubble, { preserveMessage: true });
  refreshComposerButtons();
  refreshAssistantBubbleControls();

  socket.emit("regenerate_message", {
    session_id: currentSessionId,
    message_id: newMessageId,
    prompt_id: messageId
  });
}

function selectVariant(promptId) {
  if (isResponding || !currentSessionId || !promptId) return;

  clearPromptEditState();
  postJSON(`${CHAT_PREFIX}/select_variant`, {
    session_id: currentSessionId,
    prompt_id: promptId
  })
    .then(res => res.json())
    .then(data => {
      if (data.status !== "success") {
        throw new Error(data.message || "Could not load that version.");
      }
      loadChat(currentSessionId);
    })
    .catch(err => {
      showCustomAlert(err?.message || String(err));
    });
}

document.addEventListener("DOMContentLoaded", () => {
  const sidebar = document.querySelector(".sidebar");
  const sidebarToggle = document.getElementById("sidebarToggle");
  const chatMessages = document.getElementById("chatMessages");
  const attachFilesBtn = document.getElementById("attachFilesBtn");
  const chatAttachmentInput = document.getElementById("chatAttachmentInput");
  const chatContainer = document.querySelector(".chat-container");
  const scrollToBottomBtn = document.getElementById("scrollToBottomBtn");

  if (chatMessages && chatMessages.children.length === 0) {
    localStorage.removeItem("currentSessionId");
    currentSessionId = null;
  }
  if (chatAttachmentInput) {
    chatAttachmentInput.accept = SUPPORTED_ATTACHMENT_EXTENSIONS.join(",");
    chatAttachmentInput.addEventListener("change", handleAttachmentSelection);
  }
  if (attachFilesBtn && chatAttachmentInput) {
    attachFilesBtn.addEventListener("click", () => {
      if (isPreparingSend || isResponding) return;
      chatAttachmentInput.click();
    });
  }
  initializeCpuOnlyToggle();
  initializeFixedLayoutMetricsObserver();
  refreshComposerButtons();
  renderComposerAttachments();
  handleDrawerBarOnResize();
  queueFixedLayoutMetricsUpdate();
  if (chatContainer) {
    chatContainer.addEventListener("scroll", updateScrollToBottomButtonVisibility, { passive: true });
  }
  if (scrollToBottomBtn) {
    scrollToBottomBtn.addEventListener("click", scrollChatContainerToBottomSmooth);
  }
  window.addEventListener("resize", updateScrollToBottomButtonVisibility);
  updateScrollToBottomButtonVisibility();
  sidebarToggle.addEventListener("click", () => {
    sidebar.classList.toggle("open");
    updateChatForSidebar();
    // Close any open drawer if on mobile
    if (window.innerWidth <= 1080) {
      document.querySelectorAll('.drawer.open').forEach(drawer => {
        drawer.classList.remove('open');
      });
    }
  });  

  const newChatButton = document.querySelector(".new-chat");
  if (newChatButton) {
    newChatButton.addEventListener("click", () => {
      clearPromptEditState({ clearInput: true });
      clearComposerAttachments();
      syncSocketChatSessionSubscription("");
      localStorage.removeItem("currentSessionId");
      currentSessionId = null;
      createNewSession().then(() => {
        document.getElementById("chatMessages").innerHTML = "";
        closeSidebarOnMobile();
      }).catch((err) => {
        if (isRedirectingToLoginError(err)) {
          return;
        }
        showCustomAlert(getActionErrorMessage("", err, "Could not create a new chat."));
        console.error(err);
      });
    });
  } else {
    console.warn("New Chat button not found.");
  }

  const saveSettingsBtn = document.getElementById("saveSettingsBtn");
  if (saveSettingsBtn) {
    // Replace the node to remove previously attached listeners.
    const fresh = saveSettingsBtn.cloneNode(true);
    saveSettingsBtn.parentNode.replaceChild(fresh, saveSettingsBtn);

    fresh.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      saveSettings();
    });
  }


  loadSettings();

  loadChatHistory();
  initModelDrawer();

  document.querySelectorAll('.sidebar li').forEach(li => {
    li.addEventListener('click', () => {
      // Existing: load the selected chat
      // selectChat(li); // (or whatever loads the chat)
  
      // Auto-close sidebar on mobile
      if (isMobile()) {
        document.querySelector('.sidebar').classList.remove('open');
        updateChatForSidebar();
      }
    });
  });  
});

socket.on("connect", function () {
  if (socketJoinedChatSessionId) {
    const joinedSessionId = socketJoinedChatSessionId;
    socketJoinedChatSessionId = null;
    syncSocketChatSessionSubscription(joinedSessionId);
  }
});

socket.on("update_session_name", function(data) {
  loadChatHistory();
  loadSettings();
});

function toggleDrawer(drawerId) {
  const allDrawers = document.querySelectorAll('.drawer');
  const drawer = document.getElementById(drawerId);
  const chatInput = document.querySelector('.chat-input-container');
  const drawerBar = document.querySelector('.drawer-bar');

  // If drawer doesn't exist (role-gated out), do nothing safely.
  if (!drawer) return;

  // If this drawer is already open, close all and show chat UI
  if (drawer.classList.contains('open')) {
    allDrawers.forEach(d => d.classList.remove('open'));
    if (chatInput) chatInput.style.display = '';
    // Do NOT auto-show drawer bar here: let user choose when to show controls again
    // Optionally, always update floating button visibility:
    queueFixedLayoutMetricsUpdate();
    updateScrollToBottomButtonVisibility();
    return;
  }

  // Otherwise, close all, open requested, and hide chat UI
  allDrawers.forEach(d => d.classList.remove('open'));
  drawer.classList.add('open');
  if (chatInput) chatInput.style.display = 'none';
  if (drawerBar && drawerBar.classList.contains('hidden')) {
    // Optionally show the "Show Controls" floating button
    // (or whatever is your logic)
  }

  // Special behaviors (only if the functions exist)
  if (drawerId === "adminDrawer" && typeof loadSettings === "function") loadSettings();
  if (drawerId === "analyticsDrawer" && typeof window.fetchAnalytics === "function") window.fetchAnalytics();
  if (drawerId === "modelDrawer" && typeof loadModelDropdown === "function") {
    loadModelDropdown();
    syncCpuOnlyToggleFromGpuLayers();
    pollModelStatus();
  }

  queueFixedLayoutMetricsUpdate();
  updateScrollToBottomButtonVisibility();
}

// This closes ALL drawers and restores chat input, leaves drawer-bar always visible
function closeAllDrawers() {
  document.querySelectorAll('.drawer').forEach(d => d.classList.remove('open'));
  const chatInput = document.querySelector('.chat-input-container');
  if (chatInput) chatInput.style.display = '';
  queueFixedLayoutMetricsUpdate();
  updateScrollToBottomButtonVisibility();
}

let GPU_LAYERS = "";
let CPU_THREADS = "";
function renderAuthReadinessPanel(auth) {
  const panel = document.getElementById("authReadinessPanel");
  if (!panel || !auth) return;

  const safe = (value) => {
    const text = (value === undefined || value === null) ? "" : String(value);
    return typeof escapeHtml === "function" ? escapeHtml(text) : text.replace(/[&<>"']/g, "");
  };
  const yesNo = (value) => value ? "Yes" : "No";
  const warning = auth.warning ? `<div style="color:#ffcf8a; margin-top:8px;">${safe(auth.warning)}</div>` : "";

  panel.innerHTML = `
    <div><strong>Auth readiness</strong></div>
    <div>Email confirmation structure: ${safe(yesNo(auth.email_confirmation_ready))}</div>
    <div>Forgot password routes: ${safe(yesNo(auth.forgot_password_ready))}</div>
    <div>SMTP: ${safe(auth.smtp_enabled ? "Enabled" : "Disabled")}</div>
    <div>Public base URL: ${safe(auth.public_base_url_configured ? "Configured" : "Missing")}</div>
    <div>Reset token TTL: ${safe(auth.reset_token_ttl_minutes)} minutes</div>
    <div>Confirmation token TTL: ${safe(auth.email_confirm_token_ttl_minutes)} minutes</div>
    <div>Email request cooldown: ${safe(auth.email_token_request_cooldown_seconds)} seconds</div>
    ${warning}
  `;
}

function loadSettings() {
  fetch(`${SETTINGS_PREFIX}/get_settings`, { credentials: "same-origin" })
    .then(r => r.json())
    .then(data => {
      if (!data || typeof data !== "object") throw new Error("Invalid settings JSON");

      const setVal = (id, val) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.value = (val === undefined || val === null) ? "" : String(val);
      };
      const setChecked = (id, val) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.checked = Boolean(val);
      };

      // Admin drawer
      setVal("dbHostInput", data.db_host);
      setVal("dbPortInput", data.db_port);
      setVal("llamaServerPathInput", data.llama_server_path);
      setVal("llamaMainPortInput", data.llama_main_port);
      setVal("llamaTitlePortInput", data.llama_title_port);
      setVal("scanDirectoryInput", data.scan_directory);
      setVal("versionDisplay", data.version);

      window.APP_VERSION = data.version;

      setVal("defaultGpuLayers",     data.n_gpu_layers);
      GPU_LAYERS = data.n_gpu_layers;
      setVal("defaultCpuThreads",    data.n_cpu_threads);
      CPU_THREADS = data.n_cpu_threads;
      setVal("dualGpuSplitThresholdGb", data.dual_gpu_split_threshold_gb);
      setVal("defaultTemperature",   data.temperature);
      setVal("defaultTopK",          data.top_k);
      setVal("defaultTopP",          data.top_p);
      setVal("defaultRepeatPenalty", data.repeat_penalty);
      setVal("defaultSeed",          data.seed);
      setVal("attachmentMaxFiles", data.attachments_max_files);
      setVal("attachmentMaxFileBytes", data.attachments_max_file_bytes);
      setVal("attachmentMaxTotalBytes", data.attachments_max_total_bytes);
      setVal("attachmentMaxContextChars", data.attachments_max_context_chars);
      setVal("attachmentChunkMaxLines", data.attachments_chunk_max_lines);
      setVal("attachmentChunkOverlapLines", data.attachments_chunk_overlap_lines);
      if (data.auth) {
        setChecked("authSmtpEnabled", data.auth.smtp_enabled);
        setVal("authSmtpHost", data.auth.smtp_host);
        setVal("authSmtpPort", data.auth.smtp_port);
        setChecked("authSmtpUseTls", data.auth.smtp_use_tls);
        setVal("authSmtpUsername", data.auth.smtp_username);
        setVal("authSmtpPassword", "");
        const smtpPasswordInput = document.getElementById("authSmtpPassword");
        if (smtpPasswordInput) {
          smtpPasswordInput.placeholder = data.auth.smtp_password_set
            ? "Password is saved; leave blank to keep it"
            : "Leave blank until SMTP password is configured";
        }
        setVal("authSmtpFromEmail", data.auth.smtp_from_email);
        setVal("authPublicBaseUrl", data.auth.public_base_url);
        setVal("authResetTokenTtl", data.auth.reset_token_ttl_minutes);
        setVal("authConfirmTokenTtl", data.auth.email_confirm_token_ttl_minutes);
        setVal("authEmailCooldown", data.auth.email_token_request_cooldown_seconds);
        renderAuthReadinessPanel(data.auth);
      }

      MAX_ATTACHMENT_FILES = parseIntSettingValue(data.attachments_max_files, 8, 1);
      MAX_ATTACHMENT_FILE_BYTES = parseIntSettingValue(data.attachments_max_file_bytes, 1048576, 1);
      MAX_ATTACHMENT_TOTAL_BYTES = parseIntSettingValue(data.attachments_max_total_bytes, 4194304, 1);

      // Model drawer (if present)
      setVal("nGpuLayers",      data.n_gpu_layers);
      setVal("nCpuThreads",     data.n_cpu_threads);
      setVal("temperature",     data.temperature);
      setVal("topK",            data.top_k);
      setVal("topP",            data.top_p);
      setVal("repeatPenalty",   data.repeat_penalty);
      setVal("seed",            data.seed);

      syncCpuOnlyToggleFromGpuLayers();
      queueFixedLayoutMetricsUpdate();

      if (typeof window.refreshAppTitle === "function") window.refreshAppTitle();
    })
    .catch(err => {
      console.error(err);
    });
}

function saveSettings() {
  // Guard against double invocation.
  if (window.__saveSettingsInFlight) return;
  window.__saveSettingsInFlight = true;

  const done = () => { window.__saveSettingsInFlight = false; }; 
  const getVal = (id) => {
    const el = document.getElementById(id);
    return el ? el.value : "";
  };
  const getChecked = (id) => {
    const el = document.getElementById(id);
    return el ? Boolean(el.checked) : false;
  };

  // Admin-only guard (save button exists only for admin)
  const saveBtnEl = document.getElementById("saveSettingsBtn");
  if (!saveBtnEl) {
    showCustomAlert("⚠️ Admin settings are not available for this account.");
    done();
    return;
  }

  // CSRF: forward-only (explicit token element we control)
  const csrfToken = (document.getElementById("adminCsrfToken")?.value || "").trim();
  if (!csrfToken) {
    showCustomAlert("❌ Missing CSRF token (adminCsrfToken).");
    done();
    return;
  }

  const scanDirectory = getVal("scanDirectoryInput").trim();
  const payload = {
    db_host:           getVal("dbHostInput").trim(),
    db_port:           getVal("dbPortInput"),
    llama_server_path: getVal("llamaServerPathInput").trim(),
    llama_main_port:   getVal("llamaMainPortInput"),
    llama_title_port:  getVal("llamaTitlePortInput"),
    n_gpu_layers:     getVal("defaultGpuLayers"),
    n_cpu_threads:    getVal("defaultCpuThreads"),
    dual_gpu_split_threshold_gb: getVal("dualGpuSplitThresholdGb"),
    temperature:      getVal("defaultTemperature"),
    top_k:            getVal("defaultTopK"),
    top_p:            getVal("defaultTopP"),
    repeat_penalty:   getVal("defaultRepeatPenalty"),
    seed:             getVal("defaultSeed"),
    attachments_max_files: getVal("attachmentMaxFiles"),
    attachments_max_file_bytes: getVal("attachmentMaxFileBytes"),
    attachments_max_total_bytes: getVal("attachmentMaxTotalBytes"),
    attachments_max_context_chars: getVal("attachmentMaxContextChars"),
    attachments_chunk_max_lines: getVal("attachmentChunkMaxLines"),
    attachments_chunk_overlap_lines: getVal("attachmentChunkOverlapLines"),
    auth_smtp_enabled: getChecked("authSmtpEnabled"),
    auth_smtp_host: getVal("authSmtpHost").trim(),
    auth_smtp_port: getVal("authSmtpPort"),
    auth_smtp_use_tls: getChecked("authSmtpUseTls"),
    auth_smtp_username: getVal("authSmtpUsername").trim(),
    auth_smtp_password: getVal("authSmtpPassword"),
    auth_smtp_from_email: getVal("authSmtpFromEmail").trim(),
    auth_public_base_url: getVal("authPublicBaseUrl").trim(),
    auth_reset_token_ttl_minutes: getVal("authResetTokenTtl"),
    auth_email_confirm_token_ttl_minutes: getVal("authConfirmTokenTtl"),
    auth_email_token_request_cooldown_seconds: getVal("authEmailCooldown"),
  };
  if (scanDirectory) {
    payload.scan_directory = scanDirectory;
  }

  window.ApiHttp.postJSONRequest(
    `${SETTINGS_PREFIX}/update_settings`,
    payload,
    { csrfToken },
    "Settings update failed."
  )
    .then((json) => {
      if (json.status !== "success") {
        throw new Error("Settings update failed.");
      }

      showCustomAlert("Settings updated successfully!");
      loadSettings();
      done();
    })
    .catch(err => {
      if (isRedirectingToLoginError(err)) {
        done();
        return;
      }
      showCustomAlert(getActionErrorMessage("Error updating settings", err, "Settings update failed."));
      console.error(err);
      done();
    });
}

async function exportSettingsBackup() {
  const button = document.getElementById("exportSettingsBackupBtn");
  if (button && button.dataset.busy === "1") return;

  const originalText = button ? button.textContent : "";
  if (button) {
    button.dataset.busy = "1";
    button.disabled = true;
    button.textContent = "Exporting...";
  }

  try {
    const res = await fetch(`${SETTINGS_PREFIX}/export_backup`, {
      method: "GET",
      credentials: "same-origin"
    });

    const contentType = (res.headers.get("Content-Type") || "").toLowerCase();

    if (!res.ok) {
      let message = `HTTP ${res.status}`;

      if (contentType.includes("application/json")) {
        const data = await res.json().catch(() => ({}));
        message = data.error || data.message || message;
      } else {
        const text = await res.text().catch(() => "");
        if (text && text.trim()) {
          message = text.trim();
        }
      }

      throw new Error(message);
    }

    if (!contentType.includes("application/json")) {
      const text = await res.text().catch(() => "");
      const lowered = (text || "").toLowerCase();

      if (res.redirected || lowered.includes("<html") || lowered.includes("<!doctype") || lowered.includes("<form")) {
        throw new Error("Session expired or invalid export response. Please log in again.");
      }

      throw new Error("Invalid export response type; expected application/json.");
    }

    const data = await res.json().catch(() => null);
    if (
      !data ||
      data.backup_type !== "llm-controller-settings" ||
      !data.exported_at ||
      !Array.isArray(data.settings)
    ) {
      throw new Error("Invalid settings backup payload.");
    }

    const disposition = res.headers.get("Content-Disposition") || "";
    let filename = "llm-controller-settings-backup.json";

    const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    const plainMatch = disposition.match(/filename="?([^\";]+)"?/i);

    if (utf8Match && utf8Match[1]) {
      filename = decodeURIComponent(utf8Match[1]);
    } else if (plainMatch && plainMatch[1]) {
      filename = plainMatch[1];
    }

    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const downloadUrl = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();

    window.setTimeout(() => window.URL.revokeObjectURL(downloadUrl), 1000);
    showCustomAlert("Settings backup exported.");
  } catch (err) {
    showCustomAlert("Settings export failed: " + (err?.message || err));
    console.error(err);
  } finally {
    if (button) {
      button.dataset.busy = "";
      button.disabled = false;
      button.textContent = originalText;
    }
  }
}

function loadChatHistory() {
  fetch(`${CHAT_PREFIX}/get_sessions`)
    .then(res => res.json())
    .then(data => {
      displayGroupedSessions(data.sessions);
    });
}

async function sendChat() {
  if (isResponding || isPreparingSend) return; // hard guard

  if (!modelLoaded) {
    showCustomAlert("No model loaded!");
    return;
  }

  const input = document.getElementById("chatInput");
  const message = input.value.trim();
  if (!message) return;

  let attachmentPayload = [];
  try {
    isPreparingSend = true;
    refreshComposerButtons();
    renderComposerAttachments();

    if (window.ApiHttp && typeof window.ApiHttp.ensureSessionAlive === "function") {
      await window.ApiHttp.ensureSessionAlive({
        onSessionExpired: () => savePromptDraftForReload(message)
      });
    }

    await waitForSocketConnection();

    if (!currentSessionId) {
      await createNewSession({
        onSessionExpired: () => savePromptDraftForReload(message)
      });
      if (!currentSessionId) {
        throw new Error("Failed to create a new chat session.");
      }
    }

    attachmentPayload = await buildAttachmentPayload();
  } catch (err) {
    if (isStaleAsyncError(err) || err?.reloadRequired) {
      savePromptDraftForReload(message);
    }
    isPreparingSend = false;
    refreshComposerButtons();
    renderComposerAttachments();
    if (isRedirectingToLoginError(err)) {
      return;
    }
    showCustomAlert(getActionErrorMessage("", err, "Could not send the message.", {
      draftWillBeRestored: Boolean(isStaleAsyncError(err) || err?.reloadRequired)
    }));
    return;
  }

  const isEdit = Boolean(pendingPromptEdit && pendingPromptEdit.sourceMessageId);
  const sourceMessageId = isEdit ? pendingPromptEdit.sourceMessageId : null;
  const messageId = (crypto.randomUUID ? crypto.randomUUID() : (Date.now() + "-" + Math.random().toString(16).slice(2)));
  const payload = {
    session_id: currentSessionId,
    message: message,
    message_id: messageId,
    attachments: attachmentPayload
  };
  if (sourceMessageId) {
    payload.edit_prompt_id = sourceMessageId;
  }

  if (getPayloadByteSize(payload) > SOCKET_SEND_MAX_BYTES) {
    isPreparingSend = false;
    refreshComposerButtons();
    renderComposerAttachments();
    showCustomAlert("Attachments are too large to send together in one message. Reduce attachment size or send fewer files.");
    return;
  }

  if (isEdit) {
    const userBubble = findUserBubbleByMessageId(sourceMessageId);
    const botBubble = findBotBubbleByMessageId(sourceMessageId);
    if (!userBubble || !botBubble) {
      isPreparingSend = false;
      clearPromptEditState();
      refreshComposerButtons();
      renderComposerAttachments();
      showCustomAlert("Could not edit that prompt right now.");
      return;
    }

    editPromptSnapshots.set(messageId, {
      userSnapshot: captureUserBubbleSnapshot(userBubble),
      botSnapshot: {
        ...captureBotBubbleSnapshot(botBubble),
        messageId: botBubble.dataset.messageId || ""
      }
    });

    userBubble.dataset.messageId = messageId;
    setUserBubbleText(userBubble, message);
    if (attachmentPayload.length > 0) {
      renderUserBubbleAttachments(userBubble, attachmentPayload);
    }

    botBubble.dataset.messageId = messageId;
    prepareBubbleForStreaming(botBubble);
  } else {
    appendUserMessage(message, { messageId, attachments: attachmentPayload });
    const botBubble = createBotBubble();
    botBubble.dataset.messageId = messageId;
    prepareBubbleForStreaming(botBubble);
  }

  input.value = "";
  autoResize(input);
  isResponding = true;
  currentGenerationMode = isEdit ? "edit_prompt" : "send";
  stopRequestedMessageId = null;

  // Clear any queued actions from earlier
  pendingSessionId = null;
  pendingReloadCurrent = false;
  isPreparingSend = false;

  // Recommended (lets you ignore out-of-order chunks)
  window.currentAssistantMessageId = messageId;
  clearPromptEditState();
  refreshComposerButtons();
  refreshAssistantBubbleControls();
  renderComposerAttachments();

  socket.emit("send_message", payload);
}

/**
 * Renders a full chat entry (both user bubble and bot bubble)
 */
function renderChatMessage(userMessage, botResponse, thoughts, metrics) {
  // first show the user’s message
  appendUserMessage(userMessage);

  // then build the bot bubble
  let botBubble = createBotBubble();
  let messageDiv = botBubble.querySelector('.message');
  messageDiv.innerHTML = "";

  const cleaned = (botResponse || "").replace(/\n{3,}/g, "\n\n").trim();
  const standardized = extractAndStandardizeMath(cleaned);
  messageDiv.innerHTML = renderSafeMarkdown(standardized);
  enhanceCodeBlocks(messageDiv);
  if (window.MathJax && window.MathJax.typesetPromise) {
    window.MathJax.typesetPromise([messageDiv]);
  }

  // if there were “thoughts”, render them too
  if (thoughts && thoughts.trim() !== "") {
    let thoughtsDiv = botBubble.querySelector('.thoughts');
    thoughtsDiv.textContent = thoughts.trim();
  }  

  // finally, attach the metrics to the footer
  let footer = botBubble.querySelector('.footer');
  if (metrics && metrics.tps != null && metrics.response_time != null && metrics.total_tokens != null) {
    footer.innerText = formatResponseFooter(metrics);
  }
}

function loadChat(session_id) {
  // Phase 3 guard: never re-render chat DOM while streaming
  if (isResponding) {
    if (session_id && session_id !== currentSessionId) {
      pendingSessionId = session_id;      // queue switch
    } else {
      pendingReloadCurrent = true;        // optional: refresh current after done
    }
    return;
  }

  currentSessionId = session_id;
  localStorage.setItem("currentSessionId", currentSessionId);
  syncSocketChatSessionSubscription(currentSessionId);
  clearPromptEditState();

  fetch(`${CHAT_PREFIX}/get_chat/${session_id}`)
    .then(res => res.json())
    .then(data => {
      const chatMessages = document.getElementById("chatMessages");
      if (!chatMessages) return;
      const postRenderTasks = [];

      chatMessages.innerHTML = "";
      const chats = data.chats;

      if (!chats || chats.length === 0) return;

      // --- 1. Generated with label at the top ---
      let firstModel = chats[0].model_used || "Unknown Model";
      chatMessages.appendChild(insertModelLabel(`Generated with: ${firstModel}`));

      // --- 2. Walk messages, inserting switch labels as needed ---
      let prevModel = firstModel;
      chats.forEach((msg, i) => {
        // Insert model switch label when model changes (not first message)
        if (i > 0 && msg.model_used && msg.model_used !== prevModel) {
          chatMessages.appendChild(insertModelLabel(`Model switched to: ${msg.model_used}`));
        }
        prevModel = msg.model_used || prevModel;

        // Render messages as before
        if (!msg.user_message && !msg.bot_response) return;
        appendUserMessage(msg.user_message, {
          messageId: msg.prompt_id,
          attachments: msg.attachment_names || []
        });

        const botBubble = createBotBubble();
        if (msg.prompt_id) {
          botBubble.dataset.messageId = msg.prompt_id;
        }
        botBubble.dataset.variantCount = msg.variant_count || "";
        botBubble.dataset.variantPosition = msg.variant_position || "";
        botBubble.dataset.prevVariantId = msg.prev_variant_prompt_id || "";
        botBubble.dataset.nextVariantId = msg.next_variant_prompt_id || "";
        const messageDiv = botBubble.querySelector('.message');

        if (msg.bot_response && messageDiv) {
          let cleaned = msg.bot_response.replace(/\n{3,}/g, '\n\n');
          let standardized = extractAndStandardizeMath(cleaned);
          messageDiv.innerHTML = renderSafeMarkdown(standardized);

          enhanceCodeBlocks(messageDiv);
          if (window.MathJax && window.MathJax.typesetPromise) {
            postRenderTasks.push(window.MathJax.typesetPromise([messageDiv]));
          }
        }

        // ---- Thoughts and toggle for replayed chats ----
        const thoughtsDiv = botBubble.querySelector('.thoughts');
        const toggle = botBubble.querySelector('.show-thoughts');

        if (thoughtsDiv && toggle) {
          // Only show if there are thoughts saved
          if (msg.thoughts && msg.thoughts.trim() !== "") {
            botBubble._thoughtsText = msg.thoughts.trim();
            botBubble._thoughtsExpanded = false;
            updateThoughtsUI(botBubble);
            toggle.onclick = function () {
              setThoughtsExpanded(botBubble, !botBubble._thoughtsExpanded);
            };
          } else {
            botBubble._thoughtsText = "";
            botBubble._thoughtsExpanded = false;
            thoughtsDiv.style.display = "none";
            toggle.style.display = "none";
          }
        }

        const footer = botBubble.querySelector('.footer');
        if (footer && msg.tps != null && msg.response_time != null && msg.total_tokens != null) {
          footer.innerText = formatResponseFooter(msg);
        }
      });

      refreshAssistantBubbleControls();

      const finalizeReplayScroll = () => scrollChatMessagesToBottom(chatMessages);

      if (postRenderTasks.length) {
        Promise.allSettled(postRenderTasks).finally(finalizeReplayScroll);
      } else {
        finalizeReplayScroll();
      }
    });
}



function deleteSession(session_id) {
  if (!confirm("Are you sure you want to delete this chat?")) return;
  postJSON(`${CHAT_PREFIX}/delete_session`, {
    session_id 
  }).then(() => {
    if (currentSessionId === session_id) {
      clearPromptEditState({ clearInput: true });
      clearComposerAttachments();
      syncSocketChatSessionSubscription("");
      localStorage.removeItem("currentSessionId");
      currentSessionId = null;
    }
    loadChatHistory();
  });
}

function renameSession(session_id, currentName) {
  const new_name = prompt("Enter a new session name:", currentName);
  if (!new_name) return;
  postJSON(`${CHAT_PREFIX}/rename_session`, {
session_id, new_name 
  }).then(() => loadChatHistory());
}

function formatResponseMetrics(metrics, footer) {
  footer.innerText = formatResponseFooter(metrics);
}

function getModelDisplayName(pathOrName) {
  if (!pathOrName) return '';
  let name = String(pathOrName).split(/[\\/]/).pop();
  name = name.replace(/\.[^/.]+$/, "");
  name = name.replace(/-\d{5}-of-\d{5}$/i, "");
  return name;
}

function setModelStartLoadingState(isStarting, modelPath = "") {
  modelStartInProgress = Boolean(isStarting);
  pendingModelStartName = modelStartInProgress ? (getModelDisplayName(modelPath) || pendingModelStartName) : "";

  const startBtn = document.getElementById("startModelBtn");
  if (startBtn) {
    if (!startBtn.dataset.defaultText) {
      startBtn.dataset.defaultText = startBtn.textContent.trim();
    }
    startBtn.disabled = modelStartInProgress;
    startBtn.classList.toggle("is-loading", modelStartInProgress);
    startBtn.setAttribute("aria-busy", modelStartInProgress ? "true" : "false");
    startBtn.textContent = modelStartInProgress ? "Starting Model..." : startBtn.dataset.defaultText;
  }

  if (modelStartInProgress) {
    const statusEl = document.getElementById("modelStatusText");
    const modelEl = document.getElementById("currentModel");
    const modeEl = document.getElementById("currentRuntimeMode");
    const gpuEl = document.getElementById("currentGpuLayers");
    const cpuEl = document.getElementById("currentCpuThreads");
    const tmpEl = document.getElementById("currentTemperature");
    const kEl = document.getElementById("currentTopK");
    const pEl = document.getElementById("currentTopP");
    const rEl = document.getElementById("currentRepeatPenalty");
    const seedEl = document.getElementById("currentSeed");
    if (statusEl) statusEl.innerText = "Starting model...";
    if (modelEl) {
      if (pendingModelStartName) modelEl.innerText = pendingModelStartName;
    }
    if (modeEl) {
      modeEl.innerText = "Starting...";
      modeEl.className = "runtime-mode-indicator";
    }
    if (gpuEl) gpuEl.innerText = "";
    if (cpuEl) cpuEl.innerText = "";
    if (tmpEl) tmpEl.innerText = "";
    if (kEl) kEl.innerText = "";
    if (pEl) pEl.innerText = "";
    if (rEl) rEl.innerText = "";
    if (seedEl) seedEl.innerText = "";
  }
}

function startModel(options = {}) {
  const skipCpuOnlyRetryPrompt = Boolean(options && options.skipCpuOnlyRetryPrompt);
  if (modelStartInProgress) return;
  const model = document.getElementById('modelSelect').value;
  const nGpuLayers = document.getElementById('nGpuLayers').value;
  const nCpuThreads = document.getElementById('nCpuThreads').value;
  const temperature = document.getElementById('temperature').value;
  const topK = document.getElementById('topK').value;
  const topP = document.getElementById('topP').value;
  const repeatPenalty = document.getElementById('repeatPenalty').value;
  const seed = document.getElementById('seed').value;

  const getStartModelErrorMessage = (data, fallback = 'Unknown error') => {
    if (data && typeof data.message === "string" && data.message.trim()) return data.message.trim();
    if (data && typeof data.error === "string" && data.error.trim()) return data.error.trim();
    if (data && data.errors && typeof data.errors === "object") {
      const firstError = Object.values(data.errors).find((value) => typeof value === "string" && value.trim());
      if (firstError) return firstError.trim();
    }
    return fallback;
  };

  setModelStartLoadingState(true, model);

  postJSON(`${MODEL_PREFIX}/start_model`, {
      model_path: model,
      n_gpu_layers: nGpuLayers,
      n_threads: nCpuThreads,
      temperature: temperature,
      top_k: topK,
      top_p: topP,
      repeat_penalty: repeatPenalty,
      seed: seed
  })
    .then(async (response) => {
      const text = await response.text();
      let data = null;
      try {
        data = text ? JSON.parse(text) : {};
      } catch (_) {
        data = { status: "error", message: text || `HTTP ${response.status}` };
      }
      return { ok: response.ok, data };
    })
    .then(({ ok, data }) => {
      if (ok && data.status === 'started') {
        GPU_LAYERS = String(nGpuLayers ?? "");
        CPU_THREADS = String(nCpuThreads ?? "");
        syncCpuOnlyToggleFromGpuLayers();
        setModelStartLoadingState(false);
        showCustomAlert('✅ Model loaded successfully');
        toggleDrawer('modelDrawer');
        pollModelStatus();
        // Start logs using the new system module
        if (window.SystemDrawer && typeof window.SystemDrawer.startLogStream === "function") {
          window.SystemDrawer.startLogStream();
        }
      } else {
        const message = getStartModelErrorMessage(data);
        const shouldOfferCpuRetry = Boolean(data && data.retry_cpu_only) && !skipCpuOnlyRetryPrompt;

        setModelStartLoadingState(false);
        pollModelStatus();

        if (shouldOfferCpuRetry) {
          const retryCpuOnly = confirm(
            `${message}\n\nThis model appears too large for your current GPU configuration. Retry in CPU Only mode?`
          );
          if (retryCpuOnly) {
            if (typeof setCpuOnlyEnabled === "function") {
              setCpuOnlyEnabled(true);
            }
            startModel({ skipCpuOnlyRetryPrompt: true });
          }
          return;
        }

        showCustomAlert('❌ Failed to start model: ' + message);
      }
    })
    .catch(error => {
      setModelStartLoadingState(false);
      pollModelStatus();
      showCustomAlert('❌ Error: ' + error.message);
    });
}

function stopModel() {
  postJSON(`${MODEL_PREFIX}/stop_model`, {})
    .then(async (res) => {
      // Always read the body as text first (prevents "Unexpected token E")
      const text = await res.text();

      // Try to parse JSON; if it isn't JSON, keep the raw text as the message
      let data = null;
      try {
        data = text ? JSON.parse(text) : {};
      } catch (_) {
        data = { status: "error", message: text || "Non-JSON response from server." };
      }

      // Respect HTTP failure + backend error contract
      if (!res.ok || (data && data.status === "error")) {
        const msg = (data && (data.message || data.error)) || `HTTP ${res.status}`;
        throw new Error(msg);
      }

      if (modelStartInProgress) {
        setModelStartLoadingState(false);
      }
      showCustomAlert("Model stopped!");
      if (window.SystemDrawer && typeof window.SystemDrawer.stopLogStream === "function") {
        window.SystemDrawer.stopLogStream();   // Stop log streaming on unload.
      }
      pollModelStatus();
    })
    .catch(err => {
      showCustomAlert("❌ Error stopping model: " + err.message);
      console.error(err);
    });
}

function pollModelStatus() {
  fetch(`${MODEL_PREFIX}/model_status`)
    .then(response => response.json())
    .then(data => {
      const statusEl = document.getElementById('modelStatusText');
      const modelEl  = document.getElementById('currentModel');
      const modeEl   = document.getElementById('currentRuntimeMode');
      const gpuEl    = document.getElementById('currentGpuLayers');
      const cpuEl    = document.getElementById('currentCpuThreads');
      const tmpEl    = document.getElementById('currentTemperature');
      const kEl      = document.getElementById('currentTopK');
      const pEl      = document.getElementById('currentTopP');
      const rEl      = document.getElementById('currentRepeatPenalty');
      const seedEl   = document.getElementById('currentSeed');
      const appTitle = document.getElementById("appTitle");

      // Store original branding/version for fallback
      if (!pollModelStatus.defaultTitle) {
        pollModelStatus.defaultTitle = document.title;
      }
      if (!pollModelStatus.defaultAppTitle && appTitle) {
        pollModelStatus.defaultAppTitle = appTitle.innerText;
      }

      // Safe setter (so this works for non-admin users too)
      const setText = (el, txt) => { if (el) el.innerText = (txt ?? ""); };

      function getModelDisplayName(pathOrName) {
        if (!pathOrName) return '';
        let name = String(pathOrName).split(/[\\/]/).pop(); // handles full path or already-basename
        name = name.replace(/\.[^/.]+$/, "");               // drop extension
        name = name.replace(/-\d{5}-of-\d{5}$/i, "");
        return name;
      }

      if (modelStartInProgress) {
        modelLoaded = false;
        setText(statusEl, 'Starting model...');
        if (pendingModelStartName) {
          setText(modelEl, pendingModelStartName);
        }
        if (modeEl) {
          setText(modeEl, 'Starting...');
          modeEl.className = 'runtime-mode-indicator';
        }
        setText(gpuEl, '');
        setText(cpuEl, '');
        setText(tmpEl, '');
        setText(kEl, '');
        setText(pEl, '');
        setText(rEl, '');
        setText(seedEl, '');
        return;
      }

      if (data.status === 'running') {
        modelLoaded = true;
        setText(statusEl, '✅ Running');

        const modelDisplay = data.current_model ? getModelDisplayName(data.current_model) : 'Unknown';
        setText(modelEl, modelDisplay);

        const s = data.settings || {};
        const runtime = (data.runtime && typeof data.runtime === "object") ? data.runtime : {};
        const runtimeGpuLayers = runtime.n_gpu_layers ?? GPU_LAYERS ?? '';
        const runtimeCpuThreads = runtime.n_threads ?? CPU_THREADS ?? '';
        const runtimeMode = String(runtime.runtime_mode || "").toLowerCase();
        const runtimeModeLabel = runtime.runtime_mode_label || (runtimeMode === "cpu" ? "Running on CPU" : (runtimeMode === "gpu" ? "Running on GPU" : ""));

        GPU_LAYERS = runtimeGpuLayers !== '' ? String(runtimeGpuLayers) : GPU_LAYERS;
        CPU_THREADS = runtimeCpuThreads !== '' ? String(runtimeCpuThreads) : CPU_THREADS;

        setText(gpuEl, runtimeGpuLayers);
        setText(cpuEl, runtimeCpuThreads);
        setText(tmpEl,  s['temp']           || '');
        setText(kEl,    s['top-k']          || '');
        setText(pEl,    s['top-p']          || '');
        setText(rEl,    s['repeat-penalty'] || '');
        setText(seedEl, s['seed']           || '');
        if (modeEl) {
          setText(modeEl, runtimeModeLabel || '-');
          modeEl.className = `runtime-mode-indicator${runtimeMode ? ` ${runtimeMode}` : ''}`;
        }
        syncModelDrawerRuntimeState(runtime);

        // Everyone gets title updates (admin or not).
        if (data.current_model) {
          const shortName = getModelDisplayName(data.current_model);
          document.title = shortName;
          if (appTitle) appTitle.innerText = shortName;
        }

      } else {
        modelLoaded = false;
        setText(statusEl, '⏸ Idle');
        setText(modelEl, 'None');
        if (modeEl) {
          setText(modeEl, '-');
          modeEl.className = 'runtime-mode-indicator';
        }
        setText(gpuEl, '');
        setText(cpuEl, '');
        setText(tmpEl, '');
        setText(kEl, '');
        setText(pEl, '');
        setText(rEl, '');
        setText(seedEl, '');

        // Restore default app title/branding
        document.title = pollModelStatus.defaultTitle || "LLM Controller";
        if (appTitle && pollModelStatus.defaultAppTitle) {
          appTitle.innerText = pollModelStatus.defaultAppTitle;
        }
      }
    })
    .catch(console.error);
}

(function startGlobalModelStatusPolling() {
  // Avoid double intervals if scripts.js gets reloaded or called twice
  if (window.__modelStatusPollerStarted) return;
  window.__modelStatusPollerStarted = true;

  const start = () => {
    // call once immediately, then keep in sync
    pollModelStatus();
    setInterval(pollModelStatus, 3000); // 3s is snappy; bump to 5000 if you prefer
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();

function initModelDrawer() {
  // Populate form with saved defaults…
  loadSettings();

  // Populate "Running Model" panel with live data…
  pollModelStatus();
}


function handleEnter(event) {
  if (event.key !== 'Enter' || event.shiftKey) return;

  event.preventDefault();
  sendChat();
}

function appendUserMessage(message, options = {}) {
  const chatMessages = document.getElementById("chatMessages");
  const bubble = document.createElement("div");
  bubble.className = "chat-bubble user";
  if (options.messageId) {
    bubble.dataset.messageId = options.messageId;
  }

  const messageDiv = document.createElement("div");
  messageDiv.className = "message";
  messageDiv.appendChild(document.createTextNode(message));
  bubble.appendChild(messageDiv);

  const actions = document.createElement("div");
  actions.className = "message-actions";

  const editBtn = document.createElement("button");
  editBtn.type = "button";
  editBtn.className = "message-action-btn edit-prompt-btn";
  editBtn.innerText = "Edit Prompt";
  editBtn.style.display = "none";
  editBtn.onclick = function () {
    startPromptEdit(bubble.dataset.messageId || "", messageDiv.textContent || "");
  };
  actions.appendChild(editBtn);

  bubble.appendChild(actions);
  renderUserBubbleAttachments(bubble, options.attachments || []);
  chatMessages.appendChild(bubble);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return bubble;
}

function createBotBubble() {
  const chatMessages = document.getElementById("chatMessages");
  const bubble = document.createElement("div");
  bubble.className = "chat-bubble bot";

  const messageDiv = document.createElement("div");
  messageDiv.className = "message";
  bubble.appendChild(messageDiv);

  const footer = document.createElement("div");
  footer.className = "footer";
  footer.innerText = "Thinking...";
  bubble.appendChild(footer);

  const actions = document.createElement("div");
  actions.className = "message-actions";

  const pager = document.createElement("div");
  pager.className = "variant-pagination";
  pager.style.display = "none";

  const prevBtn = document.createElement("button");
  prevBtn.type = "button";
  prevBtn.className = "message-action-btn variant-prev-btn";
  prevBtn.innerText = "Prev";
  prevBtn.onclick = function () {
    if (bubble.dataset.prevVariantId) {
      selectVariant(bubble.dataset.prevVariantId);
    }
  };
  pager.appendChild(prevBtn);

  const pageLabel = document.createElement("span");
  pageLabel.className = "variant-page-label";
  pager.appendChild(pageLabel);

  const nextBtn = document.createElement("button");
  nextBtn.type = "button";
  nextBtn.className = "message-action-btn variant-next-btn";
  nextBtn.innerText = "Next";
  nextBtn.onclick = function () {
    if (bubble.dataset.nextVariantId) {
      selectVariant(bubble.dataset.nextVariantId);
    }
  };
  pager.appendChild(nextBtn);

  actions.appendChild(pager);

  const stopBtn = document.createElement("button");
  stopBtn.type = "button";
  stopBtn.className = "message-action-btn stop-response-btn";
  stopBtn.innerText = "Stop";
  stopBtn.style.display = "none";
  stopBtn.onclick = function () {
    stopActiveResponse();
  };
  actions.appendChild(stopBtn);

  const regenerateBtn = document.createElement("button");
  regenerateBtn.type = "button";
  regenerateBtn.className = "message-action-btn regenerate-response-btn";
  regenerateBtn.innerText = "Regenerate";
  regenerateBtn.style.display = "none";
  regenerateBtn.onclick = function () {
    if (bubble.dataset.messageId) {
      regenerateResponse(bubble.dataset.messageId);
    }
  };
  actions.appendChild(regenerateBtn);

  bubble.appendChild(actions);

  const thoughtsDiv = document.createElement("div");
  thoughtsDiv.className = "thoughts";
  thoughtsDiv.style.display = "none";
  bubble.appendChild(thoughtsDiv);

  const toggle = document.createElement("div");
  toggle.className = "show-thoughts";
  toggle.innerText = "Show Thoughts";
  toggle.style.display = "none"; // Hide by default until thoughts are available.
  toggle.onclick = function () {
    setThoughtsExpanded(bubble, !bubble._thoughtsExpanded);
  };
  bubble.appendChild(toggle);

  // Streaming buffers
  bubble._streamText = "";
  bubble._thoughtsText = "";
  bubble._thoughtsExpanded = false;

  chatMessages.appendChild(bubble);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return bubble;
}

function enhanceCodeBlocks(messageDiv) {
  const codes = messageDiv.querySelectorAll('pre > code');

  codes.forEach(code => {
    const pre = code.parentElement;

    // --- 0) Ensure wrapper exists: <div class="codeblock"><pre>...</pre></div>
    let wrapper = pre.parentElement;
    if (!wrapper || !wrapper.classList || !wrapper.classList.contains('codeblock')) {
      wrapper = document.createElement('div');
      wrapper.className = 'codeblock';

      // Insert wrapper where <pre> currently is, then move <pre> into it
      pre.parentElement.insertBefore(wrapper, pre);
      wrapper.appendChild(pre);
    }

    // Language label.
    const langClass = Array.from(code.classList).find(c => c.startsWith('language-'));
    if (langClass) {
      const langName = langClass.replace('language-', '');
      const prev = wrapper.previousSibling;
      const hasLabel = prev && prev.classList && prev.classList.contains('code-lang-label');

      if (!hasLabel) {
        const langLabel = document.createElement('div');
        langLabel.className = 'code-lang-label';
        langLabel.innerText = langName;
        wrapper.parentElement.insertBefore(langLabel, wrapper);
      }
    }

    // --- 2) Ensure copy button exists and is OUTSIDE <pre> (inside wrapper)
    let copyBtn = wrapper.querySelector(':scope > .copy-btn');

    // If button exists inside <pre>, move it out
    const insidePreBtn = pre.querySelector('.copy-btn');
    if (!copyBtn && insidePreBtn) {
      copyBtn = insidePreBtn;
      wrapper.insertBefore(copyBtn, pre); // moves it out of the scroll area
    }

    // If no button anywhere, create it (inside wrapper, before <pre>)
    if (!copyBtn) {
      copyBtn = document.createElement('button');
      copyBtn.innerText = "📋 Copy";
      copyBtn.className = "copy-btn";
      wrapper.insertBefore(copyBtn, pre);
    }

    // Always ensure onclick is correct
    copyBtn.onclick = function () {
      copyTextToClipboard(code.innerText, copyBtn);
    };
  });
}

function findBotBubbleByMessageId(messageId) {
  const chatMessages = document.getElementById("chatMessages");
  if (!chatMessages) return null;
  return chatMessages.querySelector(`.chat-bubble.bot[data-message-id="${CSS.escape(messageId)}"]`);
}

function findUserBubbleByMessageId(messageId) {
  const chatMessages = document.getElementById("chatMessages");
  if (!chatMessages) return null;
  return chatMessages.querySelector(`.chat-bubble.user[data-message-id="${CSS.escape(messageId)}"]`);
}

function rollbackPendingSendTurn(messageId) {
  if (!messageId) return false;

  const userBubble = findUserBubbleByMessageId(messageId);
  const botBubble = findBotBubbleByMessageId(messageId);
  const input = document.getElementById("chatInput");
  let restoredMessage = "";

  if (userBubble) {
    const messageDiv = userBubble.querySelector(".message");
    restoredMessage = messageDiv ? (messageDiv.textContent || "") : "";
    userBubble.remove();
  }

  if (botBubble) {
    botBubble.remove();
  }

  if (restoredMessage && input && !input.value.trim()) {
    input.value = restoredMessage;
    autoResize(input);
  }

  updateScrollToBottomButtonVisibility();
  return Boolean(userBubble || botBubble);
}

function ensureBotBubbleForMessageId(messageId, options = {}) {
  const chatMessages = document.getElementById("chatMessages");
  if (!chatMessages) return null;
  const { action = "", sourcePromptId = "" } = options;

  // 1) Already exists
  let bubble = findBotBubbleByMessageId(messageId);
  if (bubble) return bubble;

  // 1b) Mirrored regenerate streams need to retarget the existing latest assistant turn
  // before tokens arrive, because only the submitting viewer has already rebound ids locally.
  if (action === "regenerate" && sourcePromptId) {
    const sourceBubble = findBotBubbleByMessageId(sourcePromptId);
    if (sourceBubble) {
      sourceBubble.dataset.messageId = messageId;
      prepareBubbleForStreaming(sourceBubble, { preserveMessage: true });
      return sourceBubble;
    }
  }

  // 2) If a pending last bot bubble exists with no messageId, claim it
  const lastBot = chatMessages.querySelector(".chat-bubble.bot:last-of-type");
  if (lastBot && !lastBot.dataset.messageId) {
    lastBot.dataset.messageId = messageId;
    prepareBubbleForStreaming(lastBot);
    return lastBot;
  }

  // 3) Otherwise create a new one
  bubble = createBotBubble();
  bubble.dataset.messageId = messageId;
  prepareBubbleForStreaming(bubble);
  return bubble;
}

function isChatSocketEventForActiveSession(data) {
  const sessionId = typeof data?.session_id === "string" ? data.session_id.trim() : "";
  if (!sessionId) return true;
  return Boolean(currentSessionId) && sessionId === currentSessionId;
}

socket.on("receive_message", function (data) {
  const chatMessages = document.getElementById("chatMessages");
  if (!chatMessages) return;
  if (!isChatSocketEventForActiveSession(data)) return;

  const messageId = data.message_id; // Current message contract only.
  if (!messageId) return;
  const incomingAction = typeof data?.action === "string" ? data.action : "";
  const incomingSourcePromptId = typeof data?.source_prompt_id === "string" ? data.source_prompt_id.trim() : "";
  const isLocalOriginStream = Boolean(
    isResponding &&
    window.currentAssistantMessageId &&
    messageId === window.currentAssistantMessageId
  );

  // Only enforce local message ordering while this tab is actively driving a response.
  if (isResponding && window.currentAssistantMessageId && messageId !== window.currentAssistantMessageId) return;

  // Mirrored send/edit flows do not have enough live identity to bind safely in non-origin viewers.
  // Reload once at completion instead of rendering a misleading partial/orphan turn.
  if (!isLocalOriginStream && (incomingAction === "send" || incomingAction === "edit_prompt")) {
    if (data.streaming_done && currentSessionId) {
      pendingReloadCurrent = false;
      loadChat(currentSessionId);
    }
    return;
  }

  const bubble = ensureBotBubbleForMessageId(messageId, {
    action: incomingAction,
    sourcePromptId: incomingSourcePromptId
  });
  if (!bubble) return;

  const thoughtsDiv = bubble.querySelector(".thoughts");
  const footer = bubble.querySelector(".footer");
  const toggle = bubble.querySelector(".show-thoughts");
  const messageDiv = bubble.querySelector(".message");

  // --- STREAM: answer delta (plain text only) ---
  if (typeof data.delta === "string" && data.delta.length) {
    bubble._streamText = (bubble._streamText || "") + data.delta;

    if (messageDiv) {
      const liveText = bubble._streamText.replace(/\n{3,}/g, "\n\n");
      messageDiv.textContent = liveText + "▍";
    }
  }

  // --- STREAM: thoughts delta (show toggle only if non-empty) ---
  if (typeof data.thoughts_delta === "string" && data.thoughts_delta.length) {
    bubble._thoughtsText = (bubble._thoughtsText || "") + data.thoughts_delta;
    updateThoughtsUI(bubble);
  }

  // --- DONE: render markdown ONCE + finalize thoughts + metrics ---
  if (data.streaming_done) {
    const completedMode = currentGenerationMode;
    const shouldRestoreSnapshot = (
      completedMode === "regenerate" &&
      Boolean(data.stopped) &&
      regenerateSnapshots.has(messageId) &&
      !(bubble._streamText || "").trim()
    );
    const shouldRestoreEditedTurn = (
      completedMode === "edit_prompt" &&
      Boolean(data.stopped) &&
      editPromptSnapshots.has(messageId) &&
      !(bubble._streamText || "").trim()
    );

    if (shouldRestoreSnapshot) {
      restoreBotBubbleSnapshot(messageId, bubble);
    } else if (shouldRestoreEditedTurn) {
      restoreEditedTurnSnapshot(messageId);
    } else {
      // Prefer authoritative final text from server, fallback to accumulated
      const finalText = (typeof data.bot_response === "string" ? data.bot_response : (bubble._streamText || ""));

      if (messageDiv) {
        messageDiv.classList.remove("streaming-plain");

        const cleaned = (finalText || "").replace(/\n{3,}/g, "\n\n").trim();
        const standardized = extractAndStandardizeMath(cleaned);

        messageDiv.innerHTML = renderSafeMarkdown(standardized);
        enhanceCodeBlocks(messageDiv);

        if (window.MathJax && window.MathJax.typesetPromise) {
          window.MathJax.typesetPromise([messageDiv]);
        }
      }

      // Finalize thoughts from server (authoritative)
      if (typeof data.thoughts === "string") {
        bubble._thoughtsText = data.thoughts;
        updateThoughtsUI(bubble);
      } else {
        bubble._thoughtsText = "";
        bubble._thoughtsExpanded = false;
        updateThoughtsUI(bubble);
      }

      if (footer) {
        footer.innerText = formatResponseFooter(data);
      }
    }

    regenerateSnapshots.delete(messageId);
    editPromptSnapshots.delete(messageId);
    stopRequestedMessageId = null;
    isResponding = false;
    currentGenerationMode = null;
    if (completedMode === "send" || completedMode === "edit_prompt") {
      clearComposerAttachments();
    }
    refreshComposerButtons();
    renderComposerAttachments();
    refreshAssistantBubbleControls();
    loadChatHistory();
    // Phase 3: apply any queued chat switch after streaming finishes
    if (pendingSessionId && pendingSessionId !== currentSessionId) {
      const next = pendingSessionId;
      pendingSessionId = null;
      requestLoadChat(next, "queued_after_done");
    } else if ((completedMode === "regenerate" || completedMode === "edit_prompt") && currentSessionId) {
      pendingReloadCurrent = false;
      loadChat(currentSessionId);
    } else if (incomingAction === "regenerate" && currentSessionId) {
      pendingReloadCurrent = false;
      loadChat(currentSessionId);
    } else if (pendingReloadCurrent && currentSessionId) {
      pendingReloadCurrent = false;
      // Optional: refresh current chat after done (usually unnecessary now)
      // requestLoadChat(currentSessionId, "queued_refresh_after_done");
    }
  }

  bubble.scrollIntoView({ behavior: "smooth", block: "end" });
});

socket.on("chat_error", function (data) {
  if (!isChatSocketEventForActiveSession(data)) return;

  const messageId = data?.message_id;

  if (data?.reset_state) {
    if (data?.action === "send" && messageId) {
      rollbackPendingSendTurn(messageId);
    }
    if (data?.action === "regenerate" && messageId) {
      restoreBotBubbleSnapshot(messageId);
    }
    if (data?.action === "edit_prompt" && messageId) {
      restoreEditedTurnSnapshot(messageId);
    }

    stopRequestedMessageId = null;
    isResponding = false;
    currentGenerationMode = null;
    isPreparingSend = false;
    refreshComposerButtons();
    renderComposerAttachments();
    refreshAssistantBubbleControls();
  }

  showCustomAlert(data?.message || "An unexpected chat error occurred.");
});

// Show/hide About Modal
function toggleAboutModal(show) {
  const modal = document.getElementById('aboutModal');
  if (show) {
    modal.classList.add('active');
  } else {
    modal.classList.remove('active');
  }
}

// Open modal when About button is clicked
document.getElementById('aboutBtn').onclick = () => toggleAboutModal(true);

// Close modal when clicking outside modal card
function toggleDrawerBar() {
  const drawerBar = document.querySelector('.drawer-bar');
  const chatInput = document.querySelector('.chat-input-container');
  const btn = document.getElementById('toggleDrawerBarBtn');
  const showControlsBar = document.getElementById('showControlsBar');
  if (!drawerBar || !chatInput || !btn || !showControlsBar) return;

  if (drawerBar.classList.contains('hidden')) {
    drawerBar.classList.remove('hidden');
    chatInput.classList.remove('drawer-bar-hidden');
    btn.innerHTML = '▼ Hide Controls';
    if (isMobile()) {
      showControlsBar.style.display = 'none';
    } else {
      showControlsBar.style.display = 'none'; // Explicitly hide on desktop!
    }
  } else {
    drawerBar.classList.add('hidden');
    chatInput.classList.add('drawer-bar-hidden');
    btn.innerHTML = '▲ Show Controls';
    if (isMobile()) {
      showControlsBar.style.display = ''; // Show only on mobile
    } else {
      showControlsBar.style.display = 'none'; // Hide on desktop!
    }
  }
  queueFixedLayoutMetricsUpdate();
}

function showDrawerBar() {
  const drawerBar = document.querySelector('.drawer-bar');
  const chatInput = document.querySelector('.chat-input-container');
  const btn = document.getElementById('toggleDrawerBarBtn');
  const showControlsBar = document.getElementById('showControlsBar');
  if (!drawerBar || !chatInput || !btn || !showControlsBar) return;

  drawerBar.classList.remove('hidden');
  chatInput.classList.remove('drawer-bar-hidden');
  btn.innerHTML = '▼ Hide Controls';
  // Only show the bottom bar on mobile:
  if (isMobile()) {
    showControlsBar.style.display = 'none';
  } else {
    showControlsBar.style.display = 'none';
  }
  queueFixedLayoutMetricsUpdate();
}
window.addEventListener('resize', function() {
  handleDrawerBarOnResize();
});
function handleDrawerBarOnResize() {
  const drawerBar = document.querySelector('.drawer-bar');
  const chatInput = document.querySelector('.chat-input-container');
  const btn = document.getElementById('toggleDrawerBarBtn');
  const showControlsBar = document.getElementById('showControlsBar');
  if (!drawerBar || !chatInput || !btn || !showControlsBar) return;

  if (!isMobile()) {
    // Desktop: always show the drawer bar, hide mobile controls
    drawerBar.classList.remove('hidden');
    chatInput.classList.remove('drawer-bar-hidden');
    btn.innerHTML = '▼ Hide Controls';
    showControlsBar.style.display = 'none';
  } else {
    // Mobile: if drawer bar is visible but the showControlsBar is hidden, show button as needed
    if (drawerBar.classList.contains('hidden')) {
      showControlsBar.style.display = '';
      btn.innerHTML = '▲ Show Controls';
    } else {
      showControlsBar.style.display = 'none';
      btn.innerHTML = '▼ Hide Controls';
    }
  }
  queueFixedLayoutMetricsUpdate();
}
function toggleUserSettingsModal(show) {
  closeAllDrawers();
  // Now open the user settings modal
  document.getElementById('userSettingsModal').style.display = show ? 'flex' : 'none';
}

// EMAIL CHANGE
document.getElementById('changeEmailForm').onsubmit = function(e) {
  e.preventDefault();
  const form = this;
  const csrfField = form.querySelector('input[name="csrf_token"]');
  const csrfToken = csrfField ? csrfField.value : '';
  const data = {
    new_email: form.new_email.value,
    current_password: form.current_password.value
  };
  const alert = document.getElementById('change-email-alert');

  window.ApiHttp.postJSONRequest('/settings/change_email', data, { csrfToken }, "Email update failed.")
    .then((json) => {
      if (json.status === "success") {
        alert.textContent = json.message || "Confirmation email sent. Your current email remains active until confirmed.";
        alert.className = "success";
        form.current_password.value = "";
        return;
      }

      throw new Error("Email update failed.");
    })
    .catch((err) => {
      if (isRedirectingToLoginError(err)) {
        return;
      }
      alert.textContent = getActionErrorMessage("", err, "Email update failed.");
      alert.className = "error";
    });
};

// PASSWORD CHANGE
document.getElementById('changePasswordForm').onsubmit = function(e) {
  e.preventDefault();
  const form = this;
  // Find CSRF field inside the form
  const csrfField = form.querySelector('input[name="csrf_token"]');
  const csrfToken = csrfField ? csrfField.value : '';
  // Collect data from named inputs
  const data = {
    old_password: form.elements["old_password"].value,
    new_password: form.elements["new_password"].value,
    new_password2: form.elements["new_password2"].value
  };
  const alert = document.getElementById('change-password-alert');

  window.ApiHttp.postJSONRequest('/settings/change_password', data, { csrfToken }, "Password change failed.")
    .then((json) => {
      if (json.status === "success") {
        alert.textContent = "Password changed!";
        alert.className = "success";
        form.reset();
        return;
      }

      throw new Error("Password change failed.");
    })
    .catch((err) => {
      if (isRedirectingToLoginError(err)) {
        return;
      }
      alert.textContent = getActionErrorMessage("", err, "Password change failed.");
      alert.className = "error";
      console.error(err);
    });
};
