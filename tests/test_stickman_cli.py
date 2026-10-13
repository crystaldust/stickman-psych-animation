import pytest

from stickman import cli
from stickman.collect.bilibili import cli as bili_cli


def test_bili_subcommand_forwards_remaining_args(monkeypatch):
    calls = []
    monkeypatch.setattr(bili_cli, "main", lambda argv, prog: calls.append((argv, prog)) or 0)

    code = cli.main(["bili", "--cookies-from-browser", "chrome", "list", "42", "--limit", "3"])

    assert code == 0
    assert calls == [(["--cookies-from-browser", "chrome", "list", "42", "--limit", "3"], "stickman bili")]


def test_bili_exit_code_is_propagated(monkeypatch):
    monkeypatch.setattr(bili_cli, "main", lambda argv, prog: 2)

    assert cli.main(["bili", "fetch", "42"]) == 2


def test_unknown_or_missing_command_is_a_usage_error(capsys):
    with pytest.raises(SystemExit):
        cli.main([])
    with pytest.raises(SystemExit):
        cli.main(["nope"])


def test_help_lists_subcommands(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])

    assert "bili" in capsys.readouterr().out
