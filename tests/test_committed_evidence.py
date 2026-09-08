"""The committed verdicts are the reviewable product; check they are coherent.

A verdict under `docs/plan-033/` is what a reviewer reads instead of re-running
a lane, so its internal claims have to hold up without the estate. These are the
checks a reviewer would otherwise have to do by eye, and one of them exists
because a reviewer did it by eye and reached a wrong conclusion: `source.files`
had acquired entries (`candidate/candidate.zip`) that are generated artifacts
rather than repository files, so resolving the block against `source.commit`
failed and the verdict looked malformed. The block now contains only what its
name promises, and this pins that.
"""

from __future__ import annotations

import hashlib
import json
import runpy
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

REPO_ROOT = Path(__file__).parents[1]
ORACLE_DIR = REPO_ROOT / "scripts" / "windows-oracle"
EVIDENCE = REPO_ROOT / "docs" / "plan-033"

#: committed verdict -> the finalizer whose tables define its bound file set.
#:
#: The RSOP lanes were absent from this map until 2026-08-04, so their committed
#: verdicts -- the ones a reviewer reads instead of re-running the lane -- were
#: the only ones nothing checked. Their finalizers use flat file tables rather
#: than the transport-keyed shape the older lanes use, and that difference is
#: what kept them out; `_bound_names` now handles both rather than the map
#: quietly covering three lanes out of five.
LANE_VERDICTS = {
    "wp1b-evidence/verification-estate.json": "finalize_wp1b_run.py",
    "wp1b-evidence/scripts-metadata/verification.json": "finalize_scripts_backup_run.py",
    "wp1b-evidence/publication-completeness/verification.json": (
        "finalize_publication_run.py"
    ),
    # The 2026-09-07 batch. WI-032 gave the model per-side applied sets and
    # promoted the WP-9 comparison to gated, which moved the candidate
    # builder and the user finalizer -- both bound by every RSOP verdict.
    # The gate found a model over-report on its first run (a GPO carrying
    # nothing for a side was reported applied to it) and these runs are
    # after that correction.
    "wp6-evidence/verdict-rsop-observe-20260907221946-2994.json": (
        "finalize_rsop_run.py"
    ),
    "wp6-evidence/verdict-rsop-observe-20260907222055-1770.json": (
        "finalize_rsop_run.py"
    ),
    "wp6-evidence/verdict-rsop-observe-20260907222206-6220.json": (
        "finalize_rsop_run.py"
    ),
    "wp6-evidence/verdict-rsop-observe-20260907222315-2698.json": (
        "finalize_rsop_run.py"
    ),
    "wp6-evidence/verdict-rsop-observe-20260907222431-1389.json": (
        "finalize_rsop_run.py"
    ),
    "wp6-evidence/verdict-rsop-observe-20260907222610-4330.json": (
        "finalize_rsop_run.py"
    ),
    "wp6-evidence/verdict-rsop-observe-20260907222726-4256.json": (
        "finalize_rsop_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260907220844-4855.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260907221020-2710.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260907221153-8426.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260907221411-3148.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260907221626-5555.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260907221808-9398.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp2-evidence/verification-estate.json": "finalize_wp2_import_run.py",
    "wp3-evidence/verification-estate.json": "finalize_wp3_run.py",
    "wp3-evidence/policy-families/dc/verification.json": "finalize_wp3_run.py",
    "wp3-evidence/policy-families/member/verification.json": "finalize_wp3_run.py",
    "wp3-evidence/object-security/verification.json": "finalize_object_security_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804020517-2089.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804051032-8845.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804051228-2926.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804070708-6831.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804151624-6393.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804152957-1430.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804154241-9337.json": "finalize_rsop_run.py",
    "wp9-evidence/verdict-rsop-user-observe-20260804050024-4383.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260804045552-9148.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260804045809-8312.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260804065146-4224.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260804065525-9254.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260804150527-3868.json": (
        "finalize_rsop_user_run.py"
    ),
    # WP-6B's first certification and the WI-031 enforcement arc. These were
    # committed but never mapped, so nothing checked them -- and the four
    # `finding` verdicts among them could not have been added at all, because
    # the consistency test had no branch for that state and demanded `passed`.
    "wp6-evidence/verdict-rsop-observe-20260804010341-7165.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804010551-9363.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804010738-5543.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804012618-5426.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804012803-7606.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804015016-5317.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804015258-1810.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804015447-4913.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804020109-7624.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804020308-9752.json": "finalize_rsop_run.py",
    # WI-039: the one undeclared finding this lane has produced.
    "wp6-evidence/verdict-rsop-observe-20260804153726-7284.json": "finalize_rsop_run.py",
    # Re-certification 2026-08-05, after the finalizers' harness check was made
    # falsifiable (review finding 3). A certification binds the harness that
    # produced it, so changing the finalizer meant every earlier verdict
    # described code that no longer ships. Ten scenarios, all `pass`.
    "wp6-evidence/verdict-rsop-observe-20260805064008-9181.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805064155-8996.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805064351-9402.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805064540-1562.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805064725-4970.json": "finalize_rsop_run.py",
    # WI-040, both halves of the arc: the `expected-finding` that measured the
    # read-deny gap and the `pass` that certified the fix. Committed together
    # on purpose -- the gap and its closure are only readable as a pair.
    "wp6-evidence/verdict-rsop-observe-20260805045139-3731.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805045851-3883.json": "finalize_rsop_run.py",
    "wp9-evidence/verdict-rsop-user-observe-20260805065203-1562.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805065415-8622.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805065630-6815.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805065943-6615.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805070255-2473.json": (
        "finalize_rsop_user_run.py"
    ),
    # Re-certification 2026-08-05 under the WI-043 result contract. Making
    # `_gpo_filter_status` side-aware changed what a verdict MEANS -- the
    # prediction gained `unevaluable_gpos` and the finalizers stopped grading
    # those rows -- so every earlier verdict describes a harness that no longer
    # ships. Eleven scenarios at `a85736a`, all `pass`, all conclusive. This set
    # also re-earns the two WI-040 verdicts, whose `harness_matches_source` was
    # produced by the self-comparing check fixed in `d1eec72` hours after they
    # ran; those two are kept below as the historical record of the divergence,
    # not as live certifications.
    "wp6-evidence/verdict-rsop-observe-20260805194053-7180.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805194245-3734.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805194432-5944.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805194627-2633.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805194814-2731.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805195001-1590.json": "finalize_rsop_run.py",
    "wp9-evidence/verdict-rsop-user-observe-20260805195149-5629.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805195400-1809.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805195614-1767.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805195909-4033.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805200214-4370.json": (
        "finalize_rsop_user_run.py"
    ),
    # Re-certification 2026-08-05 (second round) at `faad341`, after review
    # round 3 added exhaustive dispatch to the candidate builder. That file is
    # bound BY HASH in `LOCAL_FILES`, so changing it invalidated the `a85736a`
    # set above even though the prediction output is byte-identical -- verified
    # by rebuilding the deny-read candidate and diffing all three artifacts.
    # "The output did not change" is not the rule; a certification binds the
    # harness. Eleven scenarios, all `pass`, all conclusive.
    "wp6-evidence/verdict-rsop-observe-20260805220819-4762.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805221004-8571.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805221150-4243.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805221335-1702.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805221522-1983.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805221707-4871.json": "finalize_rsop_run.py",
    "wp9-evidence/verdict-rsop-user-observe-20260805221856-6415.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805222106-2378.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805222317-3382.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805222624-9750.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805222929-6350.json": (
        "finalize_rsop_user_run.py"
    ),
    # WI-043's measurement, `inconclusive`, mapped here anyway: the freshness
    # gate is about whether a verdict binds the harness that ships, not about
    # whether it was a pass. An unmapped verdict is the one shape the coverage
    # guard exists to catch. Retired below now that WI-047 moved the harness.
    "wp9-evidence/verdict-rsop-user-observe-20260806165543-8004.json": (
        "finalize_rsop_user_run.py"
    ),
    # 2026-08-06 re-certification: all twelve scenarios re-run under the WI-047
    # model, so what these bind is the reading-principal rule rather than the
    # abstention it replaced.
    "wp6-evidence/verdict-rsop-observe-20260806181033-3296.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260906045316-1301.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260806181222-5315.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260906045428-3847.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260806181411-9752.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260906045536-1696.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260806181600-5707.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260906045643-6646.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260806181748-7763.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260906045750-7576.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260806181935-5130.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260906045858-7209.json": "finalize_rsop_run.py",
    "wp9-evidence/verdict-rsop-user-observe-20260806182125-6983.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260906051241-1230.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260806182338-3982.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260906051412-9765.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260806182554-1472.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260906051544-3625.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260806182911-5363.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260906051750-5647.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260806183612-5557.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260906052004-2373.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260806184006-2532.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260906052146-2480.json": (
        "finalize_rsop_user_run.py"
    ),
    # 2026-09-06 batch two. WI-037 moved every RSOP lane driver and WI-049 moved
    # the candidate builder, so the twelve verdicts above stopped being
    # certifications the moment those files changed; these re-earn them. Twelve
    # scenarios, all `pass`, all conclusive, from a clean tree at `9f6d775`.
    #
    # Three of them carry measurements this project did not have before:
    # `...184434-8187` is WI-049's fourth read cell (a read deny naming the USER
    # on a computer-scope scenario -- it APPLIED, as the model reasoned), and
    # `...185345-9222` carries both the off-diagonal Apply cell and the first
    # group-matched deny any estate run has exercised.
    "wp6-evidence/verdict-rsop-observe-20260906183835-6175.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260906183948-3890.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260906184057-2689.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260906184205-5172.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260906184313-1876.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260906184434-8187.json": "finalize_rsop_run.py",
    "wp9-evidence/verdict-rsop-user-observe-20260906184610-3620.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260906184743-5732.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260906184916-2617.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260906185125-9433.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260906185345-9222.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260906185527-8016.json": (
        "finalize_rsop_user_run.py"
    ),
    # The endpoint lane, mapped for the FIRST time (WI-053). Its certification
    # had been sitting at `wp1b-evidence/endpoint-result-phase4-estate.json`,
    # whose name matches neither prefix the coverage guard globs for, so nothing
    # checked its `source.files`, nothing checked it was internally consistent,
    # and the freshness gate never saw it -- WI-037 changed two files it binds
    # and every RSOP verdict went red while that one stayed silent. This run
    # carries a name the guard matches, and is the lane's first verdict to bind
    # its candidate (WI-025). Committed at `38eedc6`, one commit later than the
    # rest of the batch, because the first attempt found a real defect in the
    # WI-037 change: `$(verify_endpoint)` ran the phase in a subshell, so the
    # EXIT trap ran the whole post-teardown verification a second time.
    "wp6-evidence/verdict-endpoint-observe-20260906185837-7523.json": (
        "finalize_endpoint_run.py"
    ),
    #
    # The WI-054 batch, 2026-09-06 (late). The machine-token group-deny row ran
    # FIRST, from the same tree as the twelve re-certifications below it: the
    # run restarted the client so the machine token carried the group it had
    # just authored, the membership was corroborated from the token and from
    # the directory independently, and Windows agreed with the model -- the
    # group-matched deny blocks on the COMPUTER side exactly as it does on the
    # user side. `...221638-4687` is that measurement. The other six wp6 and
    # six wp9 entries are the lanes' re-certification under the WI-054 change,
    # which retired the twelve 18xxx verdicts below in RETIRED_VERDICTS --
    # `build-rsop-candidate.py` (the dead predicate left in it), the authoring
    # and observation halves, the computer finalizer and both lane drivers all
    # moved in this change.
    "wp6-evidence/verdict-rsop-observe-20260906221638-4687.json": (
        "finalize_rsop_run.py"
    ),
    "wp6-evidence/verdict-rsop-observe-20260906221931-1695.json": (
        "finalize_rsop_run.py"
    ),
    "wp6-evidence/verdict-rsop-observe-20260906223143-4837.json": (
        "finalize_rsop_run.py"
    ),
    "wp6-evidence/verdict-rsop-observe-20260906223251-8863.json": (
        "finalize_rsop_run.py"
    ),
    "wp6-evidence/verdict-rsop-observe-20260906223400-9371.json": (
        "finalize_rsop_run.py"
    ),
    "wp6-evidence/verdict-rsop-observe-20260906223508-6654.json": (
        "finalize_rsop_run.py"
    ),
    "wp6-evidence/verdict-rsop-observe-20260906223619-5576.json": (
        "finalize_rsop_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260906222041-8299.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260906222219-6252.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260906222352-5950.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260906222601-2732.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260906222818-6584.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260906222959-7716.json": (
        "finalize_rsop_user_run.py"
    ),
    # The group-deny scenario's FIRST run, which the lane refused: the client
    # rebooted with the run's policy already linked, the startup CSE applied
    # that policy at boot, and the residual guard correctly declined to
    # attribute an observation taken from a policy key that was not empty. It
    # found a real interaction -- the reboot makes boot-time processing a
    # second applier -- and the fix (boot_applied_values: record, clear,
    # observe from empty) is why the tree moved one commit past the twelve
    # re-certifications' source. Kept retired, not deleted: it is the record
    # of WHY the observe half gained that gate.
    "wp6-evidence/verdict-rsop-observe-20260906221248-7683.json": (
        "finalize_rsop_run.py"
    ),
}

# The original estate verdict predates policy-family serializer bindings. It
# remains useful historical evidence with its exact five-file source set.
HISTORICAL_BOUND_FILES = {
    "wp3-evidence/verification-estate.json": {
        "build-wp3-candidate.py",
        "finalize_wp3_run.py",
        "psdirect.ps1",
        "run-wp3-oracle.sh",
        "run-wp3-security-template.ps1",
    }
}

#: Verdicts committed BEFORE the transport was recorded, kept as history.
#:
#: They are listed rather than skipped by pattern so that adding to this set is
#: a deliberate act with a reason, not something a new file drifts into. The
#: psdirect assertions genuinely cannot apply to them; every other verdict must
#: be mapped above.
#: `wp3-evidence/verification.json` ALSO BINDS AN UNREACHABLE COMMIT
#: (`fdb46004`, run `wp3-security-template-20260727220623-7682`) -- a fifth
#: squash-merge orphan, found 2026-09-06 and recorded in
#: `docs/evidence-binding-audit-2026-08-03.md`. The 2026-08-03 audit missed it
#: because it scanned prose for hex next to the word "commit" and never looked
#: inside the verdict JSON, where a binding actually lives.
#:
#: Nothing rests on it -- WP-3 has a live certification in
#: `verification-estate.json` -- and it cannot be repaired, because the commit
#: is gone. It is noted here rather than in the audit alone so that the next
#: person to widen this exemption knows one of its two members is unverifiable
#: in a second, separate way.
PRE_TRANSPORT_VERDICTS = {
    "wp1b-evidence/verification.json",
    "wp3-evidence/verification.json",
}

#: Verdicts whose harness has MOVED ON, kept as history rather than as claims.
#:
#: A certification binds the harness that produced it, so a verdict is a live
#: claim only while the files it names still hash to what it recorded. When a
#: harness file changes, every verdict bound to the old content stops being a
#: certification and becomes a record of something that happened once. Twice now
#: the RSOP lanes have been fully re-run for exactly this reason -- when the
#: finalizers' harness check was made falsifiable, and when review round 3
#: changed `build-rsop-candidate.py` -- and BOTH times the staleness was caught
#: by a person noticing rather than by a test (WI-045).
#:
#: These are kept deliberately. The operator ruled that `...045139-3731` retains
#: its value because the divergence it observed on a real client does not depend
#: on the harness check; the same argument covers the rest. History that is
#: supposed to be stale is why this cannot simply be "every verdict matches the
#: tree" -- that assertion would fail on day one and get switched off, which is
#: worse than no gate at all.
#:
#: ENUMERATED, never matched by pattern, for the reason `PRE_TRANSPORT_VERDICTS`
#: gives: retiring a certification should be a deliberate act with a reason, not
#: something a file drifts into. And it is not an escape hatch --
#: `test_retired_verdicts_are_genuinely_stale` fails if a verdict listed here
#: still matches the tree, so a live claim cannot be quietly parked in here to
#: silence the gate below.
RETIRED_VERDICTS = {
    # The 2026-09-06 batch, superseded by the 2026-09-07 WI-032 batch. The
    # candidate builder now predicts the side being observed rather than
    # "applied on at least one side", and the WP-9 finalizer gates on the
    # comparison it used to only record, so these verdicts bind a harness
    # that asks a different question. They remain valid for the commits
    # they name.
    "wp6-evidence/verdict-rsop-observe-20260906045316-1301.json",
    "wp6-evidence/verdict-rsop-observe-20260906045428-3847.json",
    "wp6-evidence/verdict-rsop-observe-20260906045536-1696.json",
    "wp6-evidence/verdict-rsop-observe-20260906045643-6646.json",
    "wp6-evidence/verdict-rsop-observe-20260906045750-7576.json",
    "wp6-evidence/verdict-rsop-observe-20260906045858-7209.json",
    "wp6-evidence/verdict-rsop-observe-20260906183835-6175.json",
    "wp6-evidence/verdict-rsop-observe-20260906183948-3890.json",
    "wp6-evidence/verdict-rsop-observe-20260906184057-2689.json",
    "wp6-evidence/verdict-rsop-observe-20260906184205-5172.json",
    "wp6-evidence/verdict-rsop-observe-20260906184313-1876.json",
    "wp6-evidence/verdict-rsop-observe-20260906184434-8187.json",
    "wp6-evidence/verdict-rsop-observe-20260906221248-7683.json",
    "wp6-evidence/verdict-rsop-observe-20260906221638-4687.json",
    "wp6-evidence/verdict-rsop-observe-20260906221931-1695.json",
    "wp6-evidence/verdict-rsop-observe-20260906223143-4837.json",
    "wp6-evidence/verdict-rsop-observe-20260906223251-8863.json",
    "wp6-evidence/verdict-rsop-observe-20260906223400-9371.json",
    "wp6-evidence/verdict-rsop-observe-20260906223508-6654.json",
    "wp6-evidence/verdict-rsop-observe-20260906223619-5576.json",
    "wp9-evidence/verdict-rsop-user-observe-20260906051241-1230.json",
    "wp9-evidence/verdict-rsop-user-observe-20260906051412-9765.json",
    "wp9-evidence/verdict-rsop-user-observe-20260906051544-3625.json",
    "wp9-evidence/verdict-rsop-user-observe-20260906051750-5647.json",
    "wp9-evidence/verdict-rsop-user-observe-20260906052004-2373.json",
    "wp9-evidence/verdict-rsop-user-observe-20260906052146-2480.json",
    "wp9-evidence/verdict-rsop-user-observe-20260906184610-3620.json",
    "wp9-evidence/verdict-rsop-user-observe-20260906184743-5732.json",
    "wp9-evidence/verdict-rsop-user-observe-20260906184916-2617.json",
    "wp9-evidence/verdict-rsop-user-observe-20260906185125-9433.json",
    "wp9-evidence/verdict-rsop-user-observe-20260906185345-9222.json",
    "wp9-evidence/verdict-rsop-user-observe-20260906185527-8016.json",
    "wp9-evidence/verdict-rsop-user-observe-20260906222041-8299.json",
    "wp9-evidence/verdict-rsop-user-observe-20260906222219-6252.json",
    "wp9-evidence/verdict-rsop-user-observe-20260906222352-5950.json",
    "wp9-evidence/verdict-rsop-user-observe-20260906222601-2732.json",
    "wp9-evidence/verdict-rsop-user-observe-20260906222818-6584.json",
    "wp9-evidence/verdict-rsop-user-observe-20260906222959-7716.json",
    # The 2026-09-05 batch, superseded by batch two on 2026-09-06. WI-037 moved
    # all three shared-root lane drivers and WI-049 moved the candidate builder,
    # both of which every RSOP verdict binds by hash. The gate reported exactly
    # twelve broken bindings the moment the code landed and before a single lane
    # had been re-run -- the second consecutive tranche where it did the noticing
    # rather than a person.
    # The 2026-08-06 RSOP batch, superseded by the 2026-09-05 re-certification.
    # WI-048 changed `psdirect.ps1`, which every lane transports through, and the
    # WP-9 lane's session-restart gate stopped failing silently in the same
    # tranche. Kept rather than deleted: `...184006-2532` is an anchor target in
    # the remediation corpus, and the batch records what these lanes measured
    # under the previous harness.
    #
    # Worth stating because it is this gate's whole point. The note below records
    # that the RSOP lanes have twice been fully re-run for exactly this reason,
    # and that BOTH times the staleness was caught by a person noticing rather
    # than by a test. This is the third occasion, and it was caught by
    # `test_a_live_verdict_still_binds_the_harness_that_ships` reporting fifteen
    # broken bindings before a single lane had been re-run.
    "wp6-evidence/verdict-rsop-observe-20260806181033-3296.json",
    "wp6-evidence/verdict-rsop-observe-20260806181222-5315.json",
    "wp6-evidence/verdict-rsop-observe-20260806181411-9752.json",
    "wp6-evidence/verdict-rsop-observe-20260806181600-5707.json",
    "wp6-evidence/verdict-rsop-observe-20260806181748-7763.json",
    "wp6-evidence/verdict-rsop-observe-20260806181935-5130.json",
    "wp9-evidence/verdict-rsop-user-observe-20260806182125-6983.json",
    "wp9-evidence/verdict-rsop-user-observe-20260806182338-3982.json",
    "wp9-evidence/verdict-rsop-user-observe-20260806182554-1472.json",
    "wp9-evidence/verdict-rsop-user-observe-20260806182911-5363.json",
    "wp9-evidence/verdict-rsop-user-observe-20260806183612-5557.json",
    "wp9-evidence/verdict-rsop-user-observe-20260806184006-2532.json",
    # The 2026-08-05 batch and WI-043's inconclusive measurement, superseded by
    # the 2026-08-06 re-certification. The measurement is kept rather than
    # dropped: it is the only record of the model ABSTAINING on a region it now
    # answers, and the arc from abstention to measured rule is the evidence that
    # the rule was measured rather than assumed.
    "wp6-evidence/verdict-rsop-observe-20260805220819-4762.json",
    "wp6-evidence/verdict-rsop-observe-20260805221004-8571.json",
    "wp6-evidence/verdict-rsop-observe-20260805221150-4243.json",
    "wp6-evidence/verdict-rsop-observe-20260805221335-1702.json",
    "wp6-evidence/verdict-rsop-observe-20260805221522-1983.json",
    "wp6-evidence/verdict-rsop-observe-20260805221707-4871.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805221856-6415.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805222106-2378.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805222317-3382.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805222624-9750.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805222929-6350.json",
    "wp9-evidence/verdict-rsop-user-observe-20260806165543-8004.json",
    # WP-6B's first certification and the WI-031 enforcement arc, superseded
    # when `run-rsop-author.ps1` and the candidate builder moved on.
    "wp6-evidence/verdict-rsop-observe-20260804010341-7165.json",
    "wp6-evidence/verdict-rsop-observe-20260804010551-9363.json",
    "wp6-evidence/verdict-rsop-observe-20260804010738-5543.json",
    "wp6-evidence/verdict-rsop-observe-20260804012618-5426.json",
    "wp6-evidence/verdict-rsop-observe-20260804012803-7606.json",
    "wp6-evidence/verdict-rsop-observe-20260804015016-5317.json",
    "wp6-evidence/verdict-rsop-observe-20260804015258-1810.json",
    "wp6-evidence/verdict-rsop-observe-20260804015447-4913.json",
    "wp6-evidence/verdict-rsop-observe-20260804020109-7624.json",
    "wp6-evidence/verdict-rsop-observe-20260804020308-9752.json",
    "wp6-evidence/verdict-rsop-observe-20260804020517-2089.json",
    "wp6-evidence/verdict-rsop-observe-20260804051032-8845.json",
    "wp6-evidence/verdict-rsop-observe-20260804051228-2926.json",
    "wp6-evidence/verdict-rsop-observe-20260804070708-6831.json",
    "wp6-evidence/verdict-rsop-observe-20260804151624-6393.json",
    "wp6-evidence/verdict-rsop-observe-20260804152957-1430.json",
    # WI-039, the one undeclared finding this lane has produced, and its fix.
    "wp6-evidence/verdict-rsop-observe-20260804153726-7284.json",
    "wp6-evidence/verdict-rsop-observe-20260804154241-9337.json",
    # WI-040's arc: the `expected-finding` that measured the read-deny gap and
    # the `pass` that certified the fix. Kept for the divergence they observed
    # on a real client, which does not depend on the harness binding.
    "wp6-evidence/verdict-rsop-observe-20260805045139-3731.json",
    "wp6-evidence/verdict-rsop-observe-20260805045851-3883.json",
    # Re-certification generation 1, superseded by the WI-043 contract change.
    "wp6-evidence/verdict-rsop-observe-20260805064008-9181.json",
    "wp6-evidence/verdict-rsop-observe-20260805064155-8996.json",
    "wp6-evidence/verdict-rsop-observe-20260805064351-9402.json",
    "wp6-evidence/verdict-rsop-observe-20260805064540-1562.json",
    "wp6-evidence/verdict-rsop-observe-20260805064725-4970.json",
    # Re-certification generation 2 (`a85736a`), superseded hours later by
    # review round 3's change to `build-rsop-candidate.py`. The prediction
    # output was byte-identical across that change -- which is worth knowing and
    # is NOT the standard. A certification binds the harness, not the output.
    "wp6-evidence/verdict-rsop-observe-20260805194053-7180.json",
    "wp6-evidence/verdict-rsop-observe-20260805194245-3734.json",
    "wp6-evidence/verdict-rsop-observe-20260805194432-5944.json",
    "wp6-evidence/verdict-rsop-observe-20260805194627-2633.json",
    "wp6-evidence/verdict-rsop-observe-20260805194814-2731.json",
    "wp6-evidence/verdict-rsop-observe-20260805195001-1590.json",
    # WP-9's own arc, same three generations.
    "wp9-evidence/verdict-rsop-user-observe-20260804045552-9148.json",
    "wp9-evidence/verdict-rsop-user-observe-20260804045809-8312.json",
    "wp9-evidence/verdict-rsop-user-observe-20260804050024-4383.json",
    "wp9-evidence/verdict-rsop-user-observe-20260804065146-4224.json",
    # WI-033: the deny a real client demonstrated the model could not express.
    "wp9-evidence/verdict-rsop-user-observe-20260804065525-9254.json",
    "wp9-evidence/verdict-rsop-user-observe-20260804150527-3868.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805065203-1562.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805065415-8622.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805065630-6815.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805065943-6615.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805070255-2473.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805195149-5629.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805195400-1809.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805195614-1767.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805195909-4033.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805200214-4370.json",
    # The 2026-09-06 batch-two verdicts, superseded the same day by WI-054.
    # The machine-token group-deny change moved the candidate builder (the dead
    # `reaches_reasoned_cell` predicate left it), the authoring half (which
    # account joins the disposable group), the computer observation half (token
    # corroboration), the computer finalizer (its token gate) and both lane
    # drivers (the reboot), and every verdict bound to the pre-change hashes
    # stopped being a certification the moment the code landed. Re-run in full
    # the same evening; see the WI-054 batch entries in LANE_VERDICTS.
    # The group-deny scenario's first run: a LANE-FAILURE by design, binding
    # the tree BEFORE the boot-applier fix. It is the record of the run that
    # found the interaction (boot-time policy processing filling the policy key
    # between authoring and observation), and it stays stale by construction --
    # the fix changed the observe half and the finalizer it binds.
    "wp3-evidence/verification-estate.json",
}

#: JSON files living in a `wp*-evidence/` directory whose names match neither
#: verdict prefix. WI-053: `endpoint-result-phase4-estate.json` spent five weeks
#: as a lane's only committed certification while matching neither prefix the
#: coverage guard globs for, and mapping the new endpoint verdict under a
#: covered name fixed that instance without fixing the hole -- the derivation
#: was still only as wide as its pattern. So the universe is now every JSON in
#: an evidence directory: a file is either verdict-named (and therefore mapped,
#: retired, or deliberately pre-transport) or named HERE, with its reason.
#: A verdict can no longer escape by being named unusually -- it would land in
#: the unaccounted bucket and the widened test below would fail. Adding to this
#: dict is a deliberate act with a reason attached, and
#: `test_the_widened_guard_still_sees_the_endpoint_verdict` fails if the
#: verdict pattern is ever narrowed back to something the endpoint
#: certification falls out of.
NON_VERDICT_EVIDENCE_FILES: dict[str, str] = {
    # WP-0's oracle manifest: a record of fixture comparisons (the lane has no
    # pass/fail verdict shape). Read by `_certified_runs` under its manifest
    # prefix, which is why it is named rather than verdict-named.
    "wp0-evidence/manifest-estate.json": (
        "WP-0's oracle manifest -- fixture-comparison record, not a verdict"
    ),
    # The endpoint lane's pre-estate phases (mvmdc03 / MVMCITEST01), kept as
    # raw run records. None was ever a certification; the lane's first
    # estate-era certification was phase 4's, below.
    "wp1b-evidence/endpoint-result.json": (
        "raw phase-1 endpoint run record (pre-estate transport)"
    ),
    "wp1b-evidence/endpoint-result-phase2.json": (
        "raw phase-2 endpoint run record (pre-estate transport)"
    ),
    "wp1b-evidence/endpoint-result-phase3.json": (
        "raw phase-3 endpoint run record (pre-estate transport)"
    ),
    # THE file WI-053 is about: the endpoint lane's only certification from
    # 2026-08-03 until 2026-09-06, invisible to every gate because its name
    # matched neither prefix. Superseded by
    # `wp6-evidence/verdict-endpoint-observe-20260906185837-7523.json` (the
    # WI-025 re-certification, the lane's first hash-bound candidate verdict).
    # Kept as the record of the 2026-08-03 run, now named so its name can
    # never again be the reason nothing checked it.
    "wp1b-evidence/endpoint-result-phase4-estate.json": (
        "superseded phase-4 certification -- replaced by the mapped "
        "verdict-endpoint-observe-20260906185837-7523"
    ),
    # The observation half of the same 2026-08-03 run: the guest-side gpupdate
    # observation that backs the verdict above. Raw evidence, not a claim.
    "wp1b-evidence/endpoint-observe-phase4-estate.json": (
        "phase-4 guest observation half, backing the superseded certification"
    ),
    # A trimmed RSOP XML document from the 2026-08-04 site/OU runs, kept as
    # raw observation material for the scope-of-management findings. Not a
    # pass/fail record.
    "wp6-evidence/rsop-document-excerpt.json": (
        "trimmed RSOP XML document excerpt, raw observation material"
    ),
}

# WI-059: replacement runs on one frozen harness; the old records remain immutable.
LANE_VERDICTS.update({
    'wp1b-evidence/wi059-20260908/wp1b/verification.json': 'finalize_wp1b_run.py',
    'wp2-evidence/wi059-20260908/wp2/verification.json': 'finalize_wp2_import_run.py',
    'wp3-evidence/wi059-20260908/wp3-member/verification.json': 'finalize_wp3_run.py',
    'wp3-evidence/wi059-20260908/wp3-dc/verification.json': 'finalize_wp3_run.py',
    'wp3-evidence/wi059-20260908/object-security/verification.json': (
        'finalize_object_security_run.py'
    ),
    'wp1b-evidence/wi059-20260908/scripts-metadata/verification.json': (
        'finalize_scripts_backup_run.py'
    ),
    'wp1b-evidence/wi059-20260908/publication/verification.json': 'finalize_publication_run.py',
    'wp6-evidence/wi059-20260908/endpoint/verification.json': 'finalize_endpoint_run.py',
    'wp6-evidence/wi059-20260908/lsdou-precedence/verification.json': 'finalize_rsop_run.py',
    'wp6-evidence/wi059-20260908/disabled-block-enforced/verification.json': 'finalize_rsop_run.py',
    'wp6-evidence/wi059-20260908/wmi-filtering/verification.json': 'finalize_rsop_run.py',
    'wp6-evidence/wi059-20260908/wmi-filtering-error/verification.json': 'finalize_rsop_run.py',
    'wp6-evidence/wi059-20260908/computer-security-filtering/verification.json': (
        'finalize_rsop_run.py'
    ),
    'wp6-evidence/wi059-20260908/computer-security-filtering-group-deny/verification.json': (
        'finalize_rsop_run.py'
    ),
    'wp6-evidence/wi059-20260908/computer-security-filtering-deny-read/verification.json': (
        'finalize_rsop_run.py'
    ),
    'wp9-evidence/wi059-20260908/loopback-merge/verification.json': 'finalize_rsop_user_run.py',
    'wp9-evidence/wi059-20260908/loopback-replace/verification.json': 'finalize_rsop_user_run.py',
    'wp9-evidence/wi059-20260908/user-side-disabled/verification.json': 'finalize_rsop_user_run.py',
    'wp9-evidence/wi059-20260908/user-security-filtering/verification.json': (
        'finalize_rsop_user_run.py'
    ),
    'wp9-evidence/wi059-20260908/user-security-filtering-deny/verification.json': (
        'finalize_rsop_user_run.py'
    ),
    'wp9-evidence/wi059-20260908/user-security-filtering-read-deny/verification.json': (
        'finalize_rsop_user_run.py'
    ),
})
RETIRED_VERDICTS.update({
    'wp1b-evidence/verification-estate.json',
    'wp1b-evidence/scripts-metadata/verification.json',
    'wp1b-evidence/publication-completeness/verification.json',
    'wp6-evidence/verdict-rsop-observe-20260907221946-2994.json',
    'wp6-evidence/verdict-rsop-observe-20260907222055-1770.json',
    'wp6-evidence/verdict-rsop-observe-20260907222206-6220.json',
    'wp6-evidence/verdict-rsop-observe-20260907222315-2698.json',
    'wp6-evidence/verdict-rsop-observe-20260907222431-1389.json',
    'wp6-evidence/verdict-rsop-observe-20260907222610-4330.json',
    'wp6-evidence/verdict-rsop-observe-20260907222726-4256.json',
    'wp9-evidence/verdict-rsop-user-observe-20260907220844-4855.json',
    'wp9-evidence/verdict-rsop-user-observe-20260907221020-2710.json',
    'wp9-evidence/verdict-rsop-user-observe-20260907221153-8426.json',
    'wp9-evidence/verdict-rsop-user-observe-20260907221411-3148.json',
    'wp9-evidence/verdict-rsop-user-observe-20260907221626-5555.json',
    'wp9-evidence/verdict-rsop-user-observe-20260907221808-9398.json',
    'wp2-evidence/verification-estate.json',
    'wp3-evidence/policy-families/dc/verification.json',
    'wp3-evidence/policy-families/member/verification.json',
    'wp3-evidence/object-security/verification.json',
    'wp6-evidence/verdict-endpoint-observe-20260906185837-7523.json',
})

#: The verdicts that are still CLAIMS: everything mapped and not retired.
LIVE_VERDICTS = {
    relative: finalizer
    for relative, finalizer in LANE_VERDICTS.items()
    if relative not in RETIRED_VERDICTS
}


# WI-059: immutable source-file schemas of records banked before enforcement.
HISTORICAL_BOUND_FILES.update(dict.fromkeys(
    (
        'wp1b-evidence/verification-estate.json',
    ),
    {
        'build-wp1b-candidates.py',
        'psdirect.ps1',
        'run-wp1b-oracle.sh',
        'run-wp1b-writer.ps1',
    },
))
HISTORICAL_BOUND_FILES.update(dict.fromkeys(
    (
        'wp1b-evidence/scripts-metadata/verification.json',
    ),
    {
        'build-scripts-backup-candidate.py',
        'canonical.py',
        'export.py',
        'finalize_scripts_backup_run.py',
        'gpp.py',
        'model.py',
        'oracle_evidence.py',
        'psdirect.ps1',
        'registry_pol.py',
        'run-scripts-backup-import.ps1',
        'run-scripts-backup-oracle.sh',
        'script_policy.py',
        'validation.py',
        'xml_safety.py',
    },
))
HISTORICAL_BOUND_FILES.update(dict.fromkeys(
    (
        'wp1b-evidence/publication-completeness/verification.json',
    ),
    {
        'build-publication-candidate.py',
        'canonical.py',
        'export.py',
        'finalize_publication_run.py',
        'gpp.py',
        'model.py',
        'oracle_evidence.py',
        'psdirect.ps1',
        'publication.py',
        'registry_pol.py',
        'run-publication-import.ps1',
        'run-publication-oracle.sh',
        'validation.py',
        'xml_safety.py',
    },
))
HISTORICAL_BOUND_FILES.update(dict.fromkeys(
    (
        'wp6-evidence/verdict-rsop-observe-20260907221946-2994.json',
        'wp6-evidence/verdict-rsop-observe-20260907222055-1770.json',
        'wp6-evidence/verdict-rsop-observe-20260907222206-6220.json',
        'wp6-evidence/verdict-rsop-observe-20260907222315-2698.json',
        'wp6-evidence/verdict-rsop-observe-20260907222431-1389.json',
        'wp6-evidence/verdict-rsop-observe-20260907222610-4330.json',
        'wp6-evidence/verdict-rsop-observe-20260907222726-4256.json',
        'wp6-evidence/verdict-rsop-observe-20260804020517-2089.json',
        'wp6-evidence/verdict-rsop-observe-20260804051032-8845.json',
        'wp6-evidence/verdict-rsop-observe-20260804051228-2926.json',
        'wp6-evidence/verdict-rsop-observe-20260804070708-6831.json',
        'wp6-evidence/verdict-rsop-observe-20260804151624-6393.json',
        'wp6-evidence/verdict-rsop-observe-20260804152957-1430.json',
        'wp6-evidence/verdict-rsop-observe-20260804154241-9337.json',
        'wp6-evidence/verdict-rsop-observe-20260804010341-7165.json',
        'wp6-evidence/verdict-rsop-observe-20260804010551-9363.json',
        'wp6-evidence/verdict-rsop-observe-20260804010738-5543.json',
        'wp6-evidence/verdict-rsop-observe-20260804012618-5426.json',
        'wp6-evidence/verdict-rsop-observe-20260804012803-7606.json',
        'wp6-evidence/verdict-rsop-observe-20260804015016-5317.json',
        'wp6-evidence/verdict-rsop-observe-20260804015258-1810.json',
        'wp6-evidence/verdict-rsop-observe-20260804015447-4913.json',
        'wp6-evidence/verdict-rsop-observe-20260804020109-7624.json',
        'wp6-evidence/verdict-rsop-observe-20260804020308-9752.json',
        'wp6-evidence/verdict-rsop-observe-20260804153726-7284.json',
        'wp6-evidence/verdict-rsop-observe-20260805064008-9181.json',
        'wp6-evidence/verdict-rsop-observe-20260805064155-8996.json',
        'wp6-evidence/verdict-rsop-observe-20260805064351-9402.json',
        'wp6-evidence/verdict-rsop-observe-20260805064540-1562.json',
        'wp6-evidence/verdict-rsop-observe-20260805064725-4970.json',
        'wp6-evidence/verdict-rsop-observe-20260805045139-3731.json',
        'wp6-evidence/verdict-rsop-observe-20260805045851-3883.json',
        'wp6-evidence/verdict-rsop-observe-20260805194053-7180.json',
        'wp6-evidence/verdict-rsop-observe-20260805194245-3734.json',
        'wp6-evidence/verdict-rsop-observe-20260805194432-5944.json',
        'wp6-evidence/verdict-rsop-observe-20260805194627-2633.json',
        'wp6-evidence/verdict-rsop-observe-20260805194814-2731.json',
        'wp6-evidence/verdict-rsop-observe-20260805195001-1590.json',
        'wp6-evidence/verdict-rsop-observe-20260805220819-4762.json',
        'wp6-evidence/verdict-rsop-observe-20260805221004-8571.json',
        'wp6-evidence/verdict-rsop-observe-20260805221150-4243.json',
        'wp6-evidence/verdict-rsop-observe-20260805221335-1702.json',
        'wp6-evidence/verdict-rsop-observe-20260805221522-1983.json',
        'wp6-evidence/verdict-rsop-observe-20260805221707-4871.json',
        'wp6-evidence/verdict-rsop-observe-20260806181033-3296.json',
        'wp6-evidence/verdict-rsop-observe-20260906045316-1301.json',
        'wp6-evidence/verdict-rsop-observe-20260806181222-5315.json',
        'wp6-evidence/verdict-rsop-observe-20260906045428-3847.json',
        'wp6-evidence/verdict-rsop-observe-20260806181411-9752.json',
        'wp6-evidence/verdict-rsop-observe-20260906045536-1696.json',
        'wp6-evidence/verdict-rsop-observe-20260806181600-5707.json',
        'wp6-evidence/verdict-rsop-observe-20260906045643-6646.json',
        'wp6-evidence/verdict-rsop-observe-20260806181748-7763.json',
        'wp6-evidence/verdict-rsop-observe-20260906045750-7576.json',
        'wp6-evidence/verdict-rsop-observe-20260806181935-5130.json',
        'wp6-evidence/verdict-rsop-observe-20260906045858-7209.json',
        'wp6-evidence/verdict-rsop-observe-20260906183835-6175.json',
        'wp6-evidence/verdict-rsop-observe-20260906183948-3890.json',
        'wp6-evidence/verdict-rsop-observe-20260906184057-2689.json',
        'wp6-evidence/verdict-rsop-observe-20260906184205-5172.json',
        'wp6-evidence/verdict-rsop-observe-20260906184313-1876.json',
        'wp6-evidence/verdict-rsop-observe-20260906184434-8187.json',
        'wp6-evidence/verdict-rsop-observe-20260906221638-4687.json',
        'wp6-evidence/verdict-rsop-observe-20260906221931-1695.json',
        'wp6-evidence/verdict-rsop-observe-20260906223143-4837.json',
        'wp6-evidence/verdict-rsop-observe-20260906223251-8863.json',
        'wp6-evidence/verdict-rsop-observe-20260906223400-9371.json',
        'wp6-evidence/verdict-rsop-observe-20260906223508-6654.json',
        'wp6-evidence/verdict-rsop-observe-20260906223619-5576.json',
        'wp6-evidence/verdict-rsop-observe-20260906221248-7683.json',
    ),
    {
        'build-rsop-candidate.py',
        'psdirect.ps1',
        'run-rsop-author.ps1',
        'run-rsop-observe.ps1',
        'run-rsop-oracle.sh',
    },
))
HISTORICAL_BOUND_FILES.update(dict.fromkeys(
    (
        'wp9-evidence/verdict-rsop-user-observe-20260907220844-4855.json',
        'wp9-evidence/verdict-rsop-user-observe-20260907221020-2710.json',
        'wp9-evidence/verdict-rsop-user-observe-20260907221153-8426.json',
        'wp9-evidence/verdict-rsop-user-observe-20260907221411-3148.json',
        'wp9-evidence/verdict-rsop-user-observe-20260907221626-5555.json',
        'wp9-evidence/verdict-rsop-user-observe-20260907221808-9398.json',
        'wp9-evidence/verdict-rsop-user-observe-20260804050024-4383.json',
        'wp9-evidence/verdict-rsop-user-observe-20260804045552-9148.json',
        'wp9-evidence/verdict-rsop-user-observe-20260804045809-8312.json',
        'wp9-evidence/verdict-rsop-user-observe-20260804065146-4224.json',
        'wp9-evidence/verdict-rsop-user-observe-20260804065525-9254.json',
        'wp9-evidence/verdict-rsop-user-observe-20260804150527-3868.json',
        'wp9-evidence/verdict-rsop-user-observe-20260805065203-1562.json',
        'wp9-evidence/verdict-rsop-user-observe-20260805065415-8622.json',
        'wp9-evidence/verdict-rsop-user-observe-20260805065630-6815.json',
        'wp9-evidence/verdict-rsop-user-observe-20260805065943-6615.json',
        'wp9-evidence/verdict-rsop-user-observe-20260805070255-2473.json',
        'wp9-evidence/verdict-rsop-user-observe-20260805195149-5629.json',
        'wp9-evidence/verdict-rsop-user-observe-20260805195400-1809.json',
        'wp9-evidence/verdict-rsop-user-observe-20260805195614-1767.json',
        'wp9-evidence/verdict-rsop-user-observe-20260805195909-4033.json',
        'wp9-evidence/verdict-rsop-user-observe-20260805200214-4370.json',
        'wp9-evidence/verdict-rsop-user-observe-20260805221856-6415.json',
        'wp9-evidence/verdict-rsop-user-observe-20260805222106-2378.json',
        'wp9-evidence/verdict-rsop-user-observe-20260805222317-3382.json',
        'wp9-evidence/verdict-rsop-user-observe-20260805222624-9750.json',
        'wp9-evidence/verdict-rsop-user-observe-20260805222929-6350.json',
        'wp9-evidence/verdict-rsop-user-observe-20260806165543-8004.json',
        'wp9-evidence/verdict-rsop-user-observe-20260806182125-6983.json',
        'wp9-evidence/verdict-rsop-user-observe-20260906051241-1230.json',
        'wp9-evidence/verdict-rsop-user-observe-20260806182338-3982.json',
        'wp9-evidence/verdict-rsop-user-observe-20260906051412-9765.json',
        'wp9-evidence/verdict-rsop-user-observe-20260806182554-1472.json',
        'wp9-evidence/verdict-rsop-user-observe-20260906051544-3625.json',
        'wp9-evidence/verdict-rsop-user-observe-20260806182911-5363.json',
        'wp9-evidence/verdict-rsop-user-observe-20260906051750-5647.json',
        'wp9-evidence/verdict-rsop-user-observe-20260806183612-5557.json',
        'wp9-evidence/verdict-rsop-user-observe-20260906052004-2373.json',
        'wp9-evidence/verdict-rsop-user-observe-20260806184006-2532.json',
        'wp9-evidence/verdict-rsop-user-observe-20260906052146-2480.json',
        'wp9-evidence/verdict-rsop-user-observe-20260906184610-3620.json',
        'wp9-evidence/verdict-rsop-user-observe-20260906184743-5732.json',
        'wp9-evidence/verdict-rsop-user-observe-20260906184916-2617.json',
        'wp9-evidence/verdict-rsop-user-observe-20260906185125-9433.json',
        'wp9-evidence/verdict-rsop-user-observe-20260906185345-9222.json',
        'wp9-evidence/verdict-rsop-user-observe-20260906185527-8016.json',
        'wp9-evidence/verdict-rsop-user-observe-20260906222041-8299.json',
        'wp9-evidence/verdict-rsop-user-observe-20260906222219-6252.json',
        'wp9-evidence/verdict-rsop-user-observe-20260906222352-5950.json',
        'wp9-evidence/verdict-rsop-user-observe-20260906222601-2732.json',
        'wp9-evidence/verdict-rsop-user-observe-20260906222818-6584.json',
        'wp9-evidence/verdict-rsop-user-observe-20260906222959-7716.json',
    ),
    {
        'build-rsop-candidate.py',
        'psdirect.ps1',
        'run-rsop-author.ps1',
        'run-rsop-user-observe.ps1',
        'run-rsop-user-oracle.sh',
    },
))
HISTORICAL_BOUND_FILES.update(dict.fromkeys(
    (
        'wp2-evidence/verification-estate.json',
    ),
    {
        'build-wp2-candidate.py',
        'psdirect.ps1',
        'run-wp2-import.ps1',
        'run-wp2-oracle.sh',
    },
))
HISTORICAL_BOUND_FILES.update(dict.fromkeys(
    (
        'wp3-evidence/policy-families/dc/verification.json',
        'wp3-evidence/policy-families/member/verification.json',
    ),
    {
        'build-wp3-candidate.py',
        'finalize_wp3_run.py',
        'policy_families.py',
        'psdirect.ps1',
        'run-wp3-oracle.sh',
        'run-wp3-security-template.ps1',
        'security_template.py',
    },
))
HISTORICAL_BOUND_FILES.update(dict.fromkeys(
    (
        'wp3-evidence/object-security/verification.json',
    ),
    {
        'build-object-security-candidate.py',
        'finalize_object_security_run.py',
        'object_security.py',
        'oracle_evidence.py',
        'psdirect.ps1',
        'run-object-security-oracle.sh',
        'run-object-security-template.ps1',
        'sddl.py',
        'security_template.py',
    },
))
HISTORICAL_BOUND_FILES.update(dict.fromkeys(
    (
        'wp6-evidence/verdict-endpoint-observe-20260906185837-7523.json',
    ),
    {
        'build-endpoint-candidate.py',
        'psdirect.ps1',
        'run-endpoint-author.ps1',
        'run-endpoint-observe.ps1',
        'run-endpoint-oracle.sh',
    },
))


def _verdict(relative: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((EVIDENCE / relative).read_text(encoding="utf-8")))


def _file_tables(finalizer: str) -> list[dict[str, str]]:
    """The name -> repository path tables this finalizer binds, in either shape.

    Lanes that once supported two transports key their tables by transport;
    the RSOP lanes were written after the SSH path was deleted and key them
    directly. Both are read here so the checks below cover every lane rather
    than the ones that happen to share a shape.
    """
    symbols = runpy.run_path(str(ORACLE_DIR / finalizer))
    tables: list[dict[str, str]] = []
    for transport_keyed, flat in (
        ("TRANSPORT_DEPLOYED_FILES", "DEPLOYED_FILES"),
        ("TRANSPORT_LOCAL_FILES", "LOCAL_FILES"),
    ):
        if transport_keyed in symbols:
            tables.append(cast(dict[str, dict[str, str]], symbols[transport_keyed])["psdirect"])
        else:
            tables.append(cast(dict[str, str], symbols[flat]))
    return tables


def _bound_names(finalizer: str) -> set[str]:
    return {name for table in _file_tables(finalizer) for name in table}


@pytest.mark.parametrize("relative,finalizer", sorted(LANE_VERDICTS.items()))
def test_source_files_holds_exactly_the_bound_repository_files(
    relative: str, finalizer: str
) -> None:
    """Every `source.files` key names a file this lane binds, and nothing else.

    The block sits under `source: {commit, dirty, files}`, so each key has to be
    resolvable against that commit. A generated artifact filed there is not a
    small untidiness: it makes the verdict unverifiable by the obvious method.
    """
    verdict = _verdict(relative)
    expected = HISTORICAL_BOUND_FILES.get(relative, _bound_names(finalizer))
    assert set(verdict["source"]["files"]) == expected


@pytest.mark.parametrize("relative,finalizer", sorted(LANE_VERDICTS.items()))
def test_every_bound_file_still_exists_in_the_tree(relative: str, finalizer: str) -> None:
    """A verdict naming a file the repository no longer has cannot be re-checked."""
    for table in _file_tables(finalizer):
        for name, source in table.items():
            assert (REPO_ROOT / source).is_file(), f"{name} -> {source}"


@pytest.mark.parametrize("relative,finalizer", sorted(LANE_VERDICTS.items()))
def test_a_verdict_is_internally_consistent(relative: str, finalizer: str) -> None:
    """`passed` must follow from the state and the checks recorded beside it.

    Committed evidence is no longer all passes. A scenario may DECLARE that it
    expects to diverge -- the deny row and the WMI row exist to demonstrate
    capabilities the model does not have -- and its verdict is committed as
    evidence of the gap. Such a verdict must carry `expected-finding`, must not
    claim `passed`, and must actually contain the divergence: a declared
    divergence with an empty finding list would be a claim with nothing behind
    it.

    Everything else must be a pass whose checks agree with it. A verdict that
    said `passed` while carrying a false check would mean the file was edited
    after the fact, or assembled from more than one run.
    """
    verdict = _verdict(relative)
    if verdict.get("state") == "expected-finding":
        assert verdict["passed"] is False
        assert verdict["expected_finding"], relative
        comparison = verdict["comparison"]
        assert comparison is not None, relative
        divergences = (
            comparison["value_findings"]
            + comparison["applied_only_predicted"]
            + comparison["applied_only_observed"]
        )
        assert divergences, f"{relative} declares a divergence and records none"
        assert verdict["source"]["dirty"] is False
        assert verdict["transport"] == "psdirect"
        return

    if verdict.get("state") == "finding":
        # An UNDECLARED divergence: the lane predicted one thing, Windows did
        # another, and nobody saw it coming. WI-039 is the example and it is the
        # most valuable evidence this lane has produced -- reading the code
        # could not have found it.
        #
        # There was no branch for this state, so the assertions below demanded
        # `passed` and any attempt to commit such a verdict into the map failed.
        # The effect was that the one class of finding worth keeping was the one
        # class that could not be recorded here.
        assert verdict["passed"] is False, relative
        comparison = verdict["comparison"]
        assert comparison is not None, relative
        divergences = (
            comparison["value_findings"]
            + comparison["applied_only_predicted"]
            + comparison["applied_only_observed"]
        )
        assert divergences, f"{relative} is a finding and records no divergence"
        assert verdict["source"]["dirty"] is False
        assert verdict["transport"] == "psdirect"
        return

    if verdict.get("state") == "inconclusive":
        # THE EXPERIMENT RAN AND SAYS NOTHING ABOUT THE MODEL, which is a real
        # outcome and not a failed pass. WI-043's measurement is the case: the
        # model abstained on the two rows the scenario was built to ask about,
        # so the finalizer refused to grade them and the run cannot be a pass no
        # matter what Windows did. The observation is still the point.
        #
        # THIS IS THE SECOND TIME THIS FILE HAS HAD THIS GAP. The `finding`
        # branch above carries a comment about being added because its absence
        # made the most valuable class of evidence the one class that could not
        # be recorded here. `inconclusive` was left in exactly that position and
        # was found the same way -- by a lane producing one and the test
        # demanding `passed`. A fall-through that asserts the happy path is how
        # a state machine quietly excludes its own outcomes.
        assert verdict["passed"] is False, relative
        comparison = verdict["comparison"]
        # An inconclusive run has to say WHY it could not conclude, and there
        # are exactly two legitimate reasons: a control did not hold, or the
        # model declined to predict something. A verdict claiming neither is
        # claiming to be inconclusive about nothing.
        abstained = bool(comparison and comparison.get("unevaluable_gpos"))
        controls_failed = bool(verdict.get("control_problems"))
        assert abstained or controls_failed, (
            f"{relative} is inconclusive but records neither a failed control nor a "
            "model abstention, so nothing explains why it could not conclude"
        )
        if abstained:
            assert comparison is not None
            assert comparison["conclusive"] is False, (
                f"{relative} records an abstention while claiming to be conclusive"
            )
        assert verdict["source"]["dirty"] is False
        assert verdict["transport"] == "psdirect"
        return

    if verdict.get("state") == "lane-failure":
        # THE HARNESS FAILED, not the model -- and the third time this file
        # grew a branch, it is worth saying what keeps the pattern from
        # recurring: every outcome the finalizer can emit needs a branch here,
        # because a fall-through that asserts the happy path makes each newly
        # discovered outcome the one class of evidence that cannot be
        # committed. `lane-failure` arrived the same way `finding` and
        # `inconclusive` did -- a run produced it and this test demanded
        # `passed`. WI-054's first group-deny run is the case: the client
        # rebooted with the run's policy already linked, the residual guard
        # refused the attribution, and the record is kept as the reason the
        # observe half gained its boot-applied gate.
        assert verdict["passed"] is False, relative
        # A lane failure with no stated reason claims nothing and proves
        # nothing; the problems list is the whole content of the record.
        assert verdict["lane_problems"], (
            f"{relative} is a lane failure and records no lane problem"
        )
        # The comparison is suppressed on a lane failure -- grading the model
        # on an unattributable observation is the defect the state exists to
        # prevent.
        assert verdict["comparison"] is None, relative
        assert verdict["source"]["dirty"] is False
        assert verdict["transport"] == "psdirect"
        return

    assert verdict["passed"] is True
    if "checks" in verdict:
        failed = sorted(name for name, ok in verdict["checks"].items() if not ok)
        assert not failed, f"{relative} claims passed with failing checks: {failed}"
    for candidate in verdict.get("candidates", []):
        failed = sorted(name for name, ok in candidate["checks"].items() if not ok)
        assert not failed, f"{relative}:{candidate['candidate_id']} {failed}"
        assert candidate["state"] == "pass"
    assert verdict["source"]["dirty"] is False
    assert verdict["transport"] == "psdirect"


def _recorded_vs_tree(relative: str, finalizer: str) -> list[tuple[str, str, str]]:
    """(name, recorded, on-disk) for every bound file whose hash has moved.

    Returns the empty list when the verdict still binds the shipping harness.
    Files the tables do not name are left to
    `test_source_files_holds_exactly_the_bound_repository_files`, which is the
    check that owns that failure.
    """
    verdict = _verdict(relative)
    paths = {name: source for table in _file_tables(finalizer) for name, source in table.items()}
    drifted: list[tuple[str, str, str]] = []
    for name, recorded in verdict["source"]["files"].items():
        source = paths.get(name)
        if source is None:
            continue
        actual = hashlib.sha256((REPO_ROOT / source).read_bytes()).hexdigest()
        if actual != recorded:
            drifted.append((name, recorded, actual))
    return drifted


@pytest.mark.parametrize("relative,finalizer", sorted(LIVE_VERDICTS.items()))
def test_a_live_verdict_still_binds_the_harness_that_ships(
    relative: str, finalizer: str
) -> None:
    """A live certification's bound files must still hash to what it recorded.

    This is the check the project has been performing by hand. Twice the RSOP
    lanes were re-run because a harness file moved underneath their verdicts,
    and both times the discovery was somebody thinking to look -- while commit
    messages, the plan and the capability matrix all cite the binding as though
    something enforced it.

    The tests around this one are careful about everything adjacent and never
    hash a file: `source.files` KEYS are checked against the lane's tables, each
    bound path is checked to EXIST, and each verdict is checked against ITSELF.
    A verdict can satisfy all three while naming content the repository no
    longer has -- which is exactly the state the re-certifications existed to
    leave behind.
    """
    drifted = _recorded_vs_tree(relative, finalizer)
    assert not drifted, (
        f"{relative} is a live certification, but the harness it binds has "
        "changed: "
        + "; ".join(
            f"{name} recorded {rec[:12]}, tree has {act[:12]}" for name, rec, act in drifted
        )
        + ". Either re-run the lane so the verdict binds the shipping code, or "
        "move it to RETIRED_VERDICTS with a reason if it is now history."
    )


def test_retired_verdicts_are_genuinely_stale() -> None:
    """The control, and the thing that stops `RETIRED_VERDICTS` being a hatch.

    Two properties, and both matter:

    1. **The check above can fail.** A gate nobody has seen fail is a gate
       nobody should trust -- this project has already been burned by a test
       whose assertions were satisfied by the file's own contents, and a
       reviewer then cleared a hazard partly BECAUSE that test "pinned" it. Here
       the repository itself carries the negative case: 47 verdicts whose
       harness genuinely moved on. If the hashing logic ever silently stops
       hashing, this test goes red first.

    2. **A live claim cannot be parked here to silence the gate.** The cheap way
       out of a failing freshness check is to declare the verdict history. That
       only works if it really is history, because a retired verdict that still
       matches the tree fails right here.
    """
    not_stale = sorted(
        relative
        for relative in RETIRED_VERDICTS
        if not _recorded_vs_tree(relative, LANE_VERDICTS[relative])
    )
    assert not not_stale, (
        "These verdicts are listed as retired history but still bind the "
        f"shipping harness exactly: {not_stale}. A verdict that matches the "
        "tree is a live certification -- remove it from RETIRED_VERDICTS "
        "rather than retiring a claim that still holds."
    )


def test_pre_transport_verdicts_really_predate_the_transport_field() -> None:
    """The second escape hatch, closed the same way as the first.

    Found by cross-lineage review of PR #40 (deepseek), on work whose author had
    just written that `RETIRED_VERDICTS` "is not an escape hatch" — while this
    one sat unguarded two definitions away. `RETIRED_VERDICTS` got a control
    because it was the exemption on everyone's mind; `PRE_TRANSPORT_VERDICTS` is
    older, was only ever about the psdirect assertions, and quietly became a
    stronger hatch than the one that got guarded.

    The sequence it permitted, reproduced before this test was written: edit a
    harness file so a live verdict's hashes no longer match, then — instead of
    re-running the lane or retiring the verdict — delete it from `LANE_VERDICTS`
    and add it here. It is then in NO parametrised check: not the hash gate
    (over `LIVE_VERDICTS`), not the key, existence or consistency tests (over
    `LANE_VERDICTS`), and not the staleness control (over `RETIRED_VERDICTS`).
    `test_every_committed_verdict_is_covered` subtracts this set explicitly, so
    the coverage guard passes too. Measured: the mutation that failed eleven
    live verdicts failed only ten once one had been moved here.

    The membership test is the honest one rather than a proxy: these verdicts
    are exempt **because they were written before the lane recorded a
    transport**, so a member carrying a `transport` key is by definition not
    one. Every live verdict records `transport: psdirect`, so parking one here
    fails immediately — which is precisely the move this closes.
    """
    misfiled = sorted(
        relative for relative in PRE_TRANSPORT_VERDICTS if "transport" in _verdict(relative)
    )
    assert not misfiled, (
        "These verdicts are exempted as pre-transport but record a transport, "
        f"so they are not pre-transport at all: {misfiled}. A verdict that names "
        "its transport belongs in LANE_VERDICTS where its harness binding is "
        "checked."
    )
    # The control: an empty exemption set would satisfy the assertion above
    # while proving nothing, and this test exists to keep a hatch shut.
    assert PRE_TRANSPORT_VERDICTS, "the exemption set is empty; delete it rather than keeping it"


def test_the_live_set_is_not_empty_and_covers_every_lane() -> None:
    """Retiring everything would make the freshness check vacuous silently.

    `LIVE_VERDICTS` is a subtraction, so it degrades quietly: retire enough and
    the parametrised test above simply stops generating cases, reporting green
    for a repository whose every claim has expired. Each lane must keep at least
    one verdict that still binds the code it ships.
    """
    assert set(LANE_VERDICTS) >= RETIRED_VERDICTS, (
        "RETIRED_VERDICTS names verdicts that are not in LANE_VERDICTS: "
        f"{sorted(RETIRED_VERDICTS - set(LANE_VERDICTS))}"
    )
    lanes = {relative.split("/")[0] for relative in LIVE_VERDICTS}
    assert lanes == {relative.split("/")[0] for relative in LANE_VERDICTS}, (
        f"Some lane has no live certification left: {sorted(lanes)}"
    )


def test_wp0_manifest_is_a_pass_bound_to_a_resolvable_commit() -> None:
    """WP-0 binds by COMMIT, not by file hashes — so it is checked differently.

    Its manifest carries `source: {commit, dirty}` and no `files` block at all,
    which is why it is absent from `LANE_VERDICTS` and outside the freshness
    gate above. Nothing is wrong with that; it is a different binding. But it
    means the gate must not be read as covering WP-0.

    Until now this test asserted the `pass` and the clean tree and **never
    checked that the commit resolves**, while its name said it did. That is not
    a hypothetical gap: the 2026-08-03 evidence-binding audit found FOUR cited
    commits that no longer resolve, all squash-merge orphans, and issue #22's
    remedy (auto-tagging passing runs) exists precisely to stop it recurring.
    A test named for the property, that does not test the property, is how the
    next reviewer gets talked out of checking by hand.

    SKIPPED ON A SHALLOW CLONE, and that is not a dodge. CI checks out with
    actions/checkout's default `fetch-depth: 1`, so the object is genuinely
    absent there and asserting would fail a healthy commit — the failure mode
    would be noise, and noisy gates get deleted. Where the history is present
    (any developer clone, and any CI job that deepens its checkout) the
    property is enforced for real.
    """
    manifest = _verdict("wp0-evidence/wi059-20260908/wp0/manifest.json")
    assert manifest["capability"]["evidence_state"] == "pass"
    assert manifest["source"]["dirty"] is False
    assert "files" not in manifest["source"], (
        "The WP-0 manifest has grown a source.files block. It is not covered by "
        "the freshness gate — add it to LANE_VERDICTS so its hashes are checked."
    )

    shallow = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if shallow.stdout.strip() != "false":
        pytest.skip("shallow clone: the manifest's commit is not fetched here")

    commit = manifest["source"]["commit"]
    resolved = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    assert resolved.returncode == 0, (
        f"The WP-0 manifest binds commit {commit}, which this repository can no "
        "longer resolve — the certification is unverifiable. Squash-merge "
        "orphaning is the known cause; see docs/evidence-binding-audit-2026-08-03.md."
    )


def test_every_committed_verdict_is_covered() -> None:
    """The map must not be able to omit a verdict, which is how this failed.

    `LANE_VERDICTS` was hand-maintained, so a committed verdict was checked
    only if somebody remembered to add it. Twelve were not -- including all
    four `finding` verdicts, the ones carrying the most information. Nothing
    was wrong with the files; they were simply invisible to every test above.

    So coverage is now derived from the directory rather than asserted by
    memory. A new verdict is checked by default and can only escape by being
    named in `PRE_TRANSPORT_VERDICTS`, which is a deliberate act with a reason
    attached.
    """
    committed = {
        path.relative_to(EVIDENCE).as_posix()
        for path in EVIDENCE.glob("wp*-evidence/**/*.json")
        if path.name.startswith(("verdict-", "verification"))
    }
    unmapped = sorted(committed - set(LANE_VERDICTS) - PRE_TRANSPORT_VERDICTS)
    assert not unmapped, (
        "These verdicts are committed but checked by nothing: "
        f"{unmapped}. Add them to LANE_VERDICTS, or to PRE_TRANSPORT_VERDICTS "
        "with a reason."
    )


def test_the_coverage_guard_is_looking_at_real_files() -> None:
    """The control. A glob that matches nothing makes the test above vacuous."""
    committed = {
        path.relative_to(EVIDENCE).as_posix()
        for path in EVIDENCE.glob("wp*-evidence/**/*.json")
        if path.name.startswith(("verdict-", "verification"))
    }
    assert len(committed) >= len(LANE_VERDICTS)


@pytest.mark.parametrize(
    ("role", "domain_role", "host_name", "kerberos_keys"),
    [
        (
            "dc",
            5,
            "LABDC01",
            {
                "MaxTicketAge",
                "MaxRenewAge",
                "MaxServiceAge",
                "MaxClockSkew",
                "TicketValidateClient",
            },
        ),
        ("member", 3, "LABMS01", set()),
    ],
)
def test_wp3_policy_family_evidence_is_intact_and_role_scoped(
    role: str, domain_role: int, host_name: str, kerberos_keys: set[str]
) -> None:
    """The paired WP-3 runs retain their raw bytes and asymmetric host scope."""
    evidence_dir = EVIDENCE / "wp3-evidence" / "policy-families" / role
    verification = json.loads(
        (evidence_dir / "verification.json").read_text(encoding="utf-8")
    )
    assert verification["passed"] is True
    environment = verification["environment"]
    assert environment["computer_system_domain"] == "ad.labdomain.dev"
    assert environment["computer_system_domain_role"] == domain_role
    assert environment["computer_system_name"] == host_name

    expected = json.loads((evidence_dir / "expected.json").read_text(encoding="utf-8"))
    actual_kerberos = {
        setting["key"]
        for setting in expected["settings"]
        if setting["section"] == "Kerberos Policy"
    }
    assert actual_kerberos == kerberos_keys

    source_paths = {
        "build-wp3-candidate.py": REPO_ROOT / "scripts/plan-033/build-wp3-candidate.py",
        "finalize_wp3_run.py": REPO_ROOT / "scripts/windows-oracle/finalize_wp3_run.py",
        "policy_families.py": REPO_ROOT / "src/gpo_studio/policy_families.py",
        "psdirect.ps1": REPO_ROOT / "scripts/windows-oracle/psdirect.ps1",
        "run-wp3-oracle.sh": REPO_ROOT / "scripts/windows-oracle/run-wp3-oracle.sh",
        "run-wp3-security-template.ps1": (
            REPO_ROOT / "scripts/windows-oracle/run-wp3-security-template.ps1"
        ),
        "security_template.py": REPO_ROOT / "src/gpo_studio/security_template.py",
    }
    for relative_path, recorded_hash in verification["artifacts"].items():
        artifact = evidence_dir / Path(relative_path)
        source_name = relative_path.removeprefix("deployed/")
        if not artifact.is_file() and source_name in source_paths:
            artifact = source_paths[source_name]
        if not artifact.is_file() and relative_path in source_paths:
            artifact = source_paths[relative_path]
        assert artifact.is_file(), relative_path
        actual_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
        assert actual_hash == recorded_hash, relative_path


def test_object_security_evidence_preserves_native_rows_and_raw_artifacts() -> None:
    evidence_dir = EVIDENCE / "wp3-evidence" / "object-security"
    verification = json.loads((evidence_dir / "verification.json").read_text(encoding="utf-8"))
    assert verification["passed"] is True
    assert verification["environment"]["computer_system_domain_role"] == 3
    assert verification["environment"]["computer_system_name"] == "LABMS01"
    assert verification["environment"]["computer_system_domain"] == "ad.labdomain.dev"
    symbols = runpy.run_path(str(ORACLE_DIR / "finalize_object_security_run.py"))
    source_paths = {
        name: REPO_ROOT / relative
        for table in _file_tables("finalize_object_security_run.py")
        for name, relative in table.items()
    }
    for relative, digest in verification["artifacts"].items():
        artifact = evidence_dir / relative
        if not artifact.is_file():
            artifact = source_paths[relative.removeprefix("deployed/")]
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == digest, relative
    expected_json = json.loads((evidence_dir / "expected.json").read_text(encoding="utf-8"))
    expected = symbols["_expected_rows"](expected_json)
    assert len(expected) == 9
    assert symbols["_template_rows"](evidence_dir / "candidate.inf", exported=False) == expected
    assert symbols["_template_rows"](evidence_dir / "exported.inf", exported=True) == expected
    for section, codes in (
        ("registry keys", {0, 1, 2}),
        ("file security", {0, 1, 2}),
        ("service general setting", {2, 3, 4}),
    ):
        assert {value[0] for key, value in expected.items() if key[0] == section} == codes


def test_scripts_metadata_evidence_binds_native_files_report_and_identity() -> None:
    evidence_dir = EVIDENCE / "wp1b-evidence" / "scripts-metadata"
    verification = json.loads((evidence_dir / "verification.json").read_text(encoding="utf-8"))
    result = json.loads((evidence_dir / "result.json").read_text(encoding="utf-8-sig"))
    assert verification["passed"] is True
    assert verification["environment"]["computer_system_domain_role"] == 3
    assert verification["environment"]["computer_system_name"] == "LABMS01"
    assert result["cleanup_state_restored"] is True
    assert result["report_links_to_count"] == 0
    symbols = runpy.run_path(str(ORACLE_DIR / "finalize_scripts_backup_run.py"))
    source_paths = {
        name: REPO_ROOT / relative
        for table in _file_tables("finalize_scripts_backup_run.py")
        for name, relative in table.items()
    }
    for relative, digest in verification["artifacts"].items():
        artifact = evidence_dir / relative
        if not artifact.is_file():
            artifact = source_paths[relative.removeprefix("deployed/")]
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == digest, relative
    original = symbols["_candidate_projection"](evidence_dir / "candidate.zip")
    native = symbols["_rebackup_projection"](evidence_dir / "rebackup")
    assert original == verification["original_projection"]
    assert native == verification["rebackup_projection"]
    assert original["files"] == native["files"]
    assert len(native["files"]) == 2
    assert native["user_extension_pair"] == ""
    assert len(native["wildcard_references"]) == 4
    assert symbols["_report_matches"](
        evidence_dir / "report.xml", result["owned_gpo_id"], result["target_name"], result["domain"]
    )
    assert verification["candidate_delivery"]["controller_sha256"] == verification[
        "candidate_delivery"
    ]["guest_sha256"] == hashlib.sha256((evidence_dir / "candidate.zip").read_bytes()).hexdigest()


def test_every_evidence_file_is_accounted_for() -> None:
    """The widening WI-053 owes: a verdict cannot escape by filename again.

    `test_every_committed_verdict_is_covered` derives coverage from the
    directory, but only for names matching the verdict prefixes -- which is
    exactly how `endpoint-result-phase4-estate.json` spent five weeks as the
    endpoint lane's only certification while no gate saw it. The derivation was
    only as wide as its pattern, so mapping that one instance left the hole
    open one file along.

    So the universe here is every JSON in an evidence directory, not just the
    verdict-named ones. Verdict-named files are checked by the test above; every
    other file must be named in `NON_VERDICT_EVIDENCE_FILES` with its reason.
    Renaming a verdict to something unusual no longer removes it from every
    gate -- it lands here as unaccounted, and this fails, and the person
    holding the new name has to say what the file is.
    """
    on_disk = {
        path.relative_to(EVIDENCE).as_posix()
        for path in EVIDENCE.glob("wp*-evidence/*.json")
    }
    verdict_named = {
        relative
        for relative in on_disk
        if Path(relative).name.startswith(("verdict-", "verification"))
    }
    unaccounted = sorted(on_disk - verdict_named - set(NON_VERDICT_EVIDENCE_FILES))
    assert not unaccounted, (
        "These evidence files match neither verdict prefix nor "
        "NON_VERDICT_EVIDENCE_FILES: "
        f"{unaccounted}. If one is a verdict, give it a covered name and map "
        "it in LANE_VERDICTS; otherwise name it in NON_VERDICT_EVIDENCE_FILES "
        "with the reason it is not a claim."
    )
    vanished = sorted(set(NON_VERDICT_EVIDENCE_FILES) - on_disk)
    assert not vanished, (
        "NON_VERDICT_EVIDENCE_FILES names files that are no longer committed: "
        f"{vanished}. Remove the entry -- a named exemption should not outlive "
        "the file it exempted."
    )


def test_the_widened_guard_still_sees_the_endpoint_verdict() -> None:
    """The control WI-053 names: the widening must not quietly un-see its case.

    WI-053's defect was one specific verdict -- the endpoint lane's
    certification -- escaping every gate through its filename. This pins that
    the same verdict is still matched by the widened guard's pattern and still
    mapped, so a future tidy-up that narrows the glob, changes the prefixes, or
    renames the file fails here instead of silently re-creating the original
    hole.
    """
    endpoint_verdict = (
        "wp6-evidence/verdict-endpoint-observe-20260906185837-7523.json"
    )
    assert (EVIDENCE / endpoint_verdict).is_file(), (
        "The endpoint lane's re-certification is gone from the tree. If it was "
        "renamed, the new name must still match the verdict prefixes and be "
        "remapped in LANE_VERDICTS -- and this test updated to the new name "
        "deliberately."
    )
    matched = {
        path.relative_to(EVIDENCE).as_posix()
        for path in EVIDENCE.glob("wp*-evidence/*.json")
        if path.name.startswith(("verdict-", "verification"))
    }
    assert endpoint_verdict in matched, (
        "The coverage guard's pattern no longer matches the endpoint "
        "certification -- the exact escape WI-053 closed. Widen the pattern "
        "rather than re-creating the hole."
    )
    assert endpoint_verdict in LANE_VERDICTS


#: Phrases that assert a lane's certification cannot be verified from the
#: repository. Each was true when written; what this guards is that they stayed
#: in the tree after the lane was re-certified.
UNVERIFIABLE_CLAIMS = (
    "evidence binding broken",
    "re-certification queued",
    "queued for re-certification",
    "committed no evidence manifest",
    "prose record, not a verifiable certification",
    "cannot be verified from the repository",
    "no longer independently checkable",
)

STATUS_DOC_ROOTS = ("docs", "plans")


def _certified_runs() -> dict[str, str]:
    """Lane -> run id, for every lane whose committed manifest is a clean pass.

    `passed` is absent on WP-0's manifest, which records `evidence_state`
    instead; both shapes are accepted rather than silently skipping WP-0, which
    is one of the two lanes this drift originally affected.
    """
    runs: dict[str, str] = {}
    for path in EVIDENCE.glob("wp*-evidence/*.json"):
        if not path.name.startswith(("verification", "manifest")):
            continue
        verdict = json.loads(path.read_text(encoding="utf-8"))
        source = verdict.get("source") or {}
        state = (verdict.get("capability") or {}).get("evidence_state")
        if source.get("dirty") is not False:
            continue
        if verdict.get("passed") is True or state == "pass":
            run_id = verdict.get("run_id")
            if isinstance(run_id, str) and run_id:
                runs[path.parent.name.removesuffix("-evidence")] = run_id
    return runs


def test_no_status_document_calls_a_certified_lane_unverifiable() -> None:
    """Status prose must not contradict a committed passing manifest.

    This is the seventh recurrence of status drift here and the first
    mechanical check on it. `test_domain_layer_status.py` gates the register
    against *itself*; it cannot see a document that is internally consistent
    and merely out of date with the evidence. WP-2 was exactly that:
    re-certified 2026-08-03 with a clean manifest bound to a resolvable commit,
    while two status documents went on saying its binding was broken and
    re-certification was queued.

    What this checks is narrow, and the narrowness is the point. It cannot
    verify that a status is *true*. It catches one specific repeated falsehood:
    prose calling a lane unverifiable when that lane's manifest is a committed,
    clean pass.

    The escape hatch is deliberately expensive. An earlier draft let a
    paragraph off for containing a word like "superseded" or "resolved", and a
    mutation test walked straight through it -- the stale WP-2 block quote was
    long enough to contain such a word further down, so the guard passed on the
    exact text it was written to catch. A vague marker is not evidence. So a
    paragraph may only speak of a certified lane in the past tense if it names
    **the run id that superseded the claim**, which cannot be satisfied without
    going and reading the manifest.
    """
    certified = _certified_runs()
    assert certified, "no lane has a clean manifest; this test would be vacuous"

    stale: list[str] = []
    for root in STATUS_DOC_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*.md")):
            for paragraph in path.read_text(encoding="utf-8").split("\n\n"):
                lowered = paragraph.lower()
                claim = next((c for c in UNVERIFIABLE_CLAIMS if c in lowered), None)
                if claim is None:
                    continue
                # A paragraph naming several lanes is reported once, against all
                # of them: which lane such a sentence is *about* is a question
                # only prose can answer, and guessing would trade this test's
                # precision for a plausible-looking attribution.
                unresolved = sorted(
                    f"{lane} ({run_id})"
                    for lane, run_id in certified.items()
                    if lane in lowered and run_id.lower() not in lowered
                )
                if unresolved:
                    stale.append(
                        f"{path.relative_to(REPO_ROOT)}: {claim!r}, "
                        f"but these are certified: {', '.join(unresolved)}"
                    )

    assert not stale, (
        "These paragraphs call a lane unverifiable when its committed manifest "
        "is a clean pass:\n  " + "\n  ".join(stale) + "\nReconcile the prose "
        "with the evidence, or cite the superseding run id in the same "
        "paragraph to mark the claim as history."
    )


#: Verdict commits that are KNOWN not to resolve, with the reason.
#:
#: Enumerated rather than tolerated by pattern, for the reason
#: `RETIRED_VERDICTS` gives: an exemption should be a deliberate act with a
#: reason attached, not something a file drifts into. And it is not a hatch --
#: `test_the_orphaned_commit_exemption_is_still_orphaned` fails if one of these
#: starts resolving again.
ORPHANED_VERDICT_COMMITS = {
    # Run `wp3-security-template-20260727220623-7682`, cited by
    # `wp3-evidence/verification.json`. A squash-merge orphan predating the
    # issue #22 auto-tagging remedy, so it cannot be retro-tagged: the commit
    # was already unreachable when that remedy landed. Found 2026-09-06 and
    # recorded in `docs/evidence-binding-audit-2026-08-03.md`, which had missed
    # it because that audit scanned prose for hex adjacent to the word
    # "commit" and never looked inside the verdict JSON.
    #
    # Nothing rests on it: WP-3 has a live certification in
    # `verification-estate.json`.
    "fdb46004c2f838f5b5eb6a693ebdf7f99d4ee71a",
}


def _history_is_complete() -> bool:
    shallow = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    return shallow.stdout.strip() == "false"


def _verdict_commits() -> dict[str, list[str]]:
    """commit -> the verdict files that bind it, over every committed verdict."""
    bound: dict[str, list[str]] = {}
    paths = set(EVIDENCE.glob("wp*-evidence/*.json"))
    paths.update(EVIDENCE / relative for relative in LANE_VERDICTS)
    paths.update(EVIDENCE.glob("wp0-evidence/**/manifest.json"))
    for path in sorted(paths):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        commit = (document.get("source") or {}).get("commit")
        if isinstance(commit, str) and commit:
            bound.setdefault(commit, []).append(path.relative_to(EVIDENCE).as_posix())
    return bound


def _resolves(commit: str) -> bool:
    return subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=REPO_ROOT, capture_output=True, check=False,
    ).returncode == 0


def _preserved_by_a_ref(commit: str) -> bool:
    """Is this commit reachable from a branch or a tag?

    Resolution alone is weaker than it looks on a developer clone: an orphaned
    object survives in the object database until it is garbage collected, so
    `cat-file -e` can succeed for a commit no ref reaches. Reachability is the
    property that actually makes a certification re-derivable by someone else.
    """
    for command in (["git", "branch", "-a", "--contains", commit],
                    ["git", "tag", "--contains", commit]):
        found = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True,
                               check=False)
        if found.returncode == 0 and found.stdout.strip():
            return True
    return False


