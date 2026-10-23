import { state } from "./state.mjs";
import { captureErrorReporter, showPersistentError } from "./api.mjs";

// UI ownership only. Catalogue and collection cache admission remain domain
// decisions, and dispatched writes retain their mutation outcome contracts.
const scopes = new Set();
export function invalidateReadViews() {
  for (const scope of scopes) scope.invalidate();
}
export function createReadScope({ element = () => null } = {}) {
  let intent = 0;
  const scope = {
    invalidate() {
      intent++;
    },
    start(options) {
      scope.invalidate();
      return scope.capture(options);
    },
    capture({
      isCurrent = () => true,
      report = (error) => showPersistentError(error.message ?? String(error)),
    } = {}) {
      const mine = intent,
        editor = state.editorGeneration;
      const owner = element(),
        dialog = owner?.closest?.("dialog");
      const session = dialog?.editorSession;
      const current = () =>
        mine === intent &&
        editor === state.editorGeneration &&
        (!owner ||
          (element() === owner &&
            owner.isConnected !== false &&
            !owner.hidden &&
            !owner.closest?.("[hidden]") &&
            (!dialog || (dialog.open && dialog.editorSession === session)))) &&
        isCurrent();
      return {
        isCurrent: current,
        reportError: captureErrorReporter(current, report),
      };
    },
  };
  scopes.add(scope);
  return scope;
}
