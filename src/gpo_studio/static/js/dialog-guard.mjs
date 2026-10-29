// Unsaved-edit guard for editing dialogs (#140 item 6).
//
// A dialog opts in with `data-guard-edits`. Its form state is recorded when it
// opens; Esc or the × close button then asks "Discard your changes?" if
// anything changed. Cancel, a successful save and every programmatic close()
// close without asking: those are deliberate. Dialogs that edit nothing (RSoP,
// export review, previews, catalogues) carry no marker and close freely.

const UNTRACKED_INPUT_TYPES = new Set([
  "hidden",
  "button",
  "submit",
  "reset",
  "image",
]);

function controlType(control) {
  const tag = String(control.tagName || "").toLowerCase();
  return tag === "input" ? String(control.type || "text").toLowerCase() : tag;
}

function isTracked(control) {
  const type = controlType(control);
  if (type === "select" || type === "textarea") return true;
  return (
    String(control.tagName || "").toLowerCase() === "input" &&
    !UNTRACKED_INPUT_TYPES.has(type)
  );
}

function controlValue(control) {
  const type = controlType(control);
  if (type === "checkbox" || type === "radio") return Boolean(control.checked);
  if (type === "file")
    return [...(control.files || [])].map((file) => [
      file.name,
      file.size,
      file.lastModified,
    ]);
  if (type === "select" && control.multiple)
    return [...control.options]
      .filter((option) => option.selected)
      .map((option) => option.value);
  return String(control.value ?? "");
}

// One entry per user-editable control, in document order. Adding, removing or
// reordering rows changes the sequence, so it counts as a change. Disabled and
// error states are left out: the app toggles them without the user editing.
export function snapshotControls(controls) {
  return JSON.stringify(
    [...controls].filter(isTracked).map((control) => [
      controlType(control),
      control.name ||
        control.dataset?.field ||
        control.dataset?.elemId ||
        control.id ||
        "",
      control.closest?.("[data-id]")?.dataset.id ?? null,
      controlValue(control),
    ]),
  );
}

export function createEditTracker(readControls) {
  let baseline = null;
  return {
    record() {
      baseline = snapshotControls(readControls());
    },
    clear() {
      baseline = null;
    },
    changed() {
      return baseline !== null && snapshotControls(readControls()) !== baseline;
    },
  };
}

const guards = new Map();

// Content filled in after showModal() (for example rows read from the server)
// is part of the starting state, not an edit. Call this once it is in place.
export function recordDialogBaseline(dialog) {
  guards.get(dialog)?.record();
}

export function hasUnsavedDialogEdits() {
  return [...guards.values()].some((guard) => guard.hasChanges());
}

let unloadGuardInstalled = false;
function installUnloadGuard() {
  if (unloadGuardInstalled) return;
  unloadGuardInstalled = true;
  // The handler stays registered but only asks while an open editing dialog
  // has changes; otherwise the page unloads without a prompt.
  window.addEventListener("beforeunload", (event) => {
    if (!hasUnsavedDialogEdits()) return;
    event.preventDefault();
    event.returnValue = "";
  });
}

// The prompt exists only while it is asking, so an idle dialog's controls are
// exactly the ones it had before this guard.
function buildPrompt(dialog) {
  const overlay = document.createElement("div");
  overlay.className = "discard-prompt";
  overlay.dataset.discardPrompt = "";
  const panel = document.createElement("div");
  panel.className = "discard-prompt-panel";
  panel.setAttribute("role", "alertdialog");
  panel.setAttribute("aria-modal", "true");
  const title = document.createElement("h3");
  title.id = `${dialog.id}-discard-title`;
  title.textContent = "Discard your changes?";
  const description = document.createElement("p");
  description.id = `${dialog.id}-discard-description`;
  description.textContent = "Your changes in this dialog have not been saved.";
  panel.setAttribute("aria-labelledby", title.id);
  panel.setAttribute("aria-describedby", description.id);
  const actions = document.createElement("div");
  actions.className = "discard-prompt-actions";
  const keep = document.createElement("button");
  keep.type = "button";
  keep.textContent = "Keep editing";
  const discard = document.createElement("button");
  discard.type = "button";
  discard.textContent = "Discard";
  actions.append(keep, discard);
  panel.append(title, description, actions);
  overlay.append(panel);
  return { overlay, keep, discard };
}

// Returns null for a dialog without the marker. `close` must be the dialog's
// own (wrapped) close so focus returns to the opener as for any other close.
export function createDialogGuard(dialog, { close, isPending = () => false }) {
  if (!dialog.hasAttribute("data-guard-edits")) return null;
  installUnloadGuard();
  const tracker = createEditTracker(() =>
    dialog.querySelectorAll("input,select,textarea"),
  );
  let prompt = null,
    returnFocus = null;

  function hidePrompt() {
    if (!prompt) return;
    prompt.overlay.remove();
    prompt.content.forEach((child) => {
      child.inert = false;
    });
    prompt = null;
  }
  function keepEditing() {
    hidePrompt();
    const target = returnFocus;
    returnFocus = null;
    if (
      target instanceof HTMLElement &&
      target.isConnected &&
      dialog.contains(target) &&
      !target.disabled &&
      target.getClientRects().length
    )
      target.focus();
    else
      dialog
        .querySelector(
          "input:not([type=hidden]):not([disabled]),select:not([disabled]),textarea:not([disabled])",
        )
        ?.focus();
  }
  function discardChanges() {
    returnFocus = null;
    hidePrompt();
    close();
  }

  const guard = {
    record() {
      tracker.record();
    },
    hasChanges() {
      return dialog.open && tracker.changed();
    },
    promptOpen() {
      return dialog.open && prompt !== null;
    },
    // A save already in flight keeps running when its dialog closes, so
    // closing then is not a discard.
    shouldAsk() {
      return guard.hasChanges() && !isPending();
    },
    ask() {
      if (prompt) return;
      const active = document.activeElement;
      returnFocus = dialog.contains(active) ? active : null;
      // The pending-save announcement stays live; everything else waits.
      const content = [...dialog.children].filter(
        (child) => !child.matches("[data-pending-announcement]"),
      );
      prompt = { ...buildPrompt(dialog), content };
      prompt.keep.addEventListener("click", keepEditing);
      prompt.discard.addEventListener("click", discardChanges);
      content.forEach((child) => {
        child.inert = true;
      });
      dialog.append(prompt.overlay);
      prompt.keep.focus();
    },
    keepEditing,
    opened() {
      returnFocus = null;
      hidePrompt();
      tracker.record();
    },
    // The close event is queued: ignore one that arrives after a reopen.
    closed() {
      if (dialog.open) return;
      returnFocus = null;
      hidePrompt();
      tracker.clear();
    },
  };
  guards.set(dialog, guard);
  return guard;
}
