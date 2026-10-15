// Shared CE decision and manual-copy dialogs; no application or clipboard dependencies.
let ceDecisionDialogOpen = false;

function showDecisionDialog({ title, message, actionLabel = "Confirm", destructive = true,
  requiredToken = null, selectableText = null } = {}) {
  // A second click must not queue another decision for the same action.
  if (ceDecisionDialogOpen) return Promise.resolve(false);
  ceDecisionDialogOpen = true;
  const previousFocus = document.activeElement;
  const textOnly = selectableText !== null;

  return new Promise(resolve => {
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay active ce-decision-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-labelledby", "ceDecisionTitle");
    overlay.setAttribute("aria-describedby", "ceDecisionMessage");
    const card = document.createElement("div");
    card.className = "user-modal-card project-modal-card";
    const close = document.createElement("button");
    close.type = "button";
    close.className = "user-close-btn";
    close.setAttribute("aria-label", "Close dialog");
    close.textContent = "×";
    const heading = document.createElement("h3");
    heading.id = "ceDecisionTitle";
    heading.textContent = title;
    const description = document.createElement("p");
    description.id = "ceDecisionMessage";
    description.className = "ce-dialog-message";
    description.textContent = message;
    const form = document.createElement("form");
    let input = null;
    if (requiredToken !== null || textOnly) {
      const label = document.createElement("label");
      label.htmlFor = "ceDecisionInput";
      label.textContent = textOnly ? "Copy this text:" : `Type ${requiredToken} to confirm`;
      input = document.createElement(textOnly ? "textarea" : "input");
      input.id = "ceDecisionInput";
      if (textOnly) {
        input.readOnly = true;
        input.rows = 9;
        input.value = String(selectableText);
      } else {
        input.type = "text";
        input.autocomplete = "off";
        input.spellcheck = false;
      }
      form.append(label, input);
    }
    const actions = document.createElement("div");
    actions.className = "project-modal-actions";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "user-action-btn";
    cancel.textContent = "Cancel";
    const action = document.createElement("button");
    action.type = "submit";
    action.className = `user-action-btn${textOnly ? "" : destructive ? " ce-dialog-danger" : " ce-dialog-positive"}`;
    action.textContent = textOnly ? "Close" : actionLabel;
    if (!textOnly) actions.appendChild(cancel);
    actions.appendChild(action);
    form.appendChild(actions);
    card.append(close, heading, description, form);
    overlay.appendChild(card);
    let settled = false;

    function finish(accepted) {
      if (settled) return;
      settled = true;
      action.disabled = true;
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("focusin", keepFocus, true);
      overlay.remove();
      ceDecisionDialogOpen = false;
      if (previousFocus?.isConnected) previousFocus.focus();
      resolve(accepted);
    }
    function keepFocus(event) {
      if (!overlay.contains(event.target)) (input || cancel).focus();
    }
    function onKeyDown(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        finish(false);
      } else if (event.key === "Enter" && event.repeat) {
        event.preventDefault();
        event.stopImmediatePropagation();
      } else if (event.key === "Tab") {
        const controls = Array.from(card.querySelectorAll("button, input, textarea"))
          .filter(control => !control.disabled);
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    }
    close.addEventListener("click", () => finish(false));
    cancel.addEventListener("click", () => finish(false));
    overlay.addEventListener("click", event => {
      if (event.target === overlay) finish(false);
    });
    form.addEventListener("submit", event => {
      event.preventDefault();
      if (requiredToken !== null && input.value !== requiredToken) return;
      finish(true);
    });
    if (requiredToken !== null) {
      action.disabled = true;
      input.addEventListener("input", () => { action.disabled = input.value !== requiredToken; });
    }
    document.body.appendChild(overlay);
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("focusin", keepFocus, true);
    (input || cancel).focus();
    if (textOnly) input.select();
  });
}

function showSelectableTextDialog(text) {
  return showDecisionDialog({ title: "Copy account details", message: "Select and copy the account details below.",
    selectableText: text });
}
