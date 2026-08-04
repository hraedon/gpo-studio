# Open work items

Numbered `WI-nnn` items that are **open**. Closed ones are not listed here —
they are recorded in `CHANGELOG.md` and in the plan or design doc that closed
them, which is where their evidence lives.

This register exists because there wasn't one. WI numbers were being minted in
commit messages, plan documents, design notes and source comments, with no place
that answered "what is still open?". WI-025 was written down in
`plan-033/rsop-oracle-design.md` in July and found again in August only because
someone re-read that paragraph — it had never been anywhere a person would look
for outstanding work. A number that exists in exactly one prose paragraph is a
note, not a work item.

**Adding one:** take the next free number (grep for `WI-0` across the repo,
including source comments), add a row here, and say what would close it. An item
whose closing condition is not stated cannot be closed, only forgotten.

---

## WI-025 — candidate artifacts are not hash-bound in the WP-1B and endpoint lanes

**Opened:** 2026-07 (`plan-033/rsop-oracle-design.md`).
**Status:** open for WP-1B and the endpoint lane. **Closed for WP-6B**
(2026-08-04) — that lane's verdict records SHA-256 for `topology.json`,
`prediction.json` and `expected.json`, and additionally proves the guest built
the topology the prediction describes by comparing the pulled copy byte for
byte.

A verdict that names the artifact it compared against, without hashing it,
asserts a comparison nobody can re-check. The endpoint lane already takes
`--candidate-root`, so it never had the guest-supplied-expectation defect, but
it records no candidate hashes either.

**Closes when:** `finalize_endpoint_run.py` and `finalize_wp1b_run.py` record
candidate hashes, and one re-certification run per lane is produced under the
change. WP-6B's implementation is the model.

## WI-028 — `SearchedSOM` accumulates SOMs for deleted containers

**Opened:** 2026-08-04 (WP-6B, `plan-033/wp6b-results.md`).
**Status:** open. Observed; mechanism not established.

The `SearchedSOM` section of a `gpresult /x /scope:computer` document listed 24
entries on the estate client, including OUs from all three WP-6B runs that day
*and* `GPOStudioLab-*` OUs from the endpoint lane's runs the previous day. Every
one of those OUs had been deleted, and each run's teardown verified their
absence by re-querying the directory. The applied-GPO list in the same document
does **not** behave this way — it is current.

Why this matters rather than being a curiosity: `SearchedSOM` carries `Order`,
`BlocksInheritance`, `Blocked` and `Reason`, which is Windows' own precedence
accounting and the obvious oracle for the block-inheritance and enforcement
cases WP-6's topology section asks for. A lane built on it today would read rows
for containers that no longer exist and were never searched in that run, and
could "confirm" a block-inheritance prediction against an OU from a previous
experiment.

**Closes when:** the persistence mechanism is established (RSoP WMI namespace
retention is the first hypothesis, untested) *and* a read can be scoped to a
single run — or, failing that, when the results doc and any lane using that
section carry an explicit warning and a scoping strategy.

**Do not** build the enforcement/block-inheritance oracle on `SearchedSOM`
before this is closed.

## WI-029 — `disabled-block-enforced` is one assertion away from being WP-6B-runnable

**Opened:** 2026-08-04 (WP-6A).
**Status:** open. Corpus authoring, not a defect.

Every expected winner in that scenario is HKLM except one: the
`Studio-RSOP-UserSideOff` assertion that `HKCU\Software\Policies\StudioLab\UserVal`
is absent, which needs a user-scope capture and so belongs to WP-9. The scenario
is therefore blocked in full, and WP-6B's corpus is a single scenario as a
result.

Relocating that one assertion into its own WP-9 scenario would double WP-6B's
corpus at the cost of authoring one small scenario. It was deliberately not done
inline with the registry reconciliation: it is scenario authoring with real
judgement about Windows behaviour in the expected values, and WP-6B's captured
document is now available to author against rather than guess from.

**Closes when:** the user-side assertion moves to a WP-9 scenario — *relocated,
never deleted* — and `disabled-block-enforced` runs green in WP-6B.

## WI-030 — `rsop.py` is reachable from no API endpoint

**Opened:** 2026-08-04 (recording a long-standing state, not a new one).
**Status:** open by design, pending evidence.

WP-6B gave the module its first external validation, but only for LSDOU
ordering, same-container link order and non-conflicting inheritance, on the
computer side. Security filtering, WMI filters, block inheritance, enforcement,
user scope and loopback are all unverified.

**Closes when:** the capability matrix can drop all three of its current
qualifiers — scope (WP-9), coverage (the blocked corpus scenarios), and a
decision that surfacing is wanted. It is listed here so that "WP-6 passed" is
never mistaken for "RSOP is a feature".

---

## Not yet numbered

Open question 1 from `plan-033/rsop-oracle-design.md` — whether `LabMS01` can
reach `LabCL01` over the private switch for RPC/WMI — remains untested. WP-6B
never needed it, since `gpresult.exe` on the client is sufficient for computer
scope. It gets a number if and when WP-9 depends on it.
