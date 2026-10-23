import { state, updateGpoSummary } from "./state.mjs";
import { showPersistentError } from "./api.mjs";
import { showFormErrors } from "./errors.mjs";

const pendingForms = new WeakMap();
const pendingAnnouncements = new WeakMap();

export function captureMutation(form = null) {
  const dialog = form?.closest("dialog") || null;
  const target = dialog?.editorTarget ?? state.current;
  return {
    guid: target?.guid ?? null,
    revision: target?.revision ?? null,
    viewGuid: target?.guid ?? null,
    name: target?.name || "workspace",
    form,
    dialog,
    session: dialog?.editorSession,
  };
}

export function resetEditor(form) {
  const pending = pendingForms.get(form);
  if (pending) endMutation(pending);
  const dialog = form.closest("dialog");
  if (dialog) dialog.editorSession = Symbol("editor reset");
}

export function beginMutation(form, { target = null, workspace = false } = {}) {
  if (pendingForms.has(form)) return null;
  const context = captureMutation(form);
  context.workspace = workspace;
  if (!workspace && context.viewGuid !== (state.current?.guid ?? null)) {
    showFormErrors(form, {
      message:
        "The selected policy changed. Close and reopen this editor before saving.",
    });
    return null;
  }
  if (target) {
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

export function isCurrentEditor(context) {
  return Boolean(
    context.dialog?.open && context.dialog.editorSession === context.session,
  );
}

export function isSameEditor(context) {
  return (
    isCurrentEditor(context) &&
    (state.current?.guid ?? null) === context.viewGuid
  );
}

export function updateMutationList(data, renderList) {
  updateGpoSummary(data.gpo);
  renderList();
}

export function completeMutation(context, data, { apply, renderList }) {
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

export function endMutation(context) {
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
}
