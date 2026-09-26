document.addEventListener('DOMContentLoaded', function() {
  if (typeof window.initModelDropdownUI === 'function') window.initModelDropdownUI();
  restorePromptDraftAfterReload();
});

const CHAT_PREFIX       = '/chat';
const PROJECTS_PREFIX   = '/projects';
window.MODEL_PREFIX = '/model';
const SETTINGS_PREFIX   = '/settings';
const ANALYTICS_PREFIX  = '/analytics';

const BENCH_PREFIX = '/benchmark';
window.BENCH_PREFIX = BENCH_PREFIX;

window.CHAT_PREFIX = CHAT_PREFIX;
window.SETTINGS_PREFIX = SETTINGS_PREFIX;
window.ANALYTICS_PREFIX = ANALYTICS_PREFIX;

const LEGACY_CHAT_URL_PARAMS = ["session_id", "chat_id"];
const SESSION_PATH_PATTERN = /^(?=.{6,128}$)(?=.*-)(?=.*\d)[A-Za-z0-9_-]+$/;

function normalizeSessionId(value) {
  return String(value || "").trim();
}

function getSessionIdFromUrl() {
  try {
    const pathSessionId = normalizeSessionId(decodeURIComponent((window.location.pathname || "").replace(/^\/+|\/+$/g, "")));
    if (SESSION_PATH_PATTERN.test(pathSessionId)) {
      return pathSessionId;
    }

    const params = new URLSearchParams(window.location.search || "");
    for (const paramName of LEGACY_CHAT_URL_PARAMS) {
      const paramSessionId = normalizeSessionId(params.get(paramName));
      if (paramSessionId) return paramSessionId;
    }
  } catch (_) {
    // Fall through to no URL-selected chat.
  }
  return "";
}

function updateChatSessionUrl(sessionId) {
  if (!window.history || typeof window.history.replaceState !== "function") return;

  try {
    const url = new URL(window.location.href);
    const normalizedSessionId = normalizeSessionId(sessionId);
    if (normalizedSessionId) {
      url.pathname = `/${encodeURIComponent(normalizedSessionId)}`;
    } else {
      url.pathname = "/";
    }
    for (const paramName of LEGACY_CHAT_URL_PARAMS) {
      url.searchParams.delete(paramName);
    }

    const nextUrl = url.toString();
    if (nextUrl !== window.location.href) {
      window.history.replaceState({}, "", nextUrl);
    }
  } catch (_) {
    // URL state is best-effort; chat selection should continue to work without it.
  }
}

function getStoredCurrentSessionId() {
  try {
    return normalizeSessionId(localStorage.getItem("currentSessionId"));
  } catch (_) {
    return "";
  }
}

function storeCurrentSessionId(sessionId) {
  const normalizedSessionId = normalizeSessionId(sessionId);
  try {
    if (normalizedSessionId) {
      localStorage.setItem("currentSessionId", normalizedSessionId);
    } else {
      localStorage.removeItem("currentSessionId");
    }
  } catch (_) {
    // Local storage is only continuity support; the URL remains authoritative on refresh.
  }
}

function setCurrentSessionId(sessionId, options = {}) {
  currentSessionId = normalizeSessionId(sessionId) || null;
  storeCurrentSessionId(currentSessionId);
  if (options.updateUrl !== false) {
    updateChatSessionUrl(currentSessionId);
  }
  highlightActiveChatSession();
}

function clearCurrentSessionId(options = {}) {
  currentSessionId = null;
  storeCurrentSessionId("");
  if (options.updateUrl !== false) {
    updateChatSessionUrl("");
  }
  highlightActiveChatSession();
}

const socket = io({
  path: '/socket.io'
});

window.socket = socket;

let currentSessionId = getSessionIdFromUrl() || getStoredCurrentSessionId() || null;
let isResponding = false;
let pendingSessionId = null;
let pendingReloadCurrent = false;
let modelLoaded = false;
let modelStartInProgress = false;
let pendingModelStartName = "";
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
let chatHistorySessions = [];
let projectRecords = [];
let activeProjectFilter = "all";
let editingProjectId = null;
let assigningProjectSessionId = null;
let projectModalPreviousFocus = null;
let initialUrlSessionHandled = false;

const SUPPORTED_ATTACHMENT_EXTENSIONS = [
  ".txt", ".md", ".py", ".js", ".ts", ".html", ".css", ".json", ".xml",
  ".yaml", ".yml", ".csv", ".log", ".ini", ".cfg", ".bat", ".ps1", ".sh",
  ".sql", ".php", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs"
];
const SUPPORTED_IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".heif", ".avif", ".tif", ".tiff", ".bmp"];
const SUPPORTED_IMAGE_MIME_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif", "image/heif", "image/avif", "image/tiff", "image/bmp"];
const PNG_PREVIEW_MIME_TYPES = ["image/tiff", "image/heif"];
const IMAGE_MIME_ALIASES = {
  "image/heic": "image/heif",
  "image/heic-sequence": "image/heif",
  "image/heif-sequence": "image/heif",
  "image/x-tiff": "image/tiff",
  "image/x-bmp": "image/bmp",
  "image/x-ms-bmp": "image/bmp"
};
const SUPPORTED_DOCUMENT_EXTENSIONS = [
  ".pdf",
  ".docx", ".dotx", ".docm", ".dotm",
  ".pptx", ".potx", ".ppsx", ".pptm", ".potm", ".ppsm",
  ".xlsx", ".xlsm",
  ".odt", ".ott", ".ods", ".ots", ".odp", ".otp",
  ".epub", ".eml", ".msg", ".adoc", ".asciidoc", ".tex", ".latex",
  ".boxnote", ".vtt", ".pages", ".nxml", ".xbrl", ".dclg", ".dclx"
];
let MAX_ATTACHMENT_FILES = 8;
let MAX_ATTACHMENT_FILE_BYTES = 1048576;
let MAX_ATTACHMENT_TOTAL_BYTES = 4194304;
const SOCKET_SEND_MAX_BYTES = 64 * 1024 * 1024;
const SOCKET_SEND_RESERVE_BYTES = 512 * 1024;
const MAX_BINARY_RAW_TRANSPORT_BYTES = Math.floor((SOCKET_SEND_MAX_BYTES - SOCKET_SEND_RESERVE_BYTES) * 3 / 4);

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

function isSupportedImageDataUrl(value) {
  const match = /^data:(image\/[a-z0-9-]+);base64,[a-z0-9+/_=-]+$/i.exec(String(value || ""));
  return Boolean(match && SUPPORTED_IMAGE_MIME_TYPES.includes(normalizeImageMimeType(match[1])));
}

function safeAttachmentBrowserUrl(value, options = {}) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  if (raw.startsWith("blob:")) return raw;
  if (raw.startsWith("/") && !raw.startsWith("//")) return raw;
  if (options.image && isSupportedImageDataUrl(raw)) return raw;
  return "";
}

function normalizeDisplayAttachments(attachments) {
  return (Array.isArray(attachments) ? attachments : []).map((attachment, index) => {
    if (typeof attachment === "string") {
      const name = attachment.trim();
      return name ? { name, kind: "file", mimeType: "", size: null, index } : null;
    }
    if (!attachment || typeof attachment !== "object") return null;

    const name = String(attachment.name || attachment.filename || `Attachment ${index + 1}`).trim();
    const mimeType = normalizeImageMimeType(attachment.mime_type || attachment.mimeType || attachment.type);
    const extension = getAttachmentExtension(name);
    const declaredKind = String(attachment.kind || "").toLowerCase();
    const kind = declaredKind === "image" ||
      mimeType.startsWith("image/") || SUPPORTED_IMAGE_EXTENSIONS.includes(extension)
      ? "image"
      : declaredKind === "document" || SUPPORTED_DOCUMENT_EXTENSIONS.includes(extension)
        ? "document"
      : "file";
    const numericSize = Number(attachment.size);
    const imageUrl = safeAttachmentBrowserUrl(
      attachment.previewUrl || attachment.preview_url || attachment.data_url || attachment.dataUrl || attachment.url,
      { image: true }
    );
    const downloadUrl = safeAttachmentBrowserUrl(attachment.download_url || attachment.downloadUrl || attachment.url);

    return {
      ...attachment,
      name,
      kind,
      mimeType,
      size: Number.isFinite(numericSize) ? numericSize : null,
      imageUrl,
      downloadUrl,
      index
    };
  }).filter(Boolean);
}

async function loadAttachmentPngPreview(attachment) {
  const existing = String(attachment.previewUrl || attachment.preview_url || "");
  if (existing.startsWith("data:image/png;base64,") && isSupportedImageDataUrl(existing)) return existing;
  if (attachment.previewError) throw new Error(attachment.previewError);
  if (attachment.previewRequest) return attachment.previewRequest;

  // Share only the in-flight request across rerenders; the result stays in display state.
  attachment.previewRequest = (async () => {
    let dataUrl = String(attachment.data_url || attachment.dataUrl || "");
    if (!dataUrl && attachment.file) dataUrl = await readFileAsDataUrl(attachment.file);
    dataUrl = canonicalizeImageDataUrlMime(dataUrl, attachment.mimeType || attachment.mime_type);
    const result = await window.ApiHttp.postJSONRequest(
      `${CHAT_PREFIX}/attachment_preview`,
      { data_url: dataUrl },
      {
        cache: "no-store",
        onSessionExpired: () => savePromptDraftForReload(document.getElementById("chatInput")?.value || "")
      },
      "The image preview could not be generated."
    );
    const previewUrl = String(result.preview_url || "");
    if (!previewUrl.startsWith("data:image/png;base64,") || !isSupportedImageDataUrl(previewUrl)) {
      throw new Error("The image preview could not be generated.");
    }
    releaseAttachmentPreview(attachment);
    attachment.previewUrl = previewUrl;
    return previewUrl;
  })().catch((err) => {
    attachment.previewError = "The image preview could not be generated.";
    throw err;
  }).finally(() => {
    delete attachment.previewRequest;
  });
  return attachment.previewRequest;
}

