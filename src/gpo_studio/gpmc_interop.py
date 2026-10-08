"""The issue type ``publication.py`` reports in.

Everything else this module held was deleted on 2026-10-07 by operator ruling
(``docs/direction-2026-10-07-plan-034-completion.md``): ``check_gpmc_interop``,
``check_backup_importable``, ``GpmcInteropReport`` and its ``is_gpmc_editable``
and ``is_gpmc_importable`` predicates.

The importable predicate was wrong in kind, not in detail. It reported a GPO
unimportable whenever its preserved CSE metadata named an extension Studio does
not emit, so it equated *Studio cannot emit this* with *GPMC cannot import
this*. Run over the R6 census, it would have flagged 15 of 26 production GPOs
that GPMC already holds. ``is_gpmc_editable`` had no oracle at all. The one
measured fact the module carried, the R6 extension vocabulary, lives in
``export.py``, and the publication lane certifies that byte for byte.

``InteropIssue`` stays because ``publication.py`` constructs it, and that file
is bound by the publication lane's verdict. This file is not in that verdict's
bound set, which is why it could be reduced without an estate run.
``tests/test_gpmc_interop.py`` pins the dataclass's shape so a change here
cannot quietly alter what the planner emits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

InteropCheckLevel = Literal["pass", "warning", "error"]


@dataclass(frozen=True, slots=True)
class InteropIssue:
    check: str                  # what was checked
    level: InteropCheckLevel
    message: str
    component: str = ""         # which GPO component is affected
