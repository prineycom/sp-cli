"""Config: ~/.config/sp-cli/config.toml (override via SP_CLI_CONFIG)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from sp_cli.ids import new_client_id

DEFAULT_CONFIG_PATH = "~/.config/sp-cli/config.toml"
DEFAULT_FOLDER = "superproductivity"
DEFAULT_DATA_DIR = "~/.local/share/sp-cli"
DEFAULT_BACKUP_DIR = f"{DEFAULT_DATA_DIR}/backups"
DEFAULT_TIMER_PATH = f"{DEFAULT_DATA_DIR}/timer.json"


class ConfigError(Exception):
    pass


DEFAULT_BACKUP_KEEP = 10


@dataclass
class Config:
    url: str
    folder: str
    user: str
    password: str
    client_id: str
    backup_dir: str
    # rotation of automatic pre-write backups in backup_dir (0 = keep all)
    backup_keep: int = DEFAULT_BACKUP_KEEP
    # defaults for `sp backup` when --dir/--keep are not given
    manual_backup_dir: str | None = None
    manual_backup_keep: int | None = None
    # sp-mcp tool filtering (CLI flags override these)
    mcp_read_only: bool = False
    mcp_include: list[str] | None = None
    mcp_exclude: list[str] | None = None


def config_path() -> Path:
    return Path(
        os.environ.get("SP_CLI_CONFIG") or os.path.expanduser(DEFAULT_CONFIG_PATH)
    )


def timer_path() -> Path:
    """Local live-timer state file (never synced). Override via SP_CLI_TIMER."""
    return Path(
        os.environ.get("SP_CLI_TIMER") or os.path.expanduser(DEFAULT_TIMER_PATH)
    )


def load_config() -> Config:
    path = config_path()
    if not path.is_file():
        raise ConfigError(
            f"config not found at {path}; run `sp init --url ... --user ... --password ...`"
        )
    with open(path, "rb") as f:
        data = tomllib.load(f)
    for key in ("url", "user", "client_id"):
        if not data.get(key):
            raise ConfigError(f"config {path}: missing required key '{key}'")
    password = data.get("password")
    if not password and data.get("password_file"):
        pw_path = Path(os.path.expanduser(data["password_file"]))
        try:
            password = pw_path.read_text(encoding="utf-8").strip()
        except OSError as e:
            raise ConfigError(f"cannot read password_file {pw_path}: {e}") from e
    if not password:
        raise ConfigError(f"config {path}: set 'password' or 'password_file'")

    def _keep(key: str, default: int | None) -> int | None:
        value = data.get(key, default)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError(
                f"config {path}: '{key}' must be an integer >= 0 (0 keeps everything)"
            )
        return value

    def _globs(key: str) -> list[str] | None:
        value = data.get(key)
        if value is None:
            return None
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise ConfigError(
                f"config {path}: '{key}' must be a list of command globs"
            )
        return value

    mcp_read_only = data.get("mcp_read_only", False)
    if not isinstance(mcp_read_only, bool):
        raise ConfigError(f"config {path}: 'mcp_read_only' must be true or false")

    manual_dir = data.get("manual_backup_dir")
    return Config(
        url=data["url"].rstrip("/"),
        folder=data.get("folder", DEFAULT_FOLDER).strip("/"),
        user=data["user"],
        password=password,
        client_id=data["client_id"],
        backup_dir=os.path.expanduser(data.get("backup_dir", DEFAULT_BACKUP_DIR)),
        backup_keep=_keep("backup_keep", DEFAULT_BACKUP_KEEP),
        manual_backup_dir=os.path.expanduser(manual_dir) if manual_dir else None,
        manual_backup_keep=_keep("manual_backup_keep", None),
        mcp_read_only=mcp_read_only,
        mcp_include=_globs("mcp_include"),
        mcp_exclude=_globs("mcp_exclude"),
    )


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _toml_value(value) -> str:
    """Serialize the config value types we support (str/int/bool/list of str)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return f'"{_toml_escape(value)}"'
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    raise ConfigError(f"cannot serialize config value of type {type(value).__name__}")


def init_config(
    url: str,
    user: str,
    password: str | None = None,
    password_file: str | None = None,
    folder: str = DEFAULT_FOLDER,
    backup_dir: str | None = None,
) -> Path:
    """Create (or update) the config file. client_id is generated once and
    never regenerated if a config with one already exists; keys that init
    itself does not manage (backup_keep, mcp_* etc.) are carried over."""
    path = config_path()
    client_id = None
    extra: dict = {}
    managed = {
        "url", "folder", "user", "password", "password_file",
        "client_id", "backup_dir",
    }
    if path.is_file():
        try:
            with open(path, "rb") as f:
                existing = tomllib.load(f)
            client_id = existing.get("client_id")
            extra = {k: v for k, v in existing.items() if k not in managed}
        except (tomllib.TOMLDecodeError, OSError):
            client_id = None
    if not client_id:
        client_id = new_client_id()
    if not password and not password_file:
        raise ConfigError("init: provide --password or --password-file")
    lines = [
        f'url = "{_toml_escape(url.rstrip("/"))}"',
        f'folder = "{_toml_escape(folder.strip("/"))}"',
        f'user = "{_toml_escape(user)}"',
    ]
    if password:
        lines.append(f'password = "{_toml_escape(password)}"')
    if password_file:
        lines.append(f'password_file = "{_toml_escape(password_file)}"')
    lines.append(f'client_id = "{_toml_escape(client_id)}"')
    if backup_dir:
        lines.append(f'backup_dir = "{_toml_escape(backup_dir)}"')
    for key in sorted(extra):
        lines.append(f"{key} = {_toml_value(extra[key])}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path
