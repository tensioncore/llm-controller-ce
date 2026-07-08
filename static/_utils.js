/* /core/_utils.js
   Shared UI + formatting helpers.
*/

const COPY_BUTTON_TEXT = "\u{1F4CB} Copy";
const COPY_BUTTON_SUCCESS_TEXT = "\u2705 Copied!";

function insertModelLabel(text) {
  const label = document.createElement("div");
  label.className = "model-label";
  label.innerText = text;
  return label;
}

function normalizeCopiedCodeText(text) {
  return String(text ?? "")
    .replace(/[\u201C\u201D\u201E\u201F]/g, '"')
    .replace(/[\u2018\u2019\u201A\u201B]/g, "'")
    .replace(/[\u2013\u2014]/g, "-")
    .replace(/[\u00A0\u202F]/g, " ")
    .replace(/[\u200B\u200C\u200D\uFEFF]/g, "");
}

function getCopySourceText(text, button) {
  const codeElement = button?.closest(".codeblock")?.querySelector("pre > code");
  if (codeElement) {
    return codeElement.textContent || "";
  }
  return String(text ?? "");
}

function getCopyButtonResetText(button) {
  if (!button) return COPY_BUTTON_TEXT;
  if (!button.dataset) return button.innerText || COPY_BUTTON_TEXT;
  const resetText = button.dataset.copyButtonResetText || button.innerText || COPY_BUTTON_TEXT;
  button.dataset.copyButtonResetText = resetText;
  return resetText;
}

function copyTextToClipboard(text, button) {
  const normalizedText = normalizeCopiedCodeText(getCopySourceText(text, button));
  const resetText = getCopyButtonResetText(button);

  // Try Clipboard API first
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(normalizedText).then(() => {
      if (button) {
        button.innerText = COPY_BUTTON_SUCCESS_TEXT;
        setTimeout(() => { button.innerText = resetText; }, 1500);
      }
    }).catch(() => fallbackCopy(normalizedText, button));
  } else {
    fallbackCopy(normalizedText, button);
  }
}

function fallbackCopy(text, button) {
  const resetText = getCopyButtonResetText(button);
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "absolute";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  try {
    document.execCommand("copy");
    if (button) {
      button.innerText = COPY_BUTTON_SUCCESS_TEXT;
      setTimeout(() => { button.innerText = resetText; }, 1500);
    }
  } catch (err) {
    alert("Could not copy!");
  }
  document.body.removeChild(textarea);
}

// Helper to escape HTML in <pre>
function escapeHtml(text) {
  return text.replace(/[&<>"']/g, function(m) {
    return ({
      "&":"&amp;",
      "<":"&lt;",
      ">":"&gt;",
      '"':"&quot;",
      "'":"&#39;"
    })[m];
  });
}

const SAFE_MARKDOWN_TAGS = new Set([
  "a", "blockquote", "br", "code", "del", "em", "h1", "h2", "h3", "h4", "h5", "h6",
  "hr", "li", "ol", "p", "pre", "s", "span", "strong", "table", "tbody", "td", "th",
  "thead", "tr", "ul"
]);

const DROP_MARKDOWN_TAGS = new Set([
  "script", "style", "iframe", "object", "embed", "link", "meta", "form", "input",
  "button", "textarea", "select", "option", "svg", "math"
]);

const SAFE_MARKDOWN_ATTRS = {
  a: new Set(["href", "title", "target", "rel"]),
  code: new Set(["class"]),
  pre: new Set(["class"]),
  span: new Set(["class"]),
  td: new Set(["align"]),
  th: new Set(["align"])
};

function isSafeMarkdownUrl(value) {
  const raw = String(value || "").trim();
  if (!raw) return false;
  const compact = raw.replace(/[\u0000-\u001F\u007F\s]+/g, "").toLowerCase();
  if (/^(javascript|vbscript|data):/.test(compact)) {
    return false;
  }

  if (raw.startsWith("#") || raw.startsWith("/") || raw.startsWith("./") || raw.startsWith("../")) {
    return true;
  }

  try {
    const parsed = new URL(raw, window.location.origin);
    return ["http:", "https:", "mailto:", "tel:"].includes(parsed.protocol);
  } catch (_) {
    return false;
  }
}

