from __future__ import annotations

import sys

import pytest
import typer
from unittest.mock import patch

from lemma_cli.cli_core.confirm import confirm_destructive


def test_confirm_with_yes_skips_prompt():
    # When yes=True, no prompt should be raised and function returns None.
    result = confirm_destructive("Delete?", yes=True)
    assert result is None


def test_confirm_noninteractive_without_yes_raises():
    # Non-interactive stdin (not a TTY) without --yes must exit with non-zero code.
    with pytest.raises((typer.Exit, SystemExit)) as exc_info:
        with patch.object(sys.stdin, "isatty", return_value=False):
            confirm_destructive("Delete?", yes=False)
    exc = exc_info.value
    if isinstance(exc, typer.Exit):
        assert exc.exit_code != 0
    # SystemExit with non-zero is also acceptable
    elif isinstance(exc, SystemExit):
        assert exc.code != 0


def test_confirm_interactive_user_says_no_raises():
    # If the user says no at the prompt, expect an exit.
    with pytest.raises((typer.Exit, SystemExit)) as exc_info:
        with patch.object(sys.stdin, "isatty", return_value=True):
            with patch("typer.confirm", return_value=False):
                confirm_destructive("Delete?", yes=False)
    exc = exc_info.value
    if isinstance(exc, typer.Exit):
        assert exc.exit_code != 0


def test_confirm_interactive_user_says_yes_returns():
    # If the user says yes at the interactive prompt, function should return normally.
    with patch.object(sys.stdin, "isatty", return_value=True):
        with patch("typer.confirm", return_value=True):
            result = confirm_destructive("Delete?", yes=False)
    assert result is None
