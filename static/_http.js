/* /static/_http.js
   Shared request and small utility helpers.
*/

const AUTH_LOGOUT_PATH = "/logout";
let csrfRefreshInFlight = null;
let logoutRedirectInFlight = false;

function buildJsonHeaders(extraHeaders = {}, csrfToken = window.CSRF_TOKEN) {
  const headers = {
    "Content-Type": "application/json",
    ...extraHeaders
  };

  if (csrfToken) {
    headers["X-CSRFToken"] = csrfToken;
  }

  return headers;
}

// Cross Site Request Forgery protection
function postJSON(url, data, options = {}) {
  const { headers = {}, csrfToken = window.CSRF_TOKEN, ...fetchOptions } = options;
  return fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: buildJsonHeaders(headers, csrfToken),
    body: JSON.stringify(data),
    ...fetchOptions
  });
}

function looksLikeHtmlDocument(text) {
  const snippet = String(text || "").trim().slice(0, 256).toLowerCase();
  return (
    snippet.startsWith("<!doctype") ||
    snippet.startsWith("<html") ||
    snippet.startsWith("<head") ||
    snippet.startsWith("<body") ||
    snippet.startsWith("<form") ||
    snippet.startsWith("<ht")
  );
}

async function readApiResponse(response) {
  const contentType = (response.headers.get("content-type") || "").toLowerCase();
  const text = await response.text();
  let data = null;

  if (text) {
    if (contentType.includes("application/json")) {
      try {
        data = JSON.parse(text);
      } catch (_) {
        data = null;
      }
    } else if (text.trim().startsWith("{") || text.trim().startsWith("[")) {
      try {
        data = JSON.parse(text);
      } catch (_) {
        data = null;
      }
    }
  }

  return {
    response,
    ok: response.ok,
    status: response.status,
    redirected: response.redirected,
    url: response.url,
    contentType,
    text,
    data,
    isJson: data !== null || contentType.includes("application/json"),
    looksHtml: looksLikeHtmlDocument(text)
  };
}

function getValidationMessage(data) {
  if (!data || typeof data !== "object" || !data.errors || typeof data.errors !== "object") {
    return "";
  }

  const firstEntry = Object.entries(data.errors).find(([, value]) => {
    return typeof value === "string" && value.trim();
  });

  if (!firstEntry) return "";
  return `${firstEntry[0]}: ${firstEntry[1].trim()}`;
}

function isStaleAuthResult(result) {
  if (!result) return false;

  const code = String(result.data?.code || "").trim().toLowerCase();
  if (["auth_required", "session_expired", "csrf_expired", "csrf_invalid"].includes(code)) {
    return true;
  }

  if (result.data?.reload_required === true) {
    return true;
  }

  if (result.looksHtml && [200, 400, 401, 403].includes(Number(result.status))) {
    return true;
  }

  return false;
}

function isSessionExpiredResult(result) {
  if (!result) return false;
  const code = String(result.data?.code || "").trim().toLowerCase();
  return code === "auth_required" || code === "session_expired" || result.status === 401;
}

function isCsrfFailureResult(result) {
  if (!result) return false;
  const code = String(result.data?.code || "").trim().toLowerCase();
  return code === "csrf_expired" || code === "csrf_invalid" || (result.status === 400 && result.looksHtml);
}

function getStaleAuthMessage(result) {
  const message = result?.data?.message || result?.data?.error;
  if (typeof message === "string" && message.trim()) {
    return message.trim();
  }

  if (result?.status === 401 || result?.status === 403) {
    return "Your session has expired. Reload the page and sign in again.";
  }

  if (result?.status === 400) {
    return "This page has been open too long. Reload it and try again.";
  }

  return "This page is out of date. Reload it and try again.";
}

function getApiErrorMessage(result, fallback = "Request failed.") {
  if (!result) return fallback;

  if (isStaleAuthResult(result)) {
    return getStaleAuthMessage(result);
  }

  const validationMessage = getValidationMessage(result.data);
  if (validationMessage) {
    return validationMessage;
  }

  const message = result?.data?.message || result?.data?.error;
  if (typeof message === "string" && message.trim()) {
    return message.trim();
  }

  const text = String(result?.text || "").trim();
  if (text && !result.looksHtml) {
    return text;
  }

  return fallback;
}

function buildApiError(result, fallback = "Request failed.") {
  const error = new Error(getApiErrorMessage(result, fallback));
  error.apiResult = result;
  error.code = result?.data?.code || "";
  return error;
}

