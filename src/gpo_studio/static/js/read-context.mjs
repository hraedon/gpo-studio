import { state } from "./state.mjs";
import { captureErrorContext, showPersistentError } from "./api.mjs";

// UI ownership only. Catalogue and collection cache admission remain domain
// decisions, and dispatched writes retain their mutation outcome contracts.
const scopes = new Set(),
  recoveries = new Map(),
  ownerVersions = new WeakMap();
const dialogs = [];
let serial = 0,
  recoveryScheduled = false;
function scheduleRecovery() {
  if (
    !recoveries.size ||
    recoveryScheduled ||
    typeof requestAnimationFrame !== "function"
  )
    return;
  recoveryScheduled = true;
  requestAnimationFrame(() => {
    recoveryScheduled = false;
    void resumeReadViews();
  });
}
export function readDialogOpened(dialog) {
  const at = dialogs.indexOf(dialog);
  if (at >= 0) dialogs.splice(at, 1);
  dialogs.push(dialog);
}
export function readDialogClosed() {
  for (let at = dialogs.length - 1; at >= 0; at--)
    if (!dialogs[at].open) dialogs.splice(at, 1);
  scheduleRecovery();
}
export async function resumeReadViews() {
  const top = dialogs.filter((dialog) => dialog.open).at(-1),
    tasks = [];
  for (const [key, recovery] of recoveries) {
    if (ownerVersions.get(key) !== recovery.order || !recovery.isCurrent()) {
      recoveries.delete(key);
      continue;
    }
    if (top && top !== recovery.dialog) continue;
    recoveries.delete(key);
    // GET view loaders only. Carry the old error/editor authority and spend the
    // original budget; an automatic retry cannot mint a warning/focus lease.
    tasks.push(
      // Start while the checked view still owns dispatch. The loader captures
      // its own scope synchronously; later intents retire its continuation.
      (async () =>
        recovery.run({
          errorAuthority: recovery.errorAuthority,
          recoveryBudget: 0,
          resume: true,
        }))().catch(recovery.reportError),
    );
  }
  await Promise.all(tasks);
  return tasks.length;
}
export function invalidateReadViews() {
  for (const scope of scopes) scope.invalidate();
}
export function createReadScope({
  element = () => null,
  reactivate = null,
} = {}) {
  let intent = 0,
    pending = null,
    lastOwner = null;
  const scope = {
    cancelReactivation() {
      const key = lastOwner || scope;
      ownerVersions.set(key, ++serial);
      recoveries.delete(key);
    },
    invalidate() {
      intent++;
      pending?.finish();
      scope.cancelReactivation();
    },
    start(options) {
      scope.invalidate();
      return scope.capture(options);
    },
    capture({
      isCurrent = () => true,
      report = (error) => showPersistentError(error.message ?? String(error)),
      errorAuthority = null,
      recoveryBudget = 1,
    } = {}) {
      const mine = intent,
        editor = state.editorGeneration;
      const owner = element(),
        dialog = owner?.closest?.("dialog");
      const session = dialog?.editorSession;
      lastOwner = owner;
      const key = owner || scope,
        order = ++serial;
      ownerVersions.set(key, order);
      const authority =
        errorAuthority ||
        Object.freeze({ editor, context: captureErrorContext() });
      let completed = false;
      const ownsResources = () =>
        !owner ||
        (element() === owner &&
          owner.isConnected !== false &&
          (!dialog || dialog.editorSession === session));
      const viewCurrent = () =>
        mine === intent &&
        (!owner ||
          (element() === owner &&
            owner.isConnected !== false &&
            !owner.hidden &&
            !owner.closest?.("[hidden]") &&
            (!dialog || (dialog.open && dialog.editorSession === session)))) &&
        isCurrent();
      const current = () => editor === state.editorGeneration && viewCurrent();
      const reportError = authority.context.reporter(
        () => current() && authority.editor === state.editorGeneration,
        report,
      );
      const complete = () => {
        if (completed) return;
        completed = true;
        if (
          !reactivate ||
          recoveryBudget <= 0 ||
          editor === state.editorGeneration ||
          !viewCurrent() ||
          ownerVersions.get(key) !== order
        )
          return;
        recoveries.set(key, {
          order,
          dialog,
          isCurrent: viewCurrent,
          run: reactivate,
          errorAuthority: authority,
          reportError,
        });
        scheduleRecovery();
      };
      return {
        isCurrent: current,
        reportError,
        complete,
        errorAuthority: authority,
        recoveryBudget,
        beginPending({
          controls = [],
          busy = [],
          loading = [],
          onFinish = () => {},
        } = {}) {
          // Publication can retire while the same operation still owns its UI
          // resources. A successor first releases the old lease, then captures
          // restored flags; an old finalizer cannot unlock the successor.
          if (!current())
            return {
              finish: () => false,
              message: () => false,
              isCurrent: () => false,
            };
          pending?.finish();
          const flags = controls.map((element) => [element, element.disabled]);
          const attributes = busy.map((element) => [
            element,
            element.getAttribute("aria-busy"),
          ]);
          const messages = new Map();
          const setMessage = (spec, message) => {
            const doc = spec.element.ownerDocument;
            const node =
              spec.className || spec.retry
                ? doc.createElement("div")
                : doc.createTextNode(message);
            if (spec.className || spec.retry) {
              node.className = spec.className || "";
              node.textContent = message;
            }
            spec.element.replaceChildren(node);
            messages.set(spec.element, { ...spec, node });
          };
          const lease = {
            isCurrent: () => pending === lease && ownsResources(),
            message(element, message) {
              if (pending !== lease || !current() || !messages.has(element))
                return false;
              setMessage(messages.get(element), message);
              return true;
            },
            finish() {
              if (pending !== lease) return false;
              pending = null;
              if (!ownsResources()) return false;
              flags.forEach(([element, disabled]) => {
                element.disabled = disabled;
              });
              attributes.forEach(([element, value]) => {
                if (value === null) element.removeAttribute("aria-busy");
                else element.setAttribute("aria-busy", value);
              });
              // A published result/error or a reset has replaced the exact
              // loading node. Only our still-present indicator gets recovery.
              for (const spec of messages.values()) {
                if (spec.node.parentNode === spec.element) {
                  if (spec.recovery) {
                    setMessage(spec, spec.recovery);
                    if (spec.retry) {
                      const button =
                        spec.element.ownerDocument.createElement("button");
                      button.type = "button";
                      button.textContent = spec.retryLabel || "Retry";
                      button.addEventListener("click", spec.retry);
                      messages.get(spec.element).node.append(" ", button);
                    }
                  } else spec.node.remove();
                }
              }
              onFinish();
              return true;
            },
          };
          pending = lease;
          flags.forEach(([element]) => {
            element.disabled = true;
          });
          attributes.forEach(([element]) =>
            element.setAttribute("aria-busy", "true"),
          );
          loading.forEach((spec) => setMessage(spec, spec.message));
          return lease;
        },
      };
    },
  };
  scopes.add(scope);
  return scope;
}
