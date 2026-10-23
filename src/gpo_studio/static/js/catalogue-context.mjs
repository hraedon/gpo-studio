import { api } from "./api.mjs";

export const CATALOGUE_REVIEW_MESSAGE =
  "The ADMX catalogue changed. Your draft values are kept. Close this editor, reopen the policy, and review its current definition before applying it.";
const listeners = new Set();
const retired = new Set();
let revision = null,
  epoch = 0,
  monitoring = false,
  channel = null;
export const catalogueEpoch = () => epoch;
export const currentCatalogueRevision = () => revision;
export const reviewedCatalogueIsCurrent = (reviewed) =>
  typeof reviewed === "string" && reviewed.length > 0 && reviewed === revision;
export function onCatalogueChange(listener) {
  listeners.add(listener);
}

export function observeCatalogue(data, started = epoch) {
  const incoming = data?.catalogue_revision;
  if (typeof incoming !== "string" || !incoming.trim())
    throw new Error(
      "The server did not identify the ADMX catalogue. Reload and review the policy before applying changes.",
    );
  if (incoming === revision) return { revision, changed: false };
  if (retired.has(incoming) || started !== epoch) return null;
  const previous = revision;
  revision = incoming;
  epoch++;
  if (previous !== null) {
    retired.add(previous);
    for (const listener of listeners) listener(revision);
  }
  return { revision, changed: previous !== null };
}

export function observeCatalogueConflict(error, started) {
  if (error.code !== "catalogue_conflict") return;
  const current = error.payload?.error?.current_catalogue_revision;
  if (typeof current === "string" && current.trim())
    observeCatalogue({ catalogue_revision: current }, started);
}

async function refreshCatalogue() {
  const started = epoch;
  try {
    observeCatalogue(await /* request: catalogue.monitor */ api("/api/health"), started);
  } catch {
    /* A failed refresh cannot authorize a mutation or replace a token. */
  }
}

export function announceCatalogueChange() {
  channel?.postMessage("refresh");
}
export function initCatalogueMonitoring() {
  if (monitoring || typeof window === "undefined") return;
  monitoring = true;
  window.addEventListener("focus", refreshCatalogue);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refreshCatalogue();
  });
  if (typeof BroadcastChannel !== "undefined") {
    channel = new BroadcastChannel("gpo-studio-catalogue");
    channel.onmessage = refreshCatalogue;
  }
}