def test_every_verdict_binds_a_commit_this_repository_still_has() -> None:
    """The check the 2026-08-03 evidence-binding audit should have been.

    That audit extracted hex tokens adjacent to the word "commit" from
    `docs/**/*.md` and `plans/**/*.md`. A verdict is JSON and its binding lives
    in `source.commit`, so the files whose entire purpose is to bind a result to
    a tree were the one place it never scanned -- which is how a fifth orphan
    sat unnoticed for five weeks while the audit above it read as complete.

    A verdict naming an unreachable commit is not wrong about what happened. It
    is no longer INDEPENDENTLY CHECKABLE: every one of these runs asserts some
    form of "the harness that executed matched the committed source tree", and
    that assertion cannot be re-derived once the tree is gone.

    SKIPPED ON A SHALLOW CLONE, for the reason
    `test_wp0_manifest_is_a_pass_bound_to_a_resolvable_commit` gives: CI checks
    out at `fetch-depth: 1`, where every commit here is legitimately absent and
    asserting would fail a healthy repository. Measured rather than assumed --
    the first manual pass of this check ran against a shallow clone and reported
    38 orphans, of which 37 were the clone.
    """
    if not _history_is_complete():
        pytest.skip("shallow clone: no verdict's commit is fetched here")

    bound = _verdict_commits()
    assert bound, "no committed verdict names a source commit; this test is vacuous"

    broken = sorted(
        (commit, files)
        for commit, files in bound.items()
        if commit not in ORPHANED_VERDICT_COMMITS
        and not (_resolves(commit) and _preserved_by_a_ref(commit))
    )
    assert not broken, (
        "These verdicts bind a commit this repository cannot reach, so their "
        "harness-matched-the-source claim can no longer be re-derived:\n  "
        + "\n  ".join(f"{commit[:12]} <- {', '.join(files)}" for commit, files in broken)
        + "\nSquash-merge orphaning is the known cause. If the commit is "
        "genuinely gone, record it in ORPHANED_VERDICT_COMMITS with a reason "
        "and add it to docs/evidence-binding-audit-2026-08-03.md. If it is not, "
        "push the evidence tag that preserves it -- a tag protects nothing "
        "until it is on the remote."
    )


