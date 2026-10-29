import {
  state,
  updateGpoSummary,
  getGpoGeneration,
  isGpoObservationCurrent,
} from "./state.mjs";
import { showPersistentError } from "./api.mjs";
import { showFormErrors } from "./errors.mjs";
import { refreshDialogPrompt } from "./dialog-guard.mjs";

const pendingForms = new WeakMap();
const pendingAnnouncements = new WeakMap();
// Every request between beginMutation() and its own endMutation(). Unlike
// pendingForms, a reopened editor does not release it: the request is still
// in flight after its form has moved on to a new editing session.
const inFlight = new Set();

export function captureMutation(form = null) {
  const dialog = form?.closest("dialog") || null;
  const target = dialog?.editorTarget ?? state.current;
  return {
    guid: target?.guid ?? null,
    revision: target?.revision ?? null,
    viewGuid: target?.guid ?? null,
    generation: target?.generation ?? getGpoGeneration(target?.guid),
    viewGeneration: target?.generation ?? getGpoGeneration(target?.guid),
    name: target?.name || "workspace",
    form,
    dialog,
    session: dialog?.editorSession,
  };
}

export function resetEditor(form) {
  const pending = pendingForms.get(form);
  if (pending) releaseForm(pending);
  const dialog = form.closest("dialog");
  if (dialog) dialog.editorSession = Symbol("editor reset");
}

export function beginMutation(form, { target = null, workspace = false } = {}) {
  if (pendingForms.has(form)) return null;
  const context = captureMutation(form);
  context.workspace = workspace;
  if (!workspace && context.generation !== getGpoGeneration(context.guid)) {
    showFormErrors(form, {
      message:
        "This policy was deleted. Your unsaved values are retained. Close and reopen the editor to review an existing target before saving.",
    });
    return null;
  }
  if (!workspace && context.viewGuid !== (state.current?.guid ?? null)) {
    showFormErrors(form, {
      message:
        "The selected policy changed. Close and reopen this editor before saving.",
    });
    return null;
  }
  if (target) {
    if (target.generation !== getGpoGeneration(target.guid)) {
      showFormErrors(form, {
        message:
          "The target policy was deleted. Your unsaved values are retained. Close and reopen this editor to review its target.",
      });
      return null;
    }
    context.generation = target.generation;
    context.guid = target.guid;
    context.revision = target.revision;
    context.name = target.name;
  }
  context.fieldNames = [
    ...new Set(
      [...form.querySelectorAll("input,textarea,select")]
        .filter((control) => !control.disabled)
        .map(
          (control) =>
            control.name ||
            control.dataset.elemId ||
            control.dataset.field ||
            control.id,
        )
        .filter(Boolean),
    ),
  ];
  context.controls = [
    ...form.querySelectorAll(
      'input:not([type="hidden"]),textarea,select,button',
    ),
  ]
    .filter((control) => !control.matches('button[value="cancel"]'))
    .map((control) => ({ control, disabled: control.disabled }));
  context.busy = form.getAttribute("aria-busy");
  pendingForms.set(form, context);
  inFlight.add(context);
  const pendingMessage = document.createElement("p");
  pendingMessage.className = "form-pending form-hint";
  pendingMessage.setAttribute("role", "note");
  pendingMessage.textContent =
    "Saving changes… Closing this dialog keeps the save running.";
  (form.querySelector(":scope > .dialog-body") || form).prepend(pendingMessage);
  context.pendingMessage = pendingMessage;
  context.controls.forEach(({ control }) => {
    control.disabled = true;
  });
  form.setAttribute("aria-busy", "true");
  const announcement = context.dialog?.querySelector(
    ":scope > [data-pending-announcement]",
  );
  if (announcement) {
    context.announcement = announcement;
    pendingAnnouncements.set(announcement, context);
    announcement.textContent = pendingMessage.textContent;
  }
  return context;
}

// The form's current editor is waiting for its own save.
export function isMutationPending(form) {
  return pendingForms.has(form);
}

// Any save started from this form is unresolved, whichever editor owns it now.
export function hasMutationInFlight(form) {
  for (const context of inFlight) if (context.form === form) return true;
  return false;
}

export function isCurrentEditor(context) {
  return Boolean(
    context.dialog?.open && context.dialog.editorSession === context.session,
  );
}

export function isSameEditor(context) {
  return (
    isCurrentEditor(context) &&
    (state.current?.guid ?? null) === context.viewGuid &&
    context.viewGeneration === getGpoGeneration(context.viewGuid) &&
    context.generation === getGpoGeneration(context.guid)
  );
}

export function updateMutationList(data, renderList) {
  if (!updateGpoSummary(data.gpo)) return false;
  renderList();
  return true;
}

export function completeMutation(context, data, { apply, renderList }) {
  if (
    context.generation !== getGpoGeneration(context.guid) ||
    !isGpoObservationCurrent(data.gpo)
  )
    return false;
  const closeEditor = isCurrentEditor(context);
  const current = state.current;
  const canApply =
    current?.guid === context.guid &&
    current.revision === context.revision &&
    data.gpo.guid === context.guid;
  if (closeEditor) context.dialog.close();
  updateMutationList(data, renderList);
  if (canApply) apply(data);
  return canApply;
}

export async function mutationFailed(context, error, onFormError) {
  if (
    isCurrentEditor(context) &&
    (context.workspace || isSameEditor(context)) &&
    onFormError
  )
    await onFormError(context.form, error, context);
  else showPersistentError(`${context.name}: ${error.message}`);
}

// Called once the request has settled.
export function endMutation(context) {
  inFlight.delete(context);
  releaseForm(context);
}

// Give the form back to its editor: the request may still be in flight.
function releaseForm(context) {
  const form = context.form;
  if (!form || pendingForms.get(form) !== context) return;
  pendingForms.delete(form);
  context.pendingMessage?.remove();
  if (
    context.announcement &&
    pendingAnnouncements.get(context.announcement) === context
  ) {
    pendingAnnouncements.delete(context.announcement);
    context.announcement.textContent = "";
  }
  context.controls.forEach(({ control, disabled }) => {
    control.disabled = disabled;
  });
  if (context.busy === null) form.removeAttribute("aria-busy");
  else form.setAttribute("aria-busy", context.busy);
  if (context.dialog) refreshDialogPrompt(context.dialog);
}
