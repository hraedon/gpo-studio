import { expect, test } from "vitest";
import {
  catalogueEpoch,
  currentCatalogueRevision,
  observeCatalogue,
  reviewedCatalogueIsCurrent,
  onCatalogueChange,
} from "../../src/gpo_studio/static/js/catalogue-context.mjs";

test("opaque catalogue observations reject delayed and retired snapshots, including the first response race", () => {
  const started = catalogueEpoch(),
    changes = [];
  onCatalogueChange((revision) => changes.push(revision));
  expect(
    observeCatalogue({ catalogue_revision: "newer-first" }, started).revision,
  ).toBe("newer-first");
  expect(
    observeCatalogue({ catalogue_revision: "older-delayed" }, started),
  ).toBeNull();
  const next = catalogueEpoch();
  expect(observeCatalogue({ catalogue_revision: "latest" }, next).changed).toBe(
    true,
  );
  expect(
    observeCatalogue({ catalogue_revision: "newer-first" }, catalogueEpoch()),
  ).toBeNull();
  expect(currentCatalogueRevision()).toBe("latest");
  expect(changes).toEqual(["latest"]);
  expect(reviewedCatalogueIsCurrent("newer-first")).toBe(false);
  expect(reviewedCatalogueIsCurrent("latest")).toBe(true);
  expect(() => observeCatalogue({})).toThrow(/did not identify/);
});
