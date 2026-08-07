# Plan 029 — Group Policy Modeling, Results, and impact parity

Status: `rsop.py` is **certified in the measured regions and surfaced**
(2026-08-06, WI-030). The condition this line used to state has been met in the
order [`docs/domain-layer-status.md`](../docs/domain-layer-status.md) requires:
the Plan 033 RSOP oracle validated the module against `gpresult` on a real
Windows 11 26200 client first, and only then was it wired to
`POST /api/rsop/compute` and `POST /api/rsop/compare`.

**Certified means twelve scenarios, not the plan.** LSDOU ordering, link order,
inheritance and its blocking, enforcement, disabled links and sides, security
filtering including denies on both Apply and Read, user scope, and loopback
merge and replace. Slow link and safe mode are accepted and never read (WI-036),
WQL is not evaluated and never will be by Studio (WI-035), and the per-side
applied/denied sets are still collapsed (WI-032). The workpackages below go far
beyond that: WP-1's DC modelling connector, WP-2's remote Results collection and
WP-3's impact analysis are **not implemented**, and nothing in this status line
claims otherwise.

The rest of this plan is therefore still a plan. What changed is that its
central module is no longer an unproven draft.

Scope: match GPMC Modeling/Results workflows and add evidence-bounded impact
analysis without overstating offline simulation
Depends on: Plans 023, 025, and 028 reporting
Review gate: **REVIEW AND REFINE — REQUIRED before predictive gating**

## WP-1 — Group Policy Modeling connector

- Invoke supported domain-controller RSoP planning interfaces with explicit
  user/computer/container, site, group, WMI, loopback, slow-link, and filtering
  assumptions supported by the target API.
- Store query inputs, DC/tool versions, permissions, time, and raw/normalized
  output as evidence.
- Show winning GPO and precedence chains without claiming local GPO coverage.

## WP-2 — Group Policy Results connector

- Collect remote Resultant Set data for eligible user/computer pairs using
  dedicated delegated permissions and explicit endpoint reachability.
- Ingest `gpresult`/GPMC HTML/XML where supported and retain raw evidence.
- Distinguish intended, modeled, logged/applied, and observed-behavior states.

## WP-3 — Reports and saved-query workflows

- Match GPMC query inventory, rerun, compare, export, permissions, and saved
  report workflows in a web-native experience.
- Add semantic before/after comparisons and winning/losing setting explanations
  across all adapters that expose RSoP evidence.
- Mark adapters/runtime inputs that RSoP cannot conclusively evaluate.

## WP-4 — Bounded impact engine

- Combine topology, links, security tokens, WMI/ILT evidence, loopback, Modeling,
  Results, endpoint observations, and replication state.
- Use `known-applies`, `known-does-not-apply`, and `unknown`; never turn missing
  runtime evidence into a confident decision.
- Estimate blast radius with provenance and freshness on every conclusion.

## Acceptance gates

- Modeling and Results match GPMC for the reference estate and permissions.
- Every conclusion links to query inputs, raw evidence, source, and freshness.
- Known GPMC limitations, including omitted local GPOs in Modeling, are visible.
- Reports never conflate simulation with actual endpoint application.

## REVIEW AND REFINE — REQUIRED

Run a blinded comparison between Studio, GPMC Modeling, GPMC Results, gpresult,
and endpoint behavior. Review false-positive/negative and unknown rates with
operators. Refine confidence rules before impact results can block/approve Plan
030 publication or support a public parity claim.