function buildLogoutRedirectError() {
  const error = new Error("Redirecting to login.");
  error.redirectingToLogin = true;
  error.suppressUserMessage = true;
  return error;
}

function applyCsrfToken(token) {
  const nextToken = String(token || "").trim();
  if (!nextToken) return "";

  window.CSRF_TOKEN = nextToken;

  const adminTokenField = document.getElementById("adminCsrfToken");
  if (adminTokenField) {
    adminTokenField.value = nextToken;
  }

  document.querySelectorAll('input[name="csrf_token"]').forEach((field) => {
    field.value = nextToken;
  });

  return nextToken;
}

function withCsrfToken(headers = {}, csrfToken = window.CSRF_TOKEN) {
  const nextHeaders = { ...headers };
  if (csrfToken) {
    nextHeaders["X-CSRFToken"] = csrfToken;
  } else {
    delete nextHeaders["X-CSRFToken"];
  }
  return nextHeaders;
}

function redirectToLogin(onSessionExpired) {
  try {
    if (typeof onSessionExpired === "function") {
      onSessionExpired();
    }
  } catch (_) {
    // Best-effort callback only.
  }

  if (!logoutRedirectInFlight) {
    logoutRedirectInFlight = true;
    window.location.assign(AUTH_LOGOUT_PATH);
  }

  return buildLogoutRedirectError();
}

async function refreshCsrfToken(onSessionExpired) {
  if (csrfRefreshInFlight) {
    return csrfRefreshInFlight;
  }

  csrfRefreshInFlight = (async () => {
    const response = await fetch("/api/csrf-token", {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        "Accept": "application/json"
      }
    });
    const result = await readApiResponse(response);

    if (!response.ok) {
      if (isSessionExpiredResult(result)) {
        throw redirectToLogin(onSessionExpired);
      }
      throw buildApiError(result, "Could not refresh the security token.");
    }

    const token = String(result.data?.csrf_token || "").trim();
    if (!token) {
      throw buildApiError(result, "Could not refresh the security token.");
    }

    applyCsrfToken(token);
    return token;
  })();

  try {
    return await csrfRefreshInFlight;
  } finally {
    csrfRefreshInFlight = null;
  }
}

function shouldRetryWithFreshCsrf(options, result) {
  const method = String(options.method || "GET").toUpperCase();
  if (options._csrfRetried) return false;
  if (["GET", "HEAD", "OPTIONS"].includes(method)) return false;
  return isCsrfFailureResult(result);
}

async function requestJSON(url, options = {}, fallback = "Request failed.") {
  const {
    _csrfRetried = false,
    onSessionExpired,
    ...fetchOptions
  } = options;

  const response = await fetch(url, {
    credentials: "same-origin",
    ...fetchOptions
  });
  const result = await readApiResponse(response);

  if (!response.ok) {
    if (shouldRetryWithFreshCsrf({ ...fetchOptions, _csrfRetried }, result)) {
      const freshToken = await refreshCsrfToken(onSessionExpired);
      return requestJSON(url, {
        ...fetchOptions,
        _csrfRetried: true,
        onSessionExpired,
        headers: withCsrfToken(fetchOptions.headers || {}, freshToken)
      }, fallback);
    }

    if (isSessionExpiredResult(result)) {
      throw redirectToLogin(onSessionExpired);
    }

    throw buildApiError(result, fallback);
  }

  if (result.data === null) {
    throw buildApiError(result, fallback);
  }

  return result.data;
}

async function postJSONRequest(url, data, options = {}, fallback = "Request failed.") {
  const {
    headers = {},
    csrfToken = window.CSRF_TOKEN,
    ...fetchOptions
  } = options;
  return requestJSON(url, {
    method: "POST",
    headers: buildJsonHeaders(headers, csrfToken),
    body: JSON.stringify(data),
    ...fetchOptions
  }, fallback);
}

async function ensureSessionAlive(options = {}) {
  const data = await requestJSON("/api/me", {
    method: "GET",
    cache: "no-store",
    headers: {
      "Accept": "application/json"
    },
    ...options
  }, "Your session has expired. Reload the page and sign in again.");

  if (!data || data.ok !== true) {
    throw redirectToLogin(options.onSessionExpired);
  }

  return data;
}

window.ApiHttp = {
  buildJsonHeaders,
  readApiResponse,
  isStaleAuthResult,
  isSessionExpiredResult,
  isCsrfFailureResult,
  getStaleAuthMessage,
  getApiErrorMessage,
  buildApiError,
  applyCsrfToken,
  refreshCsrfToken,
  requestJSON,
  postJSONRequest,
  ensureSessionAlive
};
