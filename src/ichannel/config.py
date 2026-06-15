from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    """Raised when required runtime configuration is missing or invalid."""


@dataclass(frozen=True)
class AppConfig:
    discord_token: str
    stream_user_id: int
    stream_username: str
    discord_dm_search: str
    android_tv_host: str
    android_tv_certfile: Path
    android_tv_keyfile: Path
    android_tv_client_name: str
    vlc_path: str
    vlc_args: list[str]
    vlc_process_names: list[str]
    vlc_window_title: str
    vlc_window_rect: tuple[int, int, int, int] | None
    vlc_window_show_cmd: int | None
    data_dir: Path
    log_file: Path
    state_file: Path
    command_sync_on_start: bool
    sync_commands_to_guild_id: int | None
    power_on_timeout_seconds: float
    android_tv_power_timeout_seconds: float
    discord_join_timeout_seconds: float
    discord_stream_timeout_seconds: float
    vlc_start_timeout_seconds: float
    desktop_automation_enabled: bool
    remote_key_rate_per_second: float
    remote_key_burst: int
    remote_number_rate_per_second: float
    remote_number_burst: int
    remote_key_queue_limit: int


def load_config(env_file: Path | str = ".env") -> AppConfig:
    env_path = Path(env_file)
    base_dir = env_path.resolve().parent
    env = _load_env_file(env_path)
    merged = {**env, **os.environ}

    data_dir = _resolve_path(base_dir, _get(merged, "DATA_DIR", "data"))
    state_file = data_dir / "state.json"

    stream_user_id_raw = _require(merged, "STREAM_USER_ID")
    try:
        stream_user_id = int(stream_user_id_raw)
    except ValueError as exc:
        raise ConfigError("STREAM_USER_ID must be a numeric Discord user ID") from exc

    sync_guild_id = _get_optional_int(merged, "SYNC_COMMANDS_TO_GUILD_ID")

    certfile = _resolve_path(base_dir, _require(merged, "ANDROID_TV_CERTFILE"))
    keyfile = _resolve_path(base_dir, _require(merged, "ANDROID_TV_KEYFILE"))
    log_file = _resolve_path(base_dir, _get(merged, "LOG_FILE", str(data_dir / "ichannel.log")))

    return AppConfig(
        discord_token=_require(merged, "DISCORD_BOT_TOKEN"),
        stream_user_id=stream_user_id,
        stream_username=_get(merged, "STREAM_USERNAME", "mr.veeseeksbox"),
        discord_dm_search=_get(merged, "DISCORD_DM_SEARCH", "iChangeChannels"),
        android_tv_host=_require(merged, "ANDROID_TV_HOST"),
        android_tv_certfile=certfile,
        android_tv_keyfile=keyfile,
        android_tv_client_name=_get(merged, "ANDROID_TV_CLIENT_NAME", "iChangeChannels"),
        vlc_path=_get(merged, "VLC_PATH", "vlc"),
        vlc_args=_split_args(_get(merged, "VLC_ARGS", "")),
        vlc_process_names=_split_csv(_get(merged, "VLC_PROCESS_NAMES", "vlc.exe,vlc")),
        vlc_window_title=_get(merged, "VLC_WINDOW_TITLE", "VLC media player"),
        vlc_window_rect=_get_optional_rect(merged, "VLC_WINDOW_RECT"),
        vlc_window_show_cmd=_get_optional_int(merged, "VLC_WINDOW_SHOW_CMD"),
        data_dir=data_dir,
        log_file=log_file,
        state_file=state_file,
        command_sync_on_start=_get_bool(merged, "COMMAND_SYNC_ON_START", True),
        sync_commands_to_guild_id=sync_guild_id,
        power_on_timeout_seconds=_get_float(merged, "POWER_ON_TIMEOUT_SECONDS", 45),
        android_tv_power_timeout_seconds=_get_float(
            merged, "ANDROID_TV_POWER_TIMEOUT_SECONDS", 12
        ),
        discord_join_timeout_seconds=_get_float(merged, "DISCORD_JOIN_TIMEOUT_SECONDS", 20),
        discord_stream_timeout_seconds=_get_float(
            merged, "DISCORD_STREAM_TIMEOUT_SECONDS", 25
        ),
        vlc_start_timeout_seconds=_get_float(merged, "VLC_START_TIMEOUT_SECONDS", 8),
        desktop_automation_enabled=_get_bool(merged, "DESKTOP_AUTOMATION_ENABLED", True),
        remote_key_rate_per_second=_get_float(merged, "REMOTE_KEY_RATE_PER_SECOND", 10),
        remote_key_burst=_get_int(merged, "REMOTE_KEY_BURST", 5),
        remote_number_rate_per_second=_get_float(merged, "REMOTE_NUMBER_RATE_PER_SECOND", 6),
        remote_number_burst=_get_int(merged, "REMOTE_NUMBER_BURST", 3),
        remote_key_queue_limit=_get_int(merged, "REMOTE_KEY_QUEUE_LIMIT", 5),
    )


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        values[key] = _unquote_env_value(key, value.strip())
    return values


def _unquote_env_value(key: str, value: str) -> str:
    if not value:
        return value
    first = value[0]
    last = value[-1]
    if first in {"'", '"'} or last in {"'", '"'}:
        if len(value) >= 2 and first == last and first in {"'", '"'}:
            return value[1:-1]
        raise ConfigError(f"{key} has mismatched quotes")
    return value


def _resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return base_dir / path


def _require(env: dict[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ConfigError(f"Missing required setting {key}")
    return value


def _get(env: dict[str, str], key: str, default: str) -> str:
    return env.get(key, default).strip() or default


def _get_bool(env: dict[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigError(f"{key} must be a boolean value")


def _get_float(env: dict[str, str], key: str, default: float) -> float:
    raw = env.get(key, "")
    if not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number") from exc


def _get_int(env: dict[str, str], key: str, default: int) -> int:
    raw = env.get(key, "")
    if not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer") from exc
    if value < 0:
        raise ConfigError(f"{key} must be zero or greater")
    return value


def _get_optional_int(env: dict[str, str], key: str) -> int | None:
    raw = env.get(key, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a numeric ID") from exc


def _get_optional_rect(env: dict[str, str], key: str) -> tuple[int, int, int, int] | None:
    raw = env.get(key, "").strip()
    if not raw:
        return None
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) != 4:
        raise ConfigError(f"{key} must use left,top,width,height format")
    try:
        left, top, width, height = [int(part) for part in parts]
    except ValueError as exc:
        raise ConfigError(f"{key} must contain integer values") from exc
    if width <= 0 or height <= 0:
        raise ConfigError(f"{key} width and height must be positive")
    return left, top, width, height


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _split_args(value: str) -> list[str]:
    if not value.strip():
        return []
    try:
        import shlex

        shlex.split(value, posix=True)
        return shlex.split(value, posix=False)
    except ValueError as exc:
        raise ConfigError(f"VLC_ARGS contains invalid quoting: {exc}") from exc
