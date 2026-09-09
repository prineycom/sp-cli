"""Config: ~/.config/sp-cli/config.toml (override via SP_CLI_CONFIG)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from sp_cli.ids import new_client_id

DEFAULT_CONFIG_PATH = "~/.config/sp-cli/config.toml"
DEFAULT_FOLDER = "superproductivity"
DEFAULT_BACKUP_DIR = "~/.local/share/sp-cli/backups"


class ConfigError(Exception):
    pass


@dataclass
class Config:
    url: str
    folder: str
    user: str
    password: str
    client_id: str
    backup_dir: str


def config_path() -> Path:
    return Path(
        os.environ.get("SP_CLI_CONFIG") or os.path.expanduser(DEFAULT_CONFIG_PATH)
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
    return Config(
        url=data["url"].rstrip("/"),
        folder=data.get("folder", DEFAULT_FOLDER).strip("/"),
        user=data["user"],
        password=password,
        client_id=data["client_id"],
        backup_dir=os.path.expanduser(data.get("backup_dir", DEFAULT_BACKUP_DIR)),
    )


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def init_config(
    url: str,
    user: str,
    password: str | None = None,
    password_file: str | None = None,
    folder: str = DEFAULT_FOLDER,
    backup_dir: str | None = None,
) -> Path:
    """Create (or update) the config file. client_id is generated once and
    never regenerated if a config with one already exists."""
    path = config_path()
    client_id = None
    if path.is_file():
        try:
            with open(path, "rb") as f:
                client_id = tomllib.load(f).get("client_id")
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path