function setAttachmentPreviewSource(image, attachment, source) {
  if (!PNG_PREVIEW_MIME_TYPES.includes(normalizeImageMimeType(attachment.mimeType || attachment.mime_type))) {
    image.src = source;
    return;
  }
  loadAttachmentPngPreview(attachment).then((previewUrl) => {
    attachment.previewUrl = previewUrl;
    image.src = previewUrl;
  }).catch(() => {
    attachment.previewError = "The image preview could not be generated.";
    image.dispatchEvent(new Event("error"));
  }).finally(() => {
    delete attachment.previewRequest;
  });
}

function renderMessageAttachments(bubble, attachments) {
  if (!bubble) return;

  const existing = bubble.querySelector(".turn-attachment-list");
  if (existing) {
    existing.remove();
  }

  const normalized = normalizeDisplayAttachments(attachments);
  bubble._attachments = normalized;
  if (!normalized.length) return;

  const list = document.createElement("div");
  list.className = "turn-attachment-list";

  normalized.forEach((attachment) => {
    const chip = document.createElement(attachment.downloadUrl ? "a" : "span");
    chip.className = `turn-attachment-chip ${attachment.kind === "image" ? "is-image" : "is-file"}`;
    chip.title = attachment.name;
    if (attachment.downloadUrl) {
      chip.href = attachment.downloadUrl;
      chip.target = "_blank";
      chip.rel = "noopener";
    }

    if (attachment.kind === "image" && attachment.imageUrl) {
      const image = document.createElement("img");
      image.className = "turn-attachment-thumbnail";
      setAttachmentPreviewSource(image, attachment, attachment.imageUrl);
      image.alt = attachment.name;
      image.loading = "lazy";
      image.setAttribute("role", "button");
      image.setAttribute("tabindex", "0");
      image.setAttribute("aria-label", `Open image ${attachment.name}`);
      image.title = "Open image viewer";
      const openViewer = (event) => {
        event.preventDefault();
        event.stopPropagation();
        openImageAttachmentViewer(image.getAttribute("src"), attachment.name);
      };
      image.addEventListener("click", openViewer);
      image.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") openViewer(event);
      });
      chip.appendChild(image);
    }

    const label = document.createElement("span");
    label.className = "turn-attachment-name";
    label.innerText = attachment.name;
    chip.appendChild(label);
    const thumbnail = chip.querySelector("img");
    if (thumbnail) thumbnail.addEventListener("error", () => {
      thumbnail.remove();
      const notice = document.createElement("span");
      notice.className = "turn-attachment-name";
      notice.textContent = "Preview unavailable";
      chip.appendChild(notice);
      chip.title = `${attachment.name}: ${attachment.previewError || "preview unavailable in this browser"}`;
    }, { once: true });
    list.appendChild(chip);
  });

  const actions = bubble.querySelector(".message-actions");
  if (actions && actions.parentNode === bubble) {
    bubble.insertBefore(list, actions);
  } else {
    bubble.appendChild(list);
  }
}

let imageLightboxPreviousFocus = null;

function closeImageAttachmentViewer() {
  const modal = document.getElementById("imageLightbox");
  const image = document.getElementById("imageLightboxImage");
  const caption = document.getElementById("imageLightboxTitle");
  if (!modal) return;

  modal.classList.remove("active");
  if (image) {
    image.removeAttribute("src");
    image.alt = "";
  }
  if (caption) caption.textContent = "";

  if (imageLightboxPreviousFocus && document.contains(imageLightboxPreviousFocus)) {
    imageLightboxPreviousFocus.focus();
  }
  imageLightboxPreviousFocus = null;
}

function openImageAttachmentViewer(source, name = "") {
  const modal = document.getElementById("imageLightbox");
  const image = document.getElementById("imageLightboxImage");
  const caption = document.getElementById("imageLightboxTitle");
  if (!modal || !image || !source) return;

  imageLightboxPreviousFocus = document.activeElement;
  image.src = String(source);
  image.alt = String(name || "Attached image");
  if (caption) caption.textContent = String(name || "");
  modal.classList.add("active");

  const closeButton = document.getElementById("imageLightboxClose");
  if (closeButton) closeButton.focus();
}

function initializeImageAttachmentViewer() {
  const modal = document.getElementById("imageLightbox");
  if (!modal || modal.dataset.bound === "true") return;

  modal.dataset.bound = "true";
  const closeButton = document.getElementById("imageLightboxClose");
  if (closeButton) closeButton.addEventListener("click", closeImageAttachmentViewer);
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeImageAttachmentViewer();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && modal.classList.contains("active")) {
      event.preventDefault();
      closeImageAttachmentViewer();
    }
  });
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
    chip.className = `attachment-chip ${attachment.kind === "image" ? "is-image" : "is-file"}`;

    if (attachment.kind === "image" && (attachment.previewUrl || PNG_PREVIEW_MIME_TYPES.includes(attachment.mimeType))) {
      const thumbnail = document.createElement("img");
      thumbnail.className = "attachment-chip-thumbnail";
      setAttachmentPreviewSource(thumbnail, attachment, attachment.previewUrl);
      thumbnail.alt = "";
      if (PNG_PREVIEW_MIME_TYPES.includes(attachment.mimeType)) {
        thumbnail.setAttribute("role", "button");
        thumbnail.setAttribute("tabindex", "0");
        thumbnail.setAttribute("aria-label", `Open image ${attachment.name}`);
        thumbnail.title = "Open image viewer";
        thumbnail.addEventListener("click", () => {
          openImageAttachmentViewer(thumbnail.getAttribute("src"), attachment.name);
        });
        thumbnail.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            openImageAttachmentViewer(thumbnail.getAttribute("src"), attachment.name);
          }
        });
      }
      chip.appendChild(thumbnail);
    }

    const name = document.createElement("span");
    name.className = "attachment-chip-name";
    name.innerText = attachment.name;
    chip.appendChild(name);

    const meta = document.createElement("span");
    meta.className = "attachment-chip-meta";
    const typeLabel = attachment.kind === "image"
      ? String(attachment.mimeType || "image").replace("image/", "").toUpperCase()
      : (getAttachmentExtension(attachment.name).replace(".", "").toUpperCase() || "FILE");
    meta.innerText = [typeLabel, formatAttachmentSize(attachment.size)].filter(Boolean).join(" · ");
    chip.appendChild(meta);
    const thumbnail = chip.querySelector("img");
    if (thumbnail) thumbnail.addEventListener("error", () => {
      thumbnail.remove();
      meta.append(" (no preview)");
      chip.title = `${attachment.name}: ${attachment.previewError || "preview unavailable in this browser"}`;
    }, { once: true });

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "attachment-chip-remove";
    removeBtn.innerText = "×";
    removeBtn.title = `Remove ${attachment.name}`;
    removeBtn.setAttribute("aria-label", `Remove ${attachment.name}`);
    removeBtn.disabled = isPreparingSend || isResponding;
    removeBtn.onclick = function () {
      releaseAttachmentPreview(attachment);
      composerAttachments = composerAttachments.filter((item) => item.id !== attachment.id);
      renderComposerAttachments();
    };
    chip.appendChild(removeBtn);

    preview.appendChild(chip);
  });

  queueFixedLayoutMetricsUpdate();
}

function releaseAttachmentPreview(attachment) {
  const previewUrl = String(attachment?.previewUrl || "");
  if (previewUrl.startsWith("blob:") && window.URL && typeof window.URL.revokeObjectURL === "function") {
    window.URL.revokeObjectURL(previewUrl);
  }
}

function clearComposerAttachments() {
  composerAttachments.forEach(releaseAttachmentPreview);
  composerAttachments = [];
  const attachmentInput = document.getElementById("chatAttachmentInput");
  if (attachmentInput) {
    attachmentInput.value = "";
  }
  renderComposerAttachments();
}