function sanitizeMarkdownHtml(html) {
  const template = document.createElement("template");
  template.innerHTML = String(html || "");

  function cleanNode(node) {
    if (node.nodeType === Node.COMMENT_NODE) {
      node.remove();
      return;
    }

    if (node.nodeType !== Node.ELEMENT_NODE) {
      return;
    }

    const tagName = node.tagName.toLowerCase();

    if (DROP_MARKDOWN_TAGS.has(tagName)) {
      node.remove();
      return;
    }

    Array.from(node.childNodes).forEach(cleanNode);

    if (!SAFE_MARKDOWN_TAGS.has(tagName)) {
      const parent = node.parentNode;
      if (!parent) return;
      while (node.firstChild) {
        parent.insertBefore(node.firstChild, node);
      }
      node.remove();
      return;
    }

    const allowedAttrs = SAFE_MARKDOWN_ATTRS[tagName] || new Set();
    Array.from(node.attributes).forEach((attr) => {
      const attrName = attr.name.toLowerCase();
      const attrValue = attr.value || "";

      if (attrName.startsWith("on") || !allowedAttrs.has(attrName)) {
        node.removeAttribute(attr.name);
        return;
      }

      if (tagName === "a" && attrName === "href" && !isSafeMarkdownUrl(attrValue)) {
        node.removeAttribute(attr.name);
      }
    });

    if (tagName === "a") {
      if (node.getAttribute("target") === "_blank") {
        node.setAttribute("rel", "noopener noreferrer");
      }
    }
  }

  Array.from(template.content.childNodes).forEach(cleanNode);
  return template.innerHTML;
}

function renderSafeMarkdown(markdownText) {
  const raw = String(markdownText || "");
  if (!raw) return "";

  try {
    if (window.marked && typeof window.marked.parse === "function") {
      return sanitizeMarkdownHtml(window.marked.parse(raw));
    }
    if (typeof marked !== "undefined" && typeof marked.parse === "function") {
      return sanitizeMarkdownHtml(marked.parse(raw));
    }
  } catch (_) {}

  return `<pre>${escapeHtml(raw)}</pre>`;
}

window.sanitizeMarkdownHtml = sanitizeMarkdownHtml;
window.renderSafeMarkdown = renderSafeMarkdown;

// Helper: format time in seconds
function formatTime(seconds) {
  seconds = Number(seconds);
  if (seconds >= 60) {
    const minutes = Math.floor(seconds / 60);
    const remaining = (seconds % 60).toFixed(2);
    return `${minutes}m ${remaining}s`;
  }
  return `${seconds.toFixed(2)}s`;
}

function extractAndStandardizeMath(content) {
  // Standardizes math formats to LaTeX blocks for MathJax
  // 1. Markdown code blocks ```math``` or ```latex```
  content = content.replace(/```(math|latex)\s*([\s\S]*?)```/gi, function(_, __, match) {
    return `\n$$${match.trim()}$$\n`;
  });
  // 2. Block math: \[ ... \]
  content = content.replace(/\\\[(.*?)\\\]/gs, (_, match) => {
    return `\n$$${match.trim()}$$\n`;
  });
  // 3. Inline math: $...$
  content = content.replace(/\$(.+?)\$/g, function(_, match) {
    if (match.match(/[\d\+\-\*\/=]/)) {
      return `\\(${match.trim()}\\)`;
    }
    return `$${match}$`;
  });
  // 4. Inline math: \( ... \)
  content = content.replace(/\\\((.*?)\\\)/g, function(_, match) {
    return `\\(${match.trim()}\\)`;
  });
  // 5. Remove ONLY math/latex code block fences, leave other code blocks alone
  return content;
}

// Custom alert function for a modern modal alert that dismisses on tap.
function showCustomAlert(message, isHTML = false) {
  const modal = document.createElement("div");
  modal.style.position = "fixed";
  modal.style.top = 0;
  modal.style.left = 0;
  modal.style.width = "100%";
  modal.style.height = "100%";
  modal.style.backgroundColor = "rgba(0, 0, 0, 0.5)";
  modal.style.color = "#000000";
  modal.style.display = "flex";
  modal.style.justifyContent = "center";
  modal.style.alignItems = "center";
  modal.style.zIndex = 9999;
  modal.onclick = function () {
    document.body.removeChild(modal);
  };

  const messageBox = document.createElement("div");
  messageBox.style.backgroundColor = "#fff";
  messageBox.style.padding = "20px";
  messageBox.style.borderRadius = "8px";
  messageBox.style.boxShadow = "0 2px 10px rgba(0,0,0,0.1)";
  messageBox.style.maxWidth = "80%";
  messageBox.style.textAlign = "center";
  messageBox.style.fontFamily = "sans-serif";
  messageBox.style.maxWidth = "900px";
  messageBox.style.width = "90vw";

  if (isHTML) {
    messageBox.innerHTML = message;
  } else {
    messageBox.innerText = message;
  }

  modal.appendChild(messageBox);
  document.body.appendChild(modal);
}
