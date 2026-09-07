// Colour theme. A classic script loaded synchronously from <head>, because the
// resolved theme must be on <html> before first paint — a module script runs
// after parse and flashes the light palette at every dark-mode load — and the
// CSP (script-src 'self') rules out an inline bootstrap.
//
// Three states, one honest default: "auto" follows the operating system and is
// what everyone gets until they choose otherwise. The choice persists in
// localStorage; CSS only ever sees the *resolved* theme via
// html[data-theme="light"|"dark"], so the stylesheet needs a single dark block
// and no media query.
(function themeBoot() {
  "use strict";
  var STORAGE_KEY = "gpo-studio-theme";
  var MODES = ["auto", "dark", "light"];
  var media = window.matchMedia("(prefers-color-scheme: dark)");

  function storedMode() {
    var value = null;
    try {
      value = window.localStorage.getItem(STORAGE_KEY);
    } catch {
      // Storage can be denied (privacy mode); auto is the right answer then.
    }
    return MODES.includes(value) ? value : "auto";
  }

  function resolve(mode) {
    if (mode === "auto") return media.matches ? "dark" : "light";
    return mode;
  }

  function apply(mode) {
    document.documentElement.dataset.theme = resolve(mode);
  }

  function label(mode) {
    return mode === "auto" ? "Auto" : mode === "dark" ? "Dark" : "Light";
  }

  var mode = storedMode();
  // Set by the toggle wiring below; a mode change from any source (click,
  // system, another tab) funnels through here so the button never lies.
  var renderToggle = function () {};
  apply(mode);

  function followSystem() {
    if (mode === "auto") apply(mode);
  }
  if (typeof media.addEventListener === "function") {
    media.addEventListener("change", followSystem);
  } else if (typeof media.addListener === "function") {
    // Older engines (Safari <14) only ship the deprecated form. Missing it
    // would not break theming — apply() already ran — but auto would stop
    // following the system until reload.
    media.addListener(followSystem);
  }

  // An explicit choice in one tab reaches the app's other tabs; without this
  // they keep the stale palette until reload.
  window.addEventListener("storage", function followOtherTabs(event) {
    if (event.key !== STORAGE_KEY) return;
    mode = MODES.includes(event.newValue) ? event.newValue : "auto";
    apply(mode);
    renderToggle();
  });

  document.addEventListener("DOMContentLoaded", function wireToggle() {
    var toggle = document.getElementById("theme-toggle");
    if (!toggle) return;
    renderToggle = function render() {
      toggle.textContent = label(mode);
      toggle.setAttribute(
        "aria-label",
        "Colour theme: " +
          (mode === "auto" ? "automatic" : mode) +
          ". Activate to change.",
      );
    };
    renderToggle();
    toggle.addEventListener("click", function cycle() {
      mode = MODES[(MODES.indexOf(mode) + 1) % MODES.length];
      try {
        window.localStorage.setItem(STORAGE_KEY, mode);
      } catch {
        // An unpersisted choice still applies for this page's lifetime.
      }
      apply(mode);
      renderToggle();
    });
  });
})();