function loadComposerAttachmentsFromRecords(records) {
  const normalized = normalizeDisplayAttachments(records);
  const hydrated = [];

  normalized.forEach((attachment) => {
    if (attachment.kind === "image") {
      const dataUrl = String(attachment.data_url || attachment.dataUrl || attachment.imageUrl || "");
      if (!isSupportedImageDataUrl(dataUrl)) return;
      hydrated.push({
        id: crypto.randomUUID ? crypto.randomUUID() : (Date.now() + "-" + Math.random().toString(16).slice(2)),
        name: attachment.name,
        size: attachment.size || 0,
        lastModified: null,
        kind: "image",
        mimeType: attachment.mimeType || dataUrl.slice(5, dataUrl.indexOf(";")),
        previewUrl: attachment.imageUrl || dataUrl,
        previewRequest: attachment.previewRequest,
        dataUrl,
        file: null
      });
      return;
    }

    if (attachment.kind === "document") return;

    if (typeof attachment.content !== "string") return;
    hydrated.push({
      id: crypto.randomUUID ? crypto.randomUUID() : (Date.now() + "-" + Math.random().toString(16).slice(2)),
      name: attachment.name,
      size: attachment.size || 0,
      lastModified: null,
      kind: "file",
      mimeType: attachment.mimeType || "text/plain",
      previewUrl: "",
      content: attachment.content,
      file: null
    });
  });

  const complete = hydrated.length === normalized.length;
  composerAttachments.forEach(releaseAttachmentPreview);
  composerAttachments = complete ? hydrated : [];
  const attachmentInput = document.getElementById("chatAttachmentInput");
  if (attachmentInput) attachmentInput.value = "";
  renderComposerAttachments();

  return {
    count: composerAttachments.length,
    complete
  };
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

function sanitizeBrowserAttachmentName(value) {
  const raw = String(value || "").replace(/\u0000/g, "").trim();
  const basename = raw.split(/[\\/]/).pop() || "";
  return basename.slice(0, 255);
}

function normalizeImageMimeType(value) {
  const mime = String(value || "").trim().toLowerCase();
  return IMAGE_MIME_ALIASES[mime] || mime;
}

function imageExtensionForMime(mimeType) {
  if (mimeType === "image/png") return ".png";
  if (mimeType === "image/jpeg") return ".jpg";
  if (mimeType === "image/webp") return ".webp";
  if (mimeType === "image/gif") return ".gif";
  if (mimeType === "image/heif") return ".heif";
  if (mimeType === "image/avif") return ".avif";
  if (mimeType === "image/tiff") return ".tiff";
  if (mimeType === "image/bmp") return ".bmp";
  return "";
}

function imageMimeForExtension(extension) {
  if (extension === ".png") return "image/png";
  if (extension === ".jpg" || extension === ".jpeg") return "image/jpeg";
  if (extension === ".webp") return "image/webp";
  if (extension === ".gif") return "image/gif";
  if (extension === ".heic" || extension === ".heif") return "image/heif";
  if (extension === ".avif") return "image/avif";
  if (extension === ".tif" || extension === ".tiff") return "image/tiff";
  if (extension === ".bmp") return "image/bmp";
  return "";
}

function ingestAttachmentFiles(files, options = {}) {
  const pickedFiles = Array.from(files || []).filter(Boolean);
  if (!pickedFiles.length || isPreparingSend || isResponding) return 0;

  let totalBytes = composerAttachments.reduce((sum, attachment) => sum + (attachment.size || 0), 0);
  let totalBinaryBytes = composerAttachments.reduce(
    (sum, attachment) => sum + (["image", "document"].includes(attachment.kind) ? (attachment.size || 0) : 0),
    0
  );
  const rejected = [];
  let accepted = 0;

  for (let index = 0; index < pickedFiles.length; index += 1) {
    const file = pickedFiles[index];
    const browserMime = normalizeImageMimeType(file.type);
    const reportedMime = browserMime === "application/octet-stream" ? "" : browserMime;
    let name = sanitizeBrowserAttachmentName(file.name);
    let extension = getAttachmentExtension(name);
    const imageByMime = SUPPORTED_IMAGE_MIME_TYPES.includes(reportedMime);

    if (imageByMime && !extension) {
      const sourcePrefix = options.source === "paste" ? "pasted-image" : "image";
      name = `${sourcePrefix}-${Date.now()}-${index + 1}${imageExtensionForMime(reportedMime)}`;
      extension = getAttachmentExtension(name);
    }

    const imageByExtension = SUPPORTED_IMAGE_EXTENSIONS.includes(extension);
    const documentByExtension = SUPPORTED_DOCUMENT_EXTENSIONS.includes(extension);
    const expectedImageMime = imageMimeForExtension(extension);
    const kind = imageByExtension ? "image" : (documentByExtension ? "document" : "file");

    if (!name) {
      rejected.push("An attachment has no usable filename.");
      continue;
    }
    if (reportedMime.startsWith("image/") && !imageByMime) {
      rejected.push(`${name} is not a supported image. Use PNG, JPEG, WebP, GIF, HEIC/HEIF, AVIF, TIFF, or BMP.`);
      continue;
    }
    if (kind === "image" && reportedMime && (!imageByMime || reportedMime !== expectedImageMime)) {
      rejected.push(`${name} has an image type that does not match its filename.`);
      continue;
    }
    if (kind !== "image" && imageByMime) {
      rejected.push(`${name} needs a supported image filename extension: ${SUPPORTED_IMAGE_EXTENSIONS.join(", ")}.`);
      continue;
    }
    if (kind === "file" && !SUPPORTED_ATTACHMENT_EXTENSIONS.includes(extension)) {
      rejected.push(`${name} is not a supported text/code file, image, or document.`);
      continue;
    }

    if (file.size <= 0) {
      rejected.push(`${name} is empty.`);
      continue;
    }

    if (file.size > MAX_ATTACHMENT_FILE_BYTES) {
      rejected.push(`${name} exceeds the ${Math.round(MAX_ATTACHMENT_FILE_BYTES / 1024)} KB per-file limit.`);
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
    if (["image", "document"].includes(kind) && (totalBinaryBytes + file.size) > MAX_BINARY_RAW_TRANSPORT_BYTES) {
      rejected.push("Combined images and documents are too large for the chat transport. Remove an attachment or use smaller files.");
      break;
    }

    let previewUrl = "";
    if (kind === "image" && window.URL && typeof window.URL.createObjectURL === "function") {
      previewUrl = window.URL.createObjectURL(file);
    }

    composerAttachments.push({
      id: crypto.randomUUID ? crypto.randomUUID() : (Date.now() + "-" + Math.random().toString(16).slice(2)),
      name,
      size: file.size,
      lastModified: file.lastModified,
      kind,
      mimeType: kind === "image"
        ? (reportedMime || expectedImageMime)
        : (kind === "document" ? (reportedMime || "application/octet-stream") : (reportedMime || "text/plain")),
      previewUrl,
      file
    });
    totalBytes += file.size;
    if (["image", "document"].includes(kind)) totalBinaryBytes += file.size;
    accepted += 1;
  }

  renderComposerAttachments();

  if (rejected.length) {
    showCustomAlert(rejected.join("\n"));
  }
  return accepted;
}

function handleAttachmentSelection(event) {
  const input = event.target;
  ingestAttachmentFiles(input?.files || [], { source: "picker" });
  if (input) input.value = "";
}

window.ingestAttachmentFiles = ingestAttachmentFiles;

function dataTransferHasFiles(dataTransfer) {
  return Array.from(dataTransfer?.types || []).includes("Files") ||
    Array.from(dataTransfer?.items || []).some(item => item.kind === "file");
}

function readFileAsDataUrl(file, errorMessage) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error(errorMessage || "The attachment could not be read."));
    reader.readAsDataURL(file);
  });
}

function canonicalizeImageDataUrlMime(dataUrl, mimeType) {
  const raw = String(dataUrl || "").trim();
  const canonicalMime = normalizeImageMimeType(mimeType);
  const separatorIndex = raw.toLowerCase().indexOf(";base64,");
  if (!raw.toLowerCase().startsWith("data:") || separatorIndex < 5 || !SUPPORTED_IMAGE_MIME_TYPES.includes(canonicalMime)) {
    return raw;
  }
  return `data:${canonicalMime};base64,${raw.slice(separatorIndex + ";base64,".length)}`;
}

async function readFileAsBase64(file) {
  const dataUrl = await readFileAsDataUrl(file, "The document could not be read.");
  const separatorIndex = dataUrl.indexOf(";base64,");
  if (separatorIndex < 0) throw new Error("The document could not be encoded.");
  const dataBase64 = dataUrl.slice(separatorIndex + ";base64,".length);
  if (!dataBase64) throw new Error("The document is empty.");
  return dataBase64;
}

async function buildAttachmentPayload() {
  const payload = [];
  const binaryBytes = composerAttachments.reduce(
    (sum, attachment) => sum + (["image", "document"].includes(attachment.kind) ? (attachment.size || 0) : 0),
    0
  );
  if (binaryBytes > MAX_BINARY_RAW_TRANSPORT_BYTES) {
    throw new Error("Combined images and documents are too large for the chat transport. Remove an attachment or use smaller files.");
  }
  for (const attachment of composerAttachments) {
    if (attachment.kind === "image") {
      let dataUrl = String(attachment.dataUrl || "");
      if (!dataUrl && attachment.file) {
        try {
          dataUrl = await readFileAsDataUrl(attachment.file);
        } catch (err) {
          throw new Error(`Could not read ${attachment.name} as an image.`);
        }
      }
      dataUrl = canonicalizeImageDataUrlMime(dataUrl, attachment.mimeType);
      if (!isSupportedImageDataUrl(dataUrl)) {
        throw new Error(`${attachment.name} is not a supported PNG, JPEG, WebP, GIF, HEIC/HEIF, AVIF, TIFF, or BMP image.`);
      }
      payload.push({
        name: attachment.name,
        size: attachment.size,
        kind: "image",
        mime_type: attachment.mimeType,
        data_url: dataUrl
      });
      continue;
    }

    if (attachment.kind === "document") {
      let dataBase64 = String(attachment.dataBase64 || "");
      if (!dataBase64 && attachment.file) {
        try {
          dataBase64 = await readFileAsBase64(attachment.file);
        } catch (err) {
          throw new Error(`Could not read ${attachment.name} as a document.`);
        }
      }
      if (!dataBase64) {
        throw new Error(`${attachment.name} is empty.`);
      }
      payload.push({
        name: attachment.name,
        size: attachment.size,
        kind: "document",
        mime_type: attachment.mimeType,
        data_base64: dataBase64
      });
      continue;
    }

    let text = typeof attachment.content === "string" ? attachment.content : "";
    if (!text && attachment.file) {
      try {
        text = await attachment.file.text();
      } catch (err) {
        throw new Error(`Could not read ${attachment.name} as text.`);
      }
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
      kind: "file",
      mime_type: attachment.mimeType,
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

document.addEventListener('visibilitychange', handleVisibilityOrFocusReturn);
window.addEventListener('focus', handleVisibilityOrFocusReturn);

function handleVisibilityOrFocusReturn() {
  if (!(document.visibilityState === "visible" || document.hasFocus())) return;

  if (typeof socket?.connected === "boolean" && !socket.connected) {
    socket.connect();
  }
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

      const modalHTML = `
        <h3 style="margin-top:0;">Debug Output</h3>
        <pre id="debugModalPre" style="max-height:600px; overflow:auto; background:#222; color:#fff; padding:10px; border-radius:8px; text-align:left; font-size:14px;">${escapeHtml(outputText)}</pre>
        <button id="copyDebugModalBtn" style="margin-top:10px;">📋 Copy All Text</button>
        <div style="margin-top:10px; font-size:12px; color:#666;">(Click anywhere outside to close)</div>
      `;
      showCustomAlert(modalHTML, true);

      setTimeout(() => {
        const btn = document.getElementById("copyDebugModalBtn");
        const pre = document.getElementById("debugModalPre");
        if (btn && pre) {
          btn.onclick = function (event) {
            event.preventDefault();
            event.stopPropagation();
            copyTextToClipboard(pre.textContent || "", btn);
          };
        }
      }, 10);

    })
    .catch(err => {
      showCustomAlert("❌ Error: " + err);
    });
}

async function createNewSession(options = {}, projectId = null) {
  const normalizedProjectId = normalizeProjectId(projectId);
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

    setCurrentSessionId(data.session_id);
    syncSocketChatSessionSubscription(currentSessionId);
    if (normalizedProjectId) {
      await window.ApiHttp.postJSONRequest(
        `${PROJECTS_PREFIX}/assign_session`,
        { session_id: currentSessionId, project_id: normalizedProjectId },
        options,
        "Conversation could not be assigned to the Project."
      );
    }
    setActiveProjectFilter(normalizedProjectId ? projectFilterKey(normalizedProjectId) : "all");
    loadChatHistory();
    return currentSessionId;
  } catch (err) {
    throw err;
  }
}

