import { state, updateGpoSummary } from "./state.mjs";
import { showPersistentError } from "./api.mjs";
import { showFormErrors } from "./errors.mjs";

const pendingForms = new WeakMap();

export function captureMutation(form = null) {
  const dialog = form?.closest("dialog") || null;
  const target = dialog?.editorTarget ?? state.current;
  return {
    guid: target?.guid ?? null,
    revision: target?.revision ?? null,
    name: target?.name || "workspace",
    form,
    dialog,
    session: dialog?.editorSession,
  };
}

export function beginMutation(form) {
  if (pendingForms.has(form)) return null;
  const context = captureMutation(form);
  if (context.guid !== (state.current?.guid ?? null)) {
    showFormErrors(form, {
      message:
        "The selected policy changed. Close and reopen this editor before saving.",
    });
    return null;
  }
  context.buttons = [
    ...form.querySelectorAll('button:not([type="button"]):not([type="reset"])'),
  ].map((button) => ({ button, disabled: button.disabled }));
  context.busy = form.getAttribute("aria-busy");
  pendingForms.set(form, context);
  context.buttons.forEach(({ button }) => {
    button.disabled = true;
  });
  form.setAttribute("aria-busy", "true");
  return context;
}

export function isSameEditor(context) {
  return Boolean(
    context.dialog?.open &&
    context.dialog.editorSession === context.session &&
    (state.current?.guid ?? null) === context.guid,
  );
}

export function updateMutationList(data, renderList) {
  updateGpoSummary(data.gpo);
  renderList();
}

export function completeMutation(context, data, { apply, renderList }) {
  const closeEditor = isSameEditor(context);
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
  if (isSameEditor(context) && onFormError)
    await onFormError(context.form, error, context);
  else showPersistentError(`${context.name}: ${error.message}`);
}

export function endMutation(context) {
  const form = context.form;
  if (!form || pendingForms.get(form) !== context) return;
  pendingForms.delete(form);
  context.buttons.forEach(({ button, disabled }) => {
    button.disabled = disabled;
  });
  if (context.busy === null) form.removeAttribute("aria-busy");
  else form.setAttribute("aria-busy", context.busy);
}
