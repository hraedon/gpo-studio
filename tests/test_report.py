from __future__ import annotations

from gpo_studio.model import GPO, RegistrySetting
from gpo_studio.report import policy_report


def test_policy_report_is_deterministic_plain_text() -> None:
    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Unicode policy — 東京",
        description="Review <script>alert('inert')</script>",
        revision=3,
        settings=(
            RegistrySetting(
                id="setting-1",
                side="computer",
                hive="HKLM",
                key=r"Software\Policies\Synthetic",
                value_name="Maximum",
                registry_type="REG_QWORD",
                value="18446744073709551615",
            ),
        ),
    )

    first = policy_report(gpo)
    assert first == policy_report(gpo)
    assert "GPO Studio policy report" in first
    assert "Unicode policy — 東京" in first
    assert "18446744073709551615" in first
    assert "Policy semantic SHA-256:" in first
    assert "<script>" in first  # safe because the API serves text/plain