async function startNewChat(project = null) {
  if (isResponding || isPreparingSend) {
    showCustomAlert("Wait for the current response to finish, or stop it, before starting a new chat.");
    return;
  }
  const projectId = normalizeProjectId(project?.id);
  clearPromptEditState({ clearInput: true });
  clearComposerAttachments();
  syncSocketChatSessionSubscription("");
  clearCurrentSessionId();

  if (projectId) {
    setActiveProjectFilter(projectFilterKey(projectId));
  }

  try {
    await createNewSession({}, projectId);
    document.getElementById("chatMessages").innerHTML = "";
    closeSidebarOnMobile();
  } catch (err) {
    if (isRedirectingToLoginError(err)) {
      return;
    }
    showCustomAlert(getActionErrorMessage(
      "",
      err,
      projectId ? "Could not create a new chat in this Project." : "Could not create a new chat."
    ));
    console.error(err);
  }
}

function normalizeProjectId(value) {
  const parsed = Number(String(value ?? "").trim());
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function projectFilterKey(projectId) {
  return `project:${projectId}`;
}

function getProjectById(projectId) {
  const normalizedId = normalizeProjectId(projectId);
  return projectRecords.find(project => normalizeProjectId(project.id) === normalizedId) || null;
}

function getActiveProject() {
  if (!activeProjectFilter.startsWith("project:")) return null;
  return getProjectById(activeProjectFilter.slice("project:".length));
}

function projectConversationCount(projectId) {
  const normalizedId = normalizeProjectId(projectId);
  return chatHistorySessions.filter(session => normalizeProjectId(session.project_id) === normalizedId).length;
}

function unassignedConversationCount() {
  return chatHistorySessions.filter(session => normalizeProjectId(session.project_id) === null).length;
}

function setActiveProjectFilter(filterKey) {
  const requestedFilter = String(filterKey || "all");
  if (requestedFilter.startsWith("project:") && !getProjectById(requestedFilter.slice("project:".length))) {
    activeProjectFilter = "all";
  } else if (requestedFilter === "unassigned") {
    activeProjectFilter = "unassigned";
  } else if (requestedFilter.startsWith("project:")) {
    activeProjectFilter = requestedFilter;
  } else {
    activeProjectFilter = "all";
  }
  renderProjectList();
  displayGroupedSessions(chatHistorySessions);
}

function createProjectFilterRow(filterKey, label, count, project = null) {
  const row = document.createElement("li");
  row.className = "project-row";
  row.classList.toggle("active", activeProjectFilter === filterKey);
  row.setAttribute("role", "button");
  row.setAttribute("tabindex", "0");

  const name = document.createElement("span");
  name.className = "project-name";
  name.textContent = label;
  name.title = label;
  row.appendChild(name);

  const countLabel = document.createElement("span");
  countLabel.className = "project-count";
  countLabel.textContent = String(count);
  countLabel.setAttribute("aria-label", `${count} conversations`);

  const selectFilter = () => setActiveProjectFilter(filterKey);
  row.addEventListener("click", selectFilter);
  row.addEventListener("keydown", event => {
    if (event.target !== row) return;
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    selectFilter();
  });

  if (project) {
    const newChatButton = document.createElement("button");
    newChatButton.type = "button";
    newChatButton.textContent = "➕";
    newChatButton.title = `New chat in project ${project.name}`;
    newChatButton.setAttribute("aria-label", newChatButton.title);
    newChatButton.addEventListener("click", event => {
      event.stopPropagation();
      startNewChat(project);
    });
    row.appendChild(newChatButton);
  }

  row.appendChild(countLabel);

  if (project) {
    const actions = document.createElement("div");
    actions.className = "sidebar-row-actions";

    const editButton = document.createElement("button");
    editButton.type = "button";
    editButton.textContent = "✏️";
    editButton.title = `Edit Project ${project.name}`;
    editButton.setAttribute("aria-label", editButton.title);
    editButton.addEventListener("click", event => {
      event.stopPropagation();
      openProjectEditor(project);
    });

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.textContent = "❌";
    deleteButton.title = `Delete Project ${project.name}`;
    deleteButton.setAttribute("aria-label", deleteButton.title);
    deleteButton.addEventListener("click", event => {
      event.stopPropagation();
      deleteProject(project);
    });

    actions.appendChild(editButton);
    actions.appendChild(deleteButton);
    row.appendChild(actions);
  }

  return row;
}

function renderProjectList() {
  const list = document.getElementById("projectList");
  if (!list) return;

  if (activeProjectFilter.startsWith("project:") && !getActiveProject()) {
    activeProjectFilter = "all";
  }

  list.innerHTML = "";
  list.appendChild(createProjectFilterRow("all", "All Chats", chatHistorySessions.length));
  list.appendChild(createProjectFilterRow("unassigned", "Unassigned", unassignedConversationCount()));

  projectRecords.forEach(project => {
    list.appendChild(createProjectFilterRow(
      projectFilterKey(project.id),
      String(project.name || "Untitled Project"),
      projectConversationCount(project.id),
      project
    ));
  });

  const context = document.getElementById("chatHistoryContext");
  if (context) {
    if (activeProjectFilter === "unassigned") {
      context.textContent = "Unassigned Chats";
    } else if (getActiveProject()) {
      context.textContent = getActiveProject().name;
    } else {
      context.textContent = "All Chats";
    }
    context.title = context.textContent;
  }
}

async function loadProjects() {
  try {
    const data = await window.ApiHttp.requestJSON(
      `${PROJECTS_PREFIX}/list`,
      { method: "GET", cache: "no-store", headers: { "Accept": "application/json" } },
      "Projects could not be loaded."
    );
    projectRecords = Array.isArray(data.projects) ? data.projects : [];
    renderProjectList();
    displayGroupedSessions(chatHistorySessions);
    return data;
  } catch (err) {
    console.error(err);
    renderProjectList();
    return { status: "error", projects: [] };
  }
}

function closeProjectModal(modalName) {
  const modalId = modalName === "assignment" ? "projectAssignmentModal" : "projectEditorModal";
  const modal = document.getElementById(modalId);
  if (modal) modal.classList.remove("active");
  if (modalName === "assignment") {
    assigningProjectSessionId = null;
  } else {
    editingProjectId = null;
  }
  if (projectModalPreviousFocus && document.contains(projectModalPreviousFocus)) {
    projectModalPreviousFocus.focus();
  }
  projectModalPreviousFocus = null;
}

function openProjectEditor(project = null) {
  const modal = document.getElementById("projectEditorModal");
  const title = document.getElementById("projectEditorTitle");
  const idInput = document.getElementById("projectEditorId");
  const nameInput = document.getElementById("projectNameInput");
  const instructionsInput = document.getElementById("projectInstructionsInput");
  const error = document.getElementById("projectEditorError");
  if (!modal || !nameInput || !instructionsInput) return;

  projectModalPreviousFocus = document.activeElement;
  editingProjectId = project ? normalizeProjectId(project.id) : null;
  if (title) title.textContent = editingProjectId ? "Edit Project" : "Create Project";
  if (idInput) idInput.value = editingProjectId ? String(editingProjectId) : "";
  nameInput.value = project ? String(project.name || "") : "";
  instructionsInput.value = project ? String(project.instructions || "") : "";
  if (error) error.textContent = "";
  modal.classList.add("active");
  nameInput.focus();
}

function openProjectAssignment(sessionRecord) {
  const modal = document.getElementById("projectAssignmentModal");
  const select = document.getElementById("projectAssignmentSelect");
  const chatName = document.getElementById("projectAssignmentChatName");
  const error = document.getElementById("projectAssignmentError");
  if (!modal || !select || !sessionRecord) return;

  projectModalPreviousFocus = document.activeElement;
  assigningProjectSessionId = normalizeSessionId(sessionRecord.session_id);
  select.innerHTML = "";

  const unassignedOption = document.createElement("option");
  unassignedOption.value = "";
  unassignedOption.textContent = "Unassigned";
  select.appendChild(unassignedOption);

  projectRecords.forEach(project => {
    const option = document.createElement("option");
    option.value = String(project.id);
    option.textContent = project.name;
    select.appendChild(option);
  });

  const currentProjectId = normalizeProjectId(sessionRecord.project_id);
  select.value = currentProjectId ? String(currentProjectId) : "";
  if (chatName) chatName.textContent = sessionRecord.session_name || "New Chat";
  if (error) error.textContent = "";
  modal.classList.add("active");
  select.focus();
}

async function saveProject(event) {
  event.preventDefault();
  const nameInput = document.getElementById("projectNameInput");
  const instructionsInput = document.getElementById("projectInstructionsInput");
  const saveButton = document.getElementById("projectEditorSaveButton");
  const error = document.getElementById("projectEditorError");
  const name = String(nameInput?.value || "").trim();
  if (!name) {
    if (error) error.textContent = "Project name is required.";
    nameInput?.focus();
    return;
  }

  const isEditing = Boolean(editingProjectId);
  const payload = {
    name,
    instructions: String(instructionsInput?.value || "")
  };
  if (isEditing) payload.project_id = editingProjectId;

  if (saveButton) saveButton.disabled = true;
  if (error) error.textContent = "";
  try {
    await window.ApiHttp.postJSONRequest(
      `${PROJECTS_PREFIX}/${isEditing ? "update" : "create"}`,
      payload,
      {},
      `Project could not be ${isEditing ? "updated" : "created"}.`
    );
    closeProjectModal("editor");
    await loadProjects();
  } catch (err) {
    if (error) error.textContent = getAsyncRequestErrorMessage(err, "Project could not be saved.");
  } finally {
    if (saveButton) saveButton.disabled = false;
  }
}

async function deleteProject(project) {
  if (!project || !confirm(`Delete Project "${project.name}"? Its conversations will remain under Unassigned.`)) {
    return;
  }

  try {
    await window.ApiHttp.postJSONRequest(
      `${PROJECTS_PREFIX}/delete`,
      { project_id: project.id },
      {},
      "Project could not be deleted."
    );
    if (activeProjectFilter === projectFilterKey(project.id)) {
      activeProjectFilter = "unassigned";
    }
    await Promise.all([loadProjects(), loadChatHistory()]);
  } catch (err) {
    showCustomAlert(getAsyncRequestErrorMessage(err, "Project could not be deleted."));
  }
}

async function saveProjectAssignment(event) {
  event.preventDefault();
  const select = document.getElementById("projectAssignmentSelect");
  const saveButton = document.getElementById("projectAssignmentSaveButton");
  const error = document.getElementById("projectAssignmentError");
  if (!assigningProjectSessionId || !select) return;

  const projectId = normalizeProjectId(select.value);
  if (saveButton) saveButton.disabled = true;
  if (error) error.textContent = "";
  try {
    await window.ApiHttp.postJSONRequest(
      `${PROJECTS_PREFIX}/assign_session`,
      { session_id: assigningProjectSessionId, project_id: projectId },
      {},
      "Conversation could not be moved."
    );
    closeProjectModal("assignment");
    await Promise.all([loadChatHistory(), loadProjects()]);
  } catch (err) {
    if (error) error.textContent = getAsyncRequestErrorMessage(err, "Conversation could not be moved.");
  } finally {
    if (saveButton) saveButton.disabled = false;
  }
}

function initializeProjectControls() {
  const createButton = document.getElementById("createProjectButton");
  const editorForm = document.getElementById("projectEditorForm");
  const assignmentForm = document.getElementById("projectAssignmentForm");
  if (createButton) createButton.addEventListener("click", () => openProjectEditor());
  if (editorForm) editorForm.addEventListener("submit", saveProject);
  if (assignmentForm) assignmentForm.addEventListener("submit", saveProjectAssignment);

  document.querySelectorAll("[data-project-modal-close]").forEach(button => {
    button.addEventListener("click", () => closeProjectModal(button.dataset.projectModalClose));
  });

  ["projectEditorModal", "projectAssignmentModal"].forEach(modalId => {
    const modal = document.getElementById(modalId);
    if (!modal) return;
    modal.addEventListener("click", event => {
      if (event.target === modal) {
        closeProjectModal(modalId === "projectAssignmentModal" ? "assignment" : "editor");
      }
    });
  });

  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    if (document.getElementById("projectAssignmentModal")?.classList.contains("active")) {
      closeProjectModal("assignment");
    } else if (document.getElementById("projectEditorModal")?.classList.contains("active")) {
      closeProjectModal("editor");
    }
  });

  renderProjectList();
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

