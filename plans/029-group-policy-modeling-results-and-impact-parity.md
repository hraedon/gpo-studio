# Plan 029 — Group Policy Modeling, Results, and impact parity

Status: proposed (post-1.0)
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

