"""Config: new backup-rotation and MCP-filter keys, init key preservation."""

import subprocess
import sys
from pathlib import Path

import pytest

from sp_cli import config as cfgmod
from sp_cli.config import ConfigError, init_config, load_config

REPO_ROOT = Path(__file__).resolve().parent.parent

BASE = '''
url = "http://127.0.0.1:1"
user = "u"
password = "p"
client_id = "T_test00"
'''


def write_config(tmp_path, monkeypatch, extra: str = "") -> Path:
    path = tmp_path / "config.toml"
    path.write_text(BASE + extra, encoding="utf-8")
    monkeypatch.setenv("SP_CLI_CONFIG", str(path))
    return path


class TestNewKeys:
    def test_defaults(self, tmp_path, monkeypatch):
        write_config(tmp_path, monkeypatch)
        cfg = load_config()
        assert cfg.backup_keep == 10
        assert cfg.manual_backup_dir is None
        assert cfg.manual_backup_keep is None
        assert cfg.mcp_read_only is False
        assert cfg.mcp_include is None and cfg.mcp_exclude is None

    def test_custom_values(self, tmp_path, monkeypatch):
        write_config(
            tmp_path,
            monkeypatch,
            'backup_keep = 25\n'
            'manual_backup_dir = "~/sp-backups"\n'
            'manual_backup_keep = 0\n'
            'mcp_read_only = true\n'
            'mcp_include = ["list", "today*"]\n'
            'mcp_exclude = ["provider*"]\n',
        )
        cfg = load_config()
        assert cfg.backup_keep == 25
        assert cfg.manual_backup_dir.endswith("sp-backups")
        assert "~" not in cfg.manual_backup_dir  # expanded
        assert cfg.manual_backup_keep == 0
        assert cfg.mcp_read_only is True
        assert cfg.mcp_include == ["list", "today*"]
        assert cfg.mcp_exclude == ["provider*"]

    @pytest.mark.parametrize(
        "line",
        [
            "backup_keep = -1",
            'backup_keep = "ten"',
            "backup_keep = true",
            "manual_backup_keep = -5",
            'mcp_read_only = "yes"',
            'mcp_include = "list"',
            "mcp_exclude = [1, 2]",
        ],
    )
    def test_invalid_values_rejected(self, tmp_path, monkeypatch, line):
        write_config(tmp_path, monkeypatch, line + "\n")
        with pytest.raises(ConfigError):
            load_config()


class TestInitPreservesExtraKeys:
    def test_reinit_keeps_unmanaged_keys_and_client_id(self, tmp_path, monkeypatch):
        write_config(
            tmp_path,
            monkeypatch,
            "backup_keep = 42\n"
            'mcp_exclude = ["provider*", "init"]\n'
            "mcp_read_only = true\n",
        )
        init_config(url="http://other:1", user="u2", password="p2")
        cfg = load_config()
        assert cfg.url == "http://other:1" and cfg.user == "u2"
        assert cfg.client_id == "T_test00"  # never regenerated
        assert cfg.backup_keep == 42
        assert cfg.mcp_exclude == ["provider*", "init"]
        assert cfg.mcp_read_only is True

    def test_toml_value_serialization_round_trip(self):
        assert cfgmod._toml_value(True) == "true"
        assert cfgmod._toml_value(7) == "7"
        assert cfgmod._toml_value('a"b') == '"a\\"b"'
        assert cfgmod._toml_value(["x", "y"]) == '["x", "y"]'


class TestMcpFiltersFromConfig:
    def _list_tools(self, tmp_path, monkeypatch, extra: str) -> list[str]:
        write_config(tmp_path, monkeypatch, extra)
        import os

        env = dict(os.environ)
        env["SP_CLI_CONFIG"] = str(tmp_path / "config.toml")
        out = subprocess.run(
            [sys.executable, "-m", "sp_cli.mcp_server", "--list-tools"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert out.returncode == 0, out.stderr
        return [l.split("\t")[0] for l in out.stdout.splitlines()]

    def test_config_exclude_applies(self, tmp_path, monkeypatch):
        names = self._list_tools(tmp_path, monkeypatch, 'mcp_exclude = ["provider*"]\n')
        assert "sp_providers" not in names and "sp_list" in names

    def test_config_read_only_applies(self, tmp_path, monkeypatch):
        names = self._list_tools(tmp_path, monkeypatch, "mcp_read_only = true\n")
        assert "sp_delete" not in names and "sp_list" in names