function getChatHistorySearchTerm() {
  const input = document.getElementById("chatHistorySearch");
  return input ? String(input.value || "").trim().toLowerCase() : "";
}

function getFilteredChatSessions(sessions) {
  let visibleSessions = sessions || [];
  if (activeProjectFilter === "unassigned") {
    visibleSessions = visibleSessions.filter(session => normalizeProjectId(session.project_id) === null);
  } else if (activeProjectFilter.startsWith("project:")) {
    const activeProjectId = normalizeProjectId(activeProjectFilter.slice("project:".length));
    visibleSessions = visibleSessions.filter(session => normalizeProjectId(session.project_id) === activeProjectId);
  }

  const term = getChatHistorySearchTerm();
  if (!term) return visibleSessions;

  return visibleSessions.filter(session => {
    const name = String(session.session_name || "").toLowerCase();
    const sessionId = String(session.session_id || "").toLowerCase();
    return name.includes(term) || sessionId.includes(term);
  });
}

function getEmptyChatHistoryMessage() {
  if (getChatHistorySearchTerm()) return "No matching chats";
  if (activeProjectFilter === "unassigned") return "No unassigned chats";
  if (getActiveProject()) return "No chats in this Project";
  return "No chats yet";
}

function sessionExistsInHistory(sessionId, sessions = chatHistorySessions) {
  const normalizedSessionId = normalizeSessionId(sessionId);
  if (!normalizedSessionId) return false;
  return (sessions || []).some(session => normalizeSessionId(session.session_id) === normalizedSessionId);
}

function highlightActiveChatSession() {
  const chatList = document.getElementById("chatHistory");
  if (!chatList) return;

  chatList.querySelectorAll(".chat-session-row").forEach(row => {
    row.classList.toggle("active", normalizeSessionId(row.dataset.sessionId) === normalizeSessionId(currentSessionId));
  });
}

function handleInitialChatUrlState(sessions) {
  if (initialUrlSessionHandled) return;
  initialUrlSessionHandled = true;

  const urlSessionId = getSessionIdFromUrl();
  if (!urlSessionId) return;

  if (!sessionExistsInHistory(urlSessionId, sessions)) {
    if (normalizeSessionId(currentSessionId) === urlSessionId) {
      clearCurrentSessionId();
    } else {
      updateChatSessionUrl("");
    }
    return;
  }

  requestLoadChat(urlSessionId, "initial_url");
}

function closeSidebarOnMobile() {
  if (window.innerWidth <= 1080) {
    document.querySelector('.sidebar').classList.remove('open');
    updateChatForSidebar();
  }
}

function displayGroupedSessions(sessions) {
  const visibleSessions = getFilteredChatSessions(sessions || []);
  const grouped = groupSessions(visibleSessions);
  const chatList = document.getElementById("chatHistory");
  chatList.innerHTML = "";
  let renderedCount = 0;

  ["Today", "Yesterday", "Beyond"].forEach(category => {
    if (grouped[category].length > 0) {
      const header = document.createElement("H5");
      header.innerHTML = `<strong>${category}</strong>`;
      header.style.cursor = "default";
      header.style.padding = "5px 10px";
      chatList.appendChild(header);
      
      grouped[category].forEach(session => {
        const li = document.createElement("li");
        li.className = "chat-session-row";
        li.dataset.sessionId = session.session_id;
        li.classList.toggle("active", normalizeSessionId(session.session_id) === normalizeSessionId(currentSessionId));
        const span = document.createElement("span");
        span.textContent = session.session_name || "New Chat";
        span.title = span.textContent;
        li.onclick = () => {
          requestLoadChat(session.session_id, "sidebar_click");
          closeSidebarOnMobile();
        };        
          
        const renameBtn = document.createElement("button");
        renameBtn.type = "button";
        renameBtn.title = "Rename chat";
        renameBtn.setAttribute("aria-label", "Rename chat");
        renameBtn.textContent = "✏️";
        renameBtn.onclick = (event) => {
          event.stopPropagation();
          renameSession(session.session_id, session.session_name);
        };

        const deleteBtn = document.createElement("button");
        deleteBtn.type = "button";
        deleteBtn.title = "Delete chat";
        deleteBtn.setAttribute("aria-label", "Delete chat");
        deleteBtn.textContent = "❌";
        deleteBtn.onclick = (event) => {
          event.stopPropagation();
          deleteSession(session.session_id);
        };

        const actions = document.createElement("div");
        actions.className = "sidebar-row-actions";
        const projectBtn = document.createElement("button");
        projectBtn.type = "button";
        projectBtn.title = "Move conversation to a Project";
        projectBtn.setAttribute("aria-label", "Move conversation to a Project");
        projectBtn.textContent = "\uD83D\uDCC1";
        projectBtn.onclick = (event) => {
          event.stopPropagation();
          openProjectAssignment(session);
        };

        actions.appendChild(projectBtn);
        actions.appendChild(renameBtn);
        actions.appendChild(deleteBtn);

        li.appendChild(span);
        li.appendChild(actions);

        chatList.appendChild(li);
        renderedCount += 1;
      });
    }
  });

  if (renderedCount === 0) {
    const empty = document.createElement("li");
    empty.className = "chat-history-empty";
    empty.textContent = getEmptyChatHistoryMessage();
    chatList.appendChild(empty);
  }
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
    attachments: Array.isArray(bubble._attachments) ? bubble._attachments.map(attachment => ({ ...attachment })) : []
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
  renderMessageAttachments(userBubble, snapshot.userSnapshot.attachments || []);
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
    clearComposerAttachments();
    clearPromptEditState();
    return;
  }

  const input = document.getElementById("chatInput");
  const sendButton = document.getElementById("sendButton");
  if (!input || !sendButton) return;

  closeOpenDrawerWithTogglePath();

  const userBubble = findUserBubbleByMessageId(messageId);
  const attachmentState = loadComposerAttachmentsFromRecords(userBubble?._attachments || []);

  pendingPromptEdit = {
    sourceMessageId: messageId,
    preserveStoredAttachments: !attachmentState.complete
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
      syncSocketChatSessionSubscription(currentSessionId);
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
  const chatInput = document.getElementById("chatInput");
  const chatInputContainer = document.querySelector(".chat-input-container");
  const attachFilesBtn = document.getElementById("attachFilesBtn");
  const chatAttachmentInput = document.getElementById("chatAttachmentInput");
  const chatContainer = document.querySelector(".chat-container");
  const scrollToBottomBtn = document.getElementById("scrollToBottomBtn");
  const chatHistorySearch = document.getElementById("chatHistorySearch");

  if (chatMessages && chatMessages.children.length === 0 && !getSessionIdFromUrl()) {
    clearCurrentSessionId({ updateUrl: false });
  }
  if (chatHistorySearch) {
    chatHistorySearch.addEventListener("input", () => {
      displayGroupedSessions(chatHistorySessions);
    });
  }
  if (chatAttachmentInput) {
    chatAttachmentInput.accept = [
      ...SUPPORTED_ATTACHMENT_EXTENSIONS,
      ...SUPPORTED_IMAGE_EXTENSIONS,
      ...SUPPORTED_IMAGE_MIME_TYPES,
      ...Object.keys(IMAGE_MIME_ALIASES),
      ...SUPPORTED_DOCUMENT_EXTENSIONS
    ].join(",");
    chatAttachmentInput.addEventListener("change", handleAttachmentSelection);
  }
  if (attachFilesBtn && chatAttachmentInput) {
    attachFilesBtn.addEventListener("click", () => {
      if (isPreparingSend || isResponding) return;
      chatAttachmentInput.click();
    });
  }
  if (chatInput) {
    chatInput.addEventListener("paste", (event) => {
      const pastedFiles = Array.from(event.clipboardData?.items || [])
        .filter(item => item.kind === "file")
        .map(item => item.getAsFile())
        .filter(Boolean);
      if (!pastedFiles.length) return;
      const accepted = ingestAttachmentFiles(pastedFiles, { source: "paste" });
      if (accepted > 0) {
        const pastedText = event.clipboardData?.getData("text/plain") || "";
        if (pastedText) {
          const start = Number.isInteger(chatInput.selectionStart) ? chatInput.selectionStart : chatInput.value.length;
          const end = Number.isInteger(chatInput.selectionEnd) ? chatInput.selectionEnd : start;
          if (typeof chatInput.setRangeText === "function") {
            chatInput.setRangeText(pastedText, start, end, "end");
          } else {
            chatInput.value = `${chatInput.value.slice(0, start)}${pastedText}${chatInput.value.slice(end)}`;
            const cursor = start + pastedText.length;
            if (typeof chatInput.setSelectionRange === "function") chatInput.setSelectionRange(cursor, cursor);
          }
          chatInput.dispatchEvent(new Event("input", { bubbles: true }));
        }
        event.preventDefault();
      }
    });
  }
  if (chatInputContainer) {
    let dragDepth = 0;
    chatInputContainer.addEventListener("dragenter", (event) => {
      if (!dataTransferHasFiles(event.dataTransfer)) return;
      event.preventDefault();
      dragDepth += 1;
      chatInputContainer.classList.add("is-file-dragover");
    });
    chatInputContainer.addEventListener("dragover", (event) => {
      if (!dataTransferHasFiles(event.dataTransfer)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    });
    chatInputContainer.addEventListener("dragleave", (event) => {
      dragDepth = Math.max(0, dragDepth - 1);
      if (dragDepth === 0) chatInputContainer.classList.remove("is-file-dragover");
    });
    chatInputContainer.addEventListener("drop", (event) => {
      if (!dataTransferHasFiles(event.dataTransfer)) return;
      event.preventDefault();
      dragDepth = 0;
      chatInputContainer.classList.remove("is-file-dragover");
      ingestAttachmentFiles(event.dataTransfer?.files || [], { source: "drop" });
    });
  }
  initializeCpuOnlyToggle();
  initializeApiAccessControls();
  initializeImageAttachmentViewer();
  initializeProjectControls();
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
    if (window.innerWidth <= 1080) {
      if (document.getElementById("adminDrawer")?.classList.contains("open")) {
        clearApiKeyReveal();
      }
      document.querySelectorAll('.drawer.open').forEach(drawer => {
        drawer.classList.remove('open');
      });
    }
  });  

  const newChatButton = document.querySelector(".new-chat");
  if (newChatButton) {
    newChatButton.addEventListener("click", () => startNewChat());
  } else {
    console.warn("New Chat button not found.");
  }

  loadSettings();

  loadProjects();
  loadChatHistory();
  initModelDrawer();
});

