import { expect, test } from "vitest";
import {
  beginDialogLoad,
  createDialogGuard,
  createEditTracker,
  hasUnsavedDialogEdits,
  requestDialogClose,
  snapshotControls,
} from "../../src/gpo_studio/static/js/dialog-guard.mjs";

function control(tagName, fields = {}) {
  const { row = null, ...rest } = fields;
  return {
    tagName,
    type: tagName === "INPUT" ? "text" : undefined,
    name: "",
    value: "",
    checked: false,
    disabled: false,
    dataset: {},
    closest: (selector) =>
      selector === "[data-id]" && row !== null
        ? { dataset: { id: row } }
        : null,
    ...rest,
  };
}

function tracked(controls) {
  const tracker = createEditTracker(() => controls);
  tracker.record();
  return tracker;
}

test("an unchanged form is not dirty, and reverting an edit clears it", () => {
  const name = control("INPUT", { name: "name", value: "Draft" });
  const tracker = tracked([name, control("TEXTAREA", { name: "description" })]);
  expect(tracker.changed()).toBe(false);
  name.value = "Draft 2";
  expect(tracker.changed()).toBe(true);
  name.value = "Draft";
  expect(tracker.changed()).toBe(false);
});

test("checkbox, select and textarea edits are changes", () => {
  const box = control("INPUT", { type: "checkbox", name: "enabled" });
  const select = control("SELECT", { name: "status", value: "draft" });
  const area = control("TEXTAREA", { name: "query" });
  const tracker = tracked([box, select, area]);
  box.checked = true;
  expect(tracker.changed()).toBe(true);
  box.checked = false;
  select.value = "ready";
  expect(tracker.changed()).toBe(true);
  select.value = "draft";
  area.value = "SELECT *";
  expect(tracker.changed()).toBe(true);
});

test("adding, removing and reordering rows are changes", () => {
  const first = control("INPUT", { dataset: { field: "sid" }, value: "S-1" });
  const second = control("INPUT", { dataset: { field: "sid" }, value: "S-2" });
  const rows = [first, second];
  const tracker = tracked(rows);
  rows.push(control("INPUT", { dataset: { field: "sid" } }));
  expect(tracker.changed()).toBe(true);
  rows.pop();
  expect(tracker.changed()).toBe(false);
  rows.pop();
  expect(tracker.changed()).toBe(true);
  rows.push(second);
  rows.reverse();
  expect(tracker.changed()).toBe(true);
});

test("rows with equal values but different stored identities differ", () => {
  const rows = [
    control("INPUT", { dataset: { field: "value" }, value: "x", row: "a" }),
    control("INPUT", { dataset: { field: "value" }, value: "x", row: "b" }),
  ];
  const tracker = tracked(rows);
  rows.reverse();
  expect(tracker.changed()).toBe(true);
});

test("hidden inputs, buttons and disabled state are not tracked", () => {
  const hidden = control("INPUT", { type: "hidden", name: "scope" });
  const submit = control("INPUT", { type: "submit", value: "Save" });
  const button = control("BUTTON", { value: "cancel" });
  const field = control("INPUT", { name: "name" });
  const tracker = tracked([hidden, submit, button, field]);
  hidden.value = "user";
  submit.value = "Saving…";
  field.disabled = true;
  expect(tracker.changed()).toBe(false);
  expect(snapshotControls([hidden, submit, button])).toBe("[]");
});

test("multiple selects and file inputs compare by their selection", () => {
  const options = [
    { value: "a", selected: true },
    { value: "b", selected: false },
  ];
  const multi = control("SELECT", { multiple: true, options });
  const file = control("INPUT", { type: "file", files: [] });
  const tracker = tracked([multi, file]);
  options[1].selected = true;
  expect(tracker.changed()).toBe(true);
  options[1].selected = false;
  file.files = [{ name: "estate.json", size: 12, lastModified: 1 }];
  expect(tracker.changed()).toBe(true);
});

test("a tracker without a recorded baseline reports no change", () => {
  const field = control("INPUT", { value: "x" });
  const tracker = createEditTracker(() => [field]);
  expect(tracker.changed()).toBe(false);
  tracker.record();
  field.value = "y";
  expect(tracker.changed()).toBe(true);
  tracker.clear();
  expect(tracker.changed()).toBe(false);
});

test("a dialog without the editing marker gets no guard", () => {
  const dialog = { hasAttribute: () => false };
  expect(createDialogGuard(dialog, { close: () => {} })).toBeNull();
  expect(hasUnsavedDialogEdits()).toBe(false);
});

test("an unguarded dialog closes directly and has no load to hold", () => {
  const closed = [];
  const dialog = { close: (value) => closed.push(value) };
  requestDialogClose(dialog);
  expect(closed).toEqual(["cancel"]);
  expect(() => beginDialogLoad(dialog)()).not.toThrow();
});