def test_the_orphaned_commit_exemption_is_still_orphaned() -> None:
    """The control, and what stops the exemption above becoming a hatch.

    The cheap way out of the check above is to declare a commit orphaned. That
    only works if it really is: a listed commit that resolves again fails here,
    the same way `test_retired_verdicts_are_genuinely_stale` guards
    `RETIRED_VERDICTS`.
    """
    if not _history_is_complete():
        pytest.skip("shallow clone: nothing resolves here, so this proves nothing")

    assert ORPHANED_VERDICT_COMMITS, (
        "the exemption set is empty; delete it rather than keeping an unused hatch"
    )
    recovered = sorted(c for c in ORPHANED_VERDICT_COMMITS if _resolves(c))
    assert not recovered, (
        f"These commits are listed as orphaned but resolve: {recovered}. Remove "
        "them from ORPHANED_VERDICT_COMMITS rather than exempting a binding that "
        "is intact."
    )


def test_every_orphaned_commit_is_actually_bound_by_a_verdict() -> None:
    """The second control: the exemption cannot outlive the verdict it excuses.

    If the verdict citing an orphaned commit is deleted or re-certified, the
    entry here becomes a permanent excuse for nothing, and the next reader has
    to work out whether it still means anything.
    """
    stale = sorted(ORPHANED_VERDICT_COMMITS - set(_verdict_commits()))
    assert not stale, (
        f"These commits are exempted but no committed verdict binds them: {stale}. "
        "Remove the entry -- the verdict it excused is gone."
    )