socket.on("connect", function () {
  if (socketJoinedChatSessionId) {
    const joinedSessionId = socketJoinedChatSessionId;
    socketJoinedChatSessionId = null;
    syncSocketChatSessionSubscription(joinedSessionId);
  }
});

socket.on("update_session_name", function() {
  loadChatHistory();
});

function toggleDrawer(drawerId) {
  const allDrawers = document.querySelectorAll('.drawer');
  const drawer = document.getElementById(drawerId);
  const adminDrawer = document.getElementById('adminDrawer');
  const chatInput = document.querySelector('.chat-input-container');
  const drawerBar = document.querySelector('.drawer-bar');

  if (!drawer) return;

  if (drawerId === 'adminDrawer' || adminDrawer?.classList.contains('open')) {
    clearApiKeyReveal();
  }

  if (drawer.classList.contains('open')) {
    allDrawers.forEach(d => d.classList.remove('open'));
    if (chatInput) chatInput.style.display = '';
    queueFixedLayoutMetricsUpdate();
    updateScrollToBottomButtonVisibility();
    return;
  }

  allDrawers.forEach(d => d.classList.remove('open'));
  drawer.classList.add('open');
  if (chatInput) chatInput.style.display = 'none';

  if (drawerId === "adminDrawer") {
    if (typeof loadSettings === "function") loadSettings();
  }
  if (drawerId === "analyticsDrawer" && typeof window.fetchAnalytics === "function") window.fetchAnalytics();
  if (drawerId === "modelDrawer" && typeof loadModelDropdown === "function") {
    loadModelDropdown();
    syncCpuOnlyToggleFromGpuLayers();
    pollModelStatus();
  }

  queueFixedLayoutMetricsUpdate();
  updateScrollToBottomButtonVisibility();
}

