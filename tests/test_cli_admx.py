"""The launcher accepts a template folder without requiring environment setup."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

from gpo_studio.__main__ import main


@pytest.mark.parametrize("command", [[], ["run"]])
def test_admx_flag_overrides_environment(
    command: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GPO_STUDIO_DB", "unchanged.db")
    monkeypatch.setenv("GPO_STUDIO_ADMX_DIR", "environment-templates")
    monkeypatch.setattr(sys, "argv", ["gpo-studio", *command, "--admx-dir", "template folder"])

    with patch("gpo_studio.__main__.uvicorn.run") as run:
        main()

    assert os.environ["GPO_STUDIO_ADMX_DIR"] == "template folder"
    run.assert_called_once_with("gpo_studio.api:app", host="127.0.0.1", port=8765)


@pytest.mark.parametrize("command", [[], ["run"]])
@pytest.mark.parametrize("environment", [None, "environment-templates"])
def test_omitted_admx_flag_preserves_environment_fallback(
    command: list[str], environment: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GPO_STUDIO_DB", "unchanged.db")
    if environment is None:
        monkeypatch.delenv("GPO_STUDIO_ADMX_DIR", raising=False)
    else:
        monkeypatch.setenv("GPO_STUDIO_ADMX_DIR", environment)
    monkeypatch.setattr(sys, "argv", ["gpo-studio", *command])

    with patch("gpo_studio.__main__.uvicorn.run") as run:
        main()

    assert os.environ.get("GPO_STUDIO_ADMX_DIR") == environment
    run.assert_called_once()


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["--admx-dir", "root-templates", "run"], "root-templates"),
        (
            ["--admx-dir", "root-templates", "run", "--admx-dir", "run-templates"],
            "run-templates",
        ),
    ],
)
def test_admx_flag_before_subcommand_is_preserved(
    arguments: list[str], expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GPO_STUDIO_DB", "unchanged.db")
    monkeypatch.setenv("GPO_STUDIO_ADMX_DIR", "environment-templates")
    monkeypatch.setattr(sys, "argv", ["gpo-studio", *arguments])

    with patch("gpo_studio.__main__.uvicorn.run"):
        main()

    assert os.environ["GPO_STUDIO_ADMX_DIR"] == expected


@pytest.mark.parametrize("command", [[], ["run"]])
def test_admx_flag_is_documented_in_help(
    command: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["gpo-studio", *command, "--help"])

    with patch("gpo_studio.__main__.uvicorn.run") as run, pytest.raises(SystemExit) as exit:
        main()

    assert exit.value.code == 0
    help_text = capsys.readouterr().out
    assert "--admx-dir" in help_text
    assert "GPO_STUDIO_ADMX_DIR" in help_text
    run.assert_not_called()