function closeAllDrawers() {
  if (document.getElementById('adminDrawer')?.classList.contains('open')) {
    clearApiKeyReveal();
  }
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
  const warning = auth.warning ? `<div class="auth-readiness-wide" style="color:#ffcf8a; margin-top:8px;">${safe(auth.warning)}</div>` : "";

  panel.innerHTML = `
    <div class="auth-readiness-wide"><strong>Auth readiness</strong></div>
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

let apiAccessState = { enabled: false, key_configured: false, active: false };

function renderApiAccessState(api, options = {}) {
  const incoming = api && typeof api === "object" ? api : {};
  apiAccessState = {
    enabled: Boolean(incoming.enabled),
    key_configured: Boolean(incoming.key_configured),
    active: Boolean(incoming.active)
  };

  const enabledInput = document.getElementById("apiAccessEnabled");
  const status = document.getElementById("apiAccessStatus");
  const generateBtn = document.getElementById("apiKeyGenerateBtn");
  const revokeBtn = document.getElementById("apiKeyRevokeBtn");

  if (enabledInput && !options.preserveEnabledInput) enabledInput.checked = apiAccessState.enabled;
  if (status) {
    const enabledText = apiAccessState.enabled ? "Enabled" : "Disabled";
    const keyText = apiAccessState.key_configured ? "Key configured" : "No key configured";
    const activeText = apiAccessState.active ? "Active" : "Inactive";
    status.textContent = `${enabledText} · ${keyText} · ${activeText}`;
    status.dataset.state = apiAccessState.active ? "active" : (apiAccessState.enabled ? "warning" : "inactive");
  }
  if (generateBtn) generateBtn.textContent = apiAccessState.key_configured ? "Regenerate Key" : "Generate Key";
  if (revokeBtn) revokeBtn.disabled = !apiAccessState.key_configured;
}

function clearApiKeyReveal() {
  const reveal = document.getElementById("apiKeyReveal");
  const input = document.getElementById("apiKeyPlaintext");
  if (input) input.value = "";
  if (reveal) reveal.hidden = true;
}

function revealGeneratedApiKey(apiKey) {
  const reveal = document.getElementById("apiKeyReveal");
  const input = document.getElementById("apiKeyPlaintext");
  if (!reveal || !input) return;
  input.value = String(apiKey || "");
  reveal.hidden = false;
  input.focus();
  input.select();
}

async function regenerateApiKey() {
  if (apiAccessState.key_configured && !window.confirm("Regenerate the API key? The current key will stop working immediately.")) {
    return;
  }

  const button = document.getElementById("apiKeyGenerateBtn");
  const csrfToken = (document.getElementById("adminCsrfToken")?.value || "").trim();
  if (!csrfToken) {
    showCustomAlert("Missing CSRF token.");
    return;
  }

  if (button) button.disabled = true;
  try {
    const data = await window.ApiHttp.postJSONRequest(
      `${SETTINGS_PREFIX}/api_key/regenerate`,
      {},
      { csrfToken },
      "API key generation failed."
    );
    if (!data || data.status !== "success" || !data.api_key) {
      throw new Error(data?.message || data?.error || "API key generation failed.");
    }
    renderApiAccessState(data.api || { ...apiAccessState, key_configured: true }, { preserveEnabledInput: true });
    revealGeneratedApiKey(data.api_key);
  } catch (err) {
    if (!isRedirectingToLoginError(err)) {
      showCustomAlert(getActionErrorMessage("API key error", err, "API key generation failed."));
    }
  } finally {
    if (button) button.disabled = false;
  }
}

async function revokeApiKey() {
  if (!apiAccessState.key_configured) return;
  if (!window.confirm("Revoke the API key? Existing clients will immediately lose access.")) return;

  const button = document.getElementById("apiKeyRevokeBtn");
  const csrfToken = (document.getElementById("adminCsrfToken")?.value || "").trim();
  if (!csrfToken) {
    showCustomAlert("Missing CSRF token.");
    return;
  }

  if (button) button.disabled = true;
  try {
    const data = await window.ApiHttp.postJSONRequest(
      `${SETTINGS_PREFIX}/api_key/revoke`,
      {},
      { csrfToken },
      "API key revocation failed."
    );
    if (!data || data.status !== "success") {
      throw new Error(data?.message || data?.error || "API key revocation failed.");
    }
    clearApiKeyReveal();
    renderApiAccessState(
      data.api || { enabled: false, key_configured: false, active: false }
    );
  } catch (err) {
    if (!isRedirectingToLoginError(err)) {
      showCustomAlert(getActionErrorMessage("API key error", err, "API key revocation failed."));
    }
  } finally {
    if (button) button.disabled = false;
  }
}

function initializeApiAccessControls() {
  const generateBtn = document.getElementById("apiKeyGenerateBtn");
  const revokeBtn = document.getElementById("apiKeyRevokeBtn");
  const copyBtn = document.getElementById("apiKeyCopyBtn");
  const baseUrlInput = document.getElementById("apiBaseUrl");
  const baseUrlCopyBtn = document.getElementById("apiBaseUrlCopyBtn");

  if (baseUrlInput) baseUrlInput.value = `${window.location.origin}/v1`;

  if (generateBtn && generateBtn.dataset.bound !== "1") {
    generateBtn.dataset.bound = "1";
    generateBtn.addEventListener("click", regenerateApiKey);
  }
  if (revokeBtn && revokeBtn.dataset.bound !== "1") {
    revokeBtn.dataset.bound = "1";
    revokeBtn.addEventListener("click", revokeApiKey);
  }
  if (copyBtn && copyBtn.dataset.bound !== "1") {
    copyBtn.dataset.bound = "1";
    copyBtn.addEventListener("click", () => {
      const input = document.getElementById("apiKeyPlaintext");
      if (input?.value) copyTextToClipboard(input.value, copyBtn);
    });
  }
  if (baseUrlCopyBtn && baseUrlCopyBtn.dataset.bound !== "1") {
    baseUrlCopyBtn.dataset.bound = "1";
    baseUrlCopyBtn.addEventListener("click", () => {
      const input = document.getElementById("apiBaseUrl");
      if (input?.value) copyTextToClipboard(input.value, baseUrlCopyBtn);
    });
  }
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

      const setAttachmentLimitMb = (id, byteValue) => {
        const el = document.getElementById(id);
        const bytes = Number(byteValue);
        if (!el || !Number.isFinite(bytes)) return;
        const exactMb = bytes / (1024 * 1024);
        const nearestMb = Math.round(exactMb);
        const displayMb = Math.abs(exactMb - nearestMb) < 0.05 ? nearestMb : exactMb;
        el.value = String(displayMb);
        el.dataset.originalBytes = String(Math.round(bytes));
        el.dataset.mbEdited = "false";
        if (el.dataset.mbConversionBound !== "true") {
          el.dataset.mbConversionBound = "true";
          el.addEventListener("input", () => { el.dataset.mbEdited = "true"; });
        }
      };

      // Admin drawer
      setVal("dbHostInput", data.db_host);
      setVal("dbPortInput", data.db_port);
      setVal("llamaServerPathInput", data.llama_server_path);
      setVal("speechRuntimePathInput", data.speech_runtime_path);
      setVal("speechPortInput", data.speech_port);
      setVal("llamaMainPortInput", data.llama_main_port);
      setVal("llamaTitlePortInput", data.llama_title_port);
      if (typeof window.setTitleModelSelection === "function") {
        window.setTitleModelSelection(data.title_model_path || "");
      } else {
        setVal("titleModelPathInput", data.title_model_path || "");
      }
      setChecked("modelPickerShowFriendlyNames", data.model_picker_show_friendly_names);
      setVal("scanDirectoryInput", data.scan_directory);
      setVal("versionDisplay", data.version);
      if (Number.isInteger(data.chat_import_max_mib) && data.chat_import_max_mib > 0) {
        setVal("chatImportMaxMib", data.chat_import_max_mib);
        const importFileInput = document.getElementById("importFile");
        const importSizeLimit = document.getElementById("chatImportSizeLimit");
        if (importFileInput) importFileInput.dataset.maxMib = String(data.chat_import_max_mib);
        if (importSizeLimit) importSizeLimit.textContent = String(data.chat_import_max_mib);
      }

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
      setAttachmentLimitMb("attachmentMaxFileBytes", data.attachments_max_file_bytes);
      setAttachmentLimitMb("attachmentMaxTotalBytes", data.attachments_max_total_bytes);
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
      renderApiAccessState(data.api);

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
  const getAttachmentLimitBytes = (id) => {
    const el = document.getElementById(id);
    if (!el) return "";
    if (el.dataset.mbEdited !== "true" && el.dataset.originalBytes) {
      return el.dataset.originalBytes;
    }
    const mb = Number(el.value);
    return Number.isFinite(mb) ? String(Math.round(mb * 1024 * 1024)) : el.value;
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
    speech_runtime_path: getVal("speechRuntimePathInput").trim(),
    speech_port: getVal("speechPortInput"),
    llama_main_port:   getVal("llamaMainPortInput"),
    llama_title_port:  getVal("llamaTitlePortInput"),
    title_model_path: typeof window.getTitleModelSelection === "function"
      ? window.getTitleModelSelection()
      : getVal("titleModelPathInput").trim(),
    model_picker_show_friendly_names: getChecked("modelPickerShowFriendlyNames"),
    api_enabled:       getChecked("apiAccessEnabled"),
    n_gpu_layers:     getVal("defaultGpuLayers"),
    n_cpu_threads:    getVal("defaultCpuThreads"),
    dual_gpu_split_threshold_gb: getVal("dualGpuSplitThresholdGb"),
    temperature:      getVal("defaultTemperature"),
    top_k:            getVal("defaultTopK"),
    top_p:            getVal("defaultTopP"),
    repeat_penalty:   getVal("defaultRepeatPenalty"),
    seed:             getVal("defaultSeed"),
    chat_import_max_mib: getVal("chatImportMaxMib"),
    attachments_max_files: getVal("attachmentMaxFiles"),
    attachments_max_file_bytes: getAttachmentLimitBytes("attachmentMaxFileBytes"),
    attachments_max_total_bytes: getAttachmentLimitBytes("attachmentMaxTotalBytes"),
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
      if (typeof window.loadModelDropdown === "function") window.loadModelDropdown();
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
  return fetch(`${CHAT_PREFIX}/get_sessions`)
    .then(res => res.json())
    .then(data => {
      chatHistorySessions = Array.isArray(data.sessions) ? data.sessions : [];
      renderProjectList();
      displayGroupedSessions(chatHistorySessions);

      if (currentSessionId && !sessionExistsInHistory(currentSessionId, chatHistorySessions)) {
        clearCurrentSessionId();
        syncSocketChatSessionSubscription("");
      }

      handleInitialChatUrlState(chatHistorySessions);
      return data;
    })
    .catch(err => {
      console.error(err);
      return { sessions: [] };
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
  if (!message && composerAttachments.length === 0) return;

  const isEdit = Boolean(pendingPromptEdit && pendingPromptEdit.sourceMessageId);
  const sourceMessageId = isEdit ? pendingPromptEdit.sourceMessageId : null;
  const preserveStoredAttachments = Boolean(
    isEdit && pendingPromptEdit.preserveStoredAttachments && composerAttachments.length === 0
  );
  let attachmentPayload = preserveStoredAttachments ? null : [];
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

    if (!preserveStoredAttachments) {
      attachmentPayload = await buildAttachmentPayload();
    }
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

  const messageId = (crypto.randomUUID ? crypto.randomUUID() : (Date.now() + "-" + Math.random().toString(16).slice(2)));
  const payload = {
    session_id: currentSessionId,
    message: message,
    message_id: messageId
  };
  if (attachmentPayload !== null) {
    payload.attachments = attachmentPayload;
  }
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

  // Keep generated preview data out of the transport/persistence payload.
  const displayAttachments = attachmentPayload === null ? null : attachmentPayload.map((attachment, index) => {
    const source = composerAttachments[index];
    return PNG_PREVIEW_MIME_TYPES.includes(attachment.mime_type) && source
      ? { ...attachment, previewUrl: source.previewUrl, previewRequest: source.previewRequest, previewError: source.previewError }
      : attachment;
  });

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
      },
      retryDraft: {
        sourceMessageId,
        message,
        preserveStoredAttachments
      }
    });

    userBubble.dataset.messageId = messageId;
    setUserBubbleText(userBubble, message);
    if (attachmentPayload !== null) {
      renderMessageAttachments(userBubble, displayAttachments);
    }

    botBubble.dataset.messageId = messageId;
    prepareBubbleForStreaming(botBubble);
  } else {
    appendUserMessage(message, { messageId, attachments: displayAttachments });
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

  // Associate incoming chunks with the current response.
  window.currentAssistantMessageId = messageId;
  clearPromptEditState();
  refreshComposerButtons();
  refreshAssistantBubbleControls();
  renderComposerAttachments();

  socket.emit("send_message", payload);
}

function loadChat(session_id) {
  if (isResponding) {
    if (session_id && session_id !== currentSessionId) {
      pendingSessionId = session_id;
    } else {
      pendingReloadCurrent = true;
    }
    return;
  }

  setCurrentSessionId(session_id);
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

      let firstModel = chats[0].model_used || "Unknown Model";
      chatMessages.appendChild(insertModelLabel(`Generated with: ${firstModel}`));

      let prevModel = firstModel;
      chats.forEach((msg, i) => {
        if (i > 0 && msg.model_used && msg.model_used !== prevModel) {
          chatMessages.appendChild(insertModelLabel(`Model switched to: ${msg.model_used}`));
        }
        prevModel = msg.model_used || prevModel;

        if (!msg.user_message && !msg.bot_response) return;
        appendUserMessage(msg.user_message, {
          messageId: msg.prompt_id,
          attachments: msg.attachments || []
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

        const thoughtsDiv = botBubble.querySelector('.thoughts');
        const toggle = botBubble.querySelector('.show-thoughts');

        if (thoughtsDiv && toggle) {
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
        renderMessageAttachments(botBubble, msg.assistant_attachments || msg.bot_attachments || []);
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
  window.ApiHttp.postJSONRequest(`${CHAT_PREFIX}/delete_session`, {
    session_id
  }, {}, "Could not delete this chat.").then(() => {
    if (currentSessionId === session_id) {
      clearPromptEditState({ clearInput: true });
      clearComposerAttachments();
      // Keep the stream subscription until its terminal event can release the busy state.
      if (isResponding) stopActiveResponse();
      else syncSocketChatSessionSubscription("");
      clearCurrentSessionId();
    }
    loadChatHistory();
  }).catch(err => {
    if (!isRedirectingToLoginError(err)) {
      showCustomAlert(getActionErrorMessage("", err, "Could not delete this chat."));
    }
  });
}

function renameSession(session_id, currentName) {
  const new_name = prompt("Enter a new session name:", currentName);
  if (!new_name) return;
  postJSON(`${CHAT_PREFIX}/rename_session`, {
session_id, new_name 
  }).then(() => loadChatHistory());
}

function getModelDisplayName(pathOrName) {
  if (!pathOrName) return '';
  const name = String(pathOrName).split(/[\\/]/).pop();
  return name.replace(/\.gguf$/i, "");
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
        window.SystemDrawer.stopLogStream();
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
      if (typeof window.updateTitleModelStatus === "function") window.updateTitleModelStatus(data);
      if (!modelStartInProgress && typeof window.syncModelPickerStatus === "function") window.syncModelPickerStatus(data);
      const statusEl = document.getElementById('modelStatusText');
      const modelEl  = document.getElementById('currentModel');
      const modeEl   = document.getElementById('currentRuntimeMode');
      const mmprojRow = document.getElementById('currentMmprojRow');
      const mmprojEl = document.getElementById('currentMmproj');
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
        setText(mmprojEl, '');
        if (mmprojRow) mmprojRow.hidden = true;
        return;
      }

      if (data.status === 'running') {
        modelLoaded = true;
        setText(statusEl, '✅ Running');

        const modelDisplay = data.current_model ? String(data.current_model) : 'Unknown';
        setText(modelEl, modelDisplay);

        const s = data.settings || {};
        const runtime = (data.runtime && typeof data.runtime === "object") ? data.runtime : {};
        const runtimeGpuLayers = runtime.n_gpu_layers ?? GPU_LAYERS ?? '';
        const runtimeCpuThreads = runtime.n_threads ?? CPU_THREADS ?? '';
        const runtimeMode = String(runtime.runtime_mode || "").toLowerCase();
        const runtimeModeLabel = runtime.runtime_mode_label || (runtimeMode === "cpu" ? "Running on CPU" : (runtimeMode === "gpu" ? "Running on GPU" : ""));
        const mmprojFilename = String(runtime.mmproj_filename || "").trim();
        const mmprojDisplay = getModelDisplayName(mmprojFilename);

        GPU_LAYERS = runtimeGpuLayers !== '' ? String(runtimeGpuLayers) : GPU_LAYERS;
        CPU_THREADS = runtimeCpuThreads !== '' ? String(runtimeCpuThreads) : CPU_THREADS;

        setText(gpuEl, runtimeGpuLayers);
        setText(cpuEl, runtimeCpuThreads);
        setText(tmpEl,  s['temp']           || '');
        setText(kEl,    s['top-k']          || '');
        setText(pEl,    s['top-p']          || '');
        setText(rEl,    s['repeat-penalty'] || '');
        setText(seedEl, s['seed']           || '');
        setText(mmprojEl, mmprojDisplay);
        if (mmprojRow) mmprojRow.hidden = !mmprojFilename;
        if (modeEl) {
          setText(modeEl, runtimeModeLabel || '-');
          modeEl.className = `runtime-mode-indicator${runtimeMode ? ` ${runtimeMode}` : ''}`;
        }
        syncModelDrawerRuntimeState(runtime);

        // Everyone gets title updates (admin or not).
        if (data.current_model) {
          const modelTitle = String(data.current_model);
          document.title = modelTitle;
          if (appTitle) appTitle.innerText = modelTitle;
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
        setText(mmprojEl, '');
        if (mmprojRow) mmprojRow.hidden = true;

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
    setInterval(pollModelStatus, 3000);
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();

function initModelDrawer() {
  // Populate "Running Model" panel with live data…
  pollModelStatus();
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
  renderMessageAttachments(bubble, options.attachments || []);
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
  if (isResponding && data.message_id === window.currentAssistantMessageId &&
      (data.streaming_done || data.reset_state)) return true;
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
        footer.innerText = data.error
          ? `Response interrupted — not saved. ${data.error}`
          : formatResponseFooter(data);
      }
      renderMessageAttachments(bubble, data.assistant_attachments || data.bot_attachments || []);
    }

    regenerateSnapshots.delete(messageId);
    editPromptSnapshots.delete(messageId);
    stopRequestedMessageId = null;
    isResponding = false;
    currentGenerationMode = null;
    syncSocketChatSessionSubscription(currentSessionId);
    const preserveAttachments = Boolean(
      data.preserve_attachments || data.attachment_error || data.error || data.status === "error"
    );
    if ((completedMode === "send" || completedMode === "edit_prompt") && !preserveAttachments) {
      clearComposerAttachments();
    }
    refreshComposerButtons();
    renderComposerAttachments();
    refreshAssistantBubbleControls();
    loadChatHistory();
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
      const retryDraft = editPromptSnapshots.get(messageId)?.retryDraft || null;
      restoreEditedTurnSnapshot(messageId);
      if (retryDraft?.sourceMessageId) {
        const input = document.getElementById("chatInput");
        pendingPromptEdit = {
          sourceMessageId: retryDraft.sourceMessageId,
          preserveStoredAttachments: Boolean(retryDraft.preserveStoredAttachments)
        };
        if (input) {
          input.value = retryDraft.message || "";
          autoResize(input);
        }
      }
    }

    stopRequestedMessageId = null;
    isResponding = false;
    currentGenerationMode = null;
    isPreparingSend = false;
    syncSocketChatSessionSubscription(currentSessionId);
    refreshComposerButtons();
    renderComposerAttachments();
    refreshAssistantBubbleControls();
  }

  showCustomAlert(data?.message || "An unexpected chat error occurred.");
});

function toggleAboutModal(show) {
  const modal = document.getElementById('aboutModal');
  if (show) {
    modal.classList.add('active');
  } else {
    modal.classList.remove('active');
  }
}

document.getElementById('aboutBtn').onclick = () => toggleAboutModal(true);

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
      showControlsBar.style.display = 'none';
    }
  } else {
    drawerBar.classList.add('hidden');
    chatInput.classList.add('drawer-bar-hidden');
    btn.innerHTML = '▲ Show Controls';
    if (isMobile()) {
      showControlsBar.style.display = '';
    } else {
      showControlsBar.style.display = 'none';
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
    drawerBar.classList.remove('hidden');
    chatInput.classList.remove('drawer-bar-hidden');
    btn.innerHTML = '▼ Hide Controls';
    showControlsBar.style.display = 'none';
  } else {
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
async function loadSystemInstructionsPreference() {
  const input = document.getElementById('systemInstructionsInput');
  const alert = document.getElementById('system-instructions-alert');
  const submitButton = document.querySelector('#systemInstructionsForm button[type="submit"]');
  if (!input || !alert) return;

  input.disabled = true;
  if (submitButton) submitButton.disabled = true;
  alert.textContent = "Loading System Instructions...";
  alert.className = "";
  try {
    const data = await window.ApiHttp.requestJSON(
      '/settings/system_instructions',
      {
        method: 'GET',
        cache: 'no-store',
        headers: { 'Accept': 'application/json' }
      },
      "System Instructions could not be loaded."
    );
    input.value = String(data.system_instructions || "");
    const maxChars = Number(data.max_chars);
    if (Number.isInteger(maxChars) && maxChars > 0) input.maxLength = maxChars;
    alert.textContent = "";
  } catch (err) {
    if (isRedirectingToLoginError(err)) return;
    alert.textContent = getActionErrorMessage("", err, "System Instructions could not be loaded.");
    alert.className = "error";
  } finally {
    input.disabled = false;
    if (submitButton) submitButton.disabled = false;
  }
}

(function initializeAccountSettingsTabs() {
  const tabs = Array.from(document.querySelectorAll('#userSettingsModal [role="tab"]'));
  function selectTab(index, focus = false) {
    tabs.forEach((tab, i) => {
      const selected = i === index;
      tab.classList.toggle('active', selected);
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
      document.getElementById(tab.getAttribute('aria-controls')).hidden = !selected;
      if (selected && focus) tab.focus();
    });
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => selectTab(index));
    tab.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
        : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      selectTab(next, true);
    });
  });
})();

function toggleUserSettingsModal(show) {
  closeAllDrawers();
  document.getElementById('userSettingsModal').style.display = show ? 'flex' : 'none';
  if (show) loadSystemInstructionsPreference();
}

document.getElementById('systemInstructionsForm').onsubmit = function(e) {
  e.preventDefault();
  const form = this;
  const input = form.elements["system_instructions"];
  const submitButton = form.querySelector('button[type="submit"]');
  const csrfField = form.querySelector('input[name="csrf_token"]');
  const csrfToken = csrfField ? csrfField.value : '';
  const alert = document.getElementById('system-instructions-alert');

  submitButton.disabled = true;
  alert.textContent = "Saving...";
  alert.className = "";
  window.ApiHttp.postJSONRequest(
    '/settings/system_instructions',
    { system_instructions: input.value },
    { csrfToken },
    "System Instructions could not be saved."
  )
    .then((json) => {
      input.value = String(json.system_instructions || "");
      alert.textContent = json.message || "System Instructions saved.";
      alert.className = "success";
    })
    .catch((err) => {
      if (isRedirectingToLoginError(err)) return;
      alert.textContent = getActionErrorMessage("", err, "System Instructions could not be saved.");
      alert.className = "error";
    })
    .finally(() => {
      submitButton.disabled = false;
    });
};

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

document.getElementById('changePasswordForm').onsubmit = function(e) {
  e.preventDefault();
  const form = this;
  const csrfField = form.querySelector('input[name="csrf_token"]');
  const csrfToken = csrfField ? csrfField.value : '';
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
