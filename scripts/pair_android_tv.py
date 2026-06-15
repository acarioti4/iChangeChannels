from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
MDNS_SERVICE = "_androidtvremote2._tcp.local."
DISCOVERY_SECONDS = 5


@dataclass(frozen=True)
class PairingSettings:
    host: str
    certfile: Path
    keyfile: Path
    client_name: str


@dataclass(frozen=True)
class DiscoveredDevice:
    name: str
    host: str
    port: int


async def main() -> int:
    from androidtvremote2 import AndroidTVRemote, ConnectionClosed, InvalidAuth

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    settings = _load_pairing_settings()
    settings.certfile.parent.mkdir(parents=True, exist_ok=True)
    settings.keyfile.parent.mkdir(parents=True, exist_ok=True)

    remote = AndroidTVRemote(
        settings.client_name,
        str(settings.certfile),
        str(settings.keyfile),
        settings.host,
        enable_voice=False,
    )
    generated = await remote.async_generate_cert_if_missing()
    if generated:
        print("Generated new Android TV certificate and key files.")

    name, mac = await remote.async_get_name_and_mac()
    print(f"Pairing with {name} ({mac}) at {settings.host}.")
    print("Keep the Android TV screen visible and approve the pairing prompt.")

    await remote.async_start_pairing()
    while True:
        code = input("Enter the pairing code shown on the TV: ").strip()
        try:
            await remote.async_finish_pairing(code)
            print("Pairing complete.")
            break
        except InvalidAuth:
            print("That pairing code was rejected. Try again.")
        except ConnectionClosed:
            print("Pairing session reset. Starting again.")
            await remote.async_start_pairing()

    await remote.async_connect()
    print(f"Connected. is_on={remote.is_on}, current_app={remote.current_app}")
    remote.disconnect()
    return 0


def _load_pairing_settings() -> PairingSettings:
    env = _read_env_file(ENV_FILE)

    configured_host = env.get("ANDROID_TV_HOST", "").strip()
    host = _choose_host(configured_host)
    _maybe_update_env_host(host)

    return PairingSettings(
        host=host,
        certfile=_resolve_path(env.get("ANDROID_TV_CERTFILE", "data/androidtv_cert.pem")),
        keyfile=_resolve_path(env.get("ANDROID_TV_KEYFILE", "data/androidtv_key.pem")),
        client_name=env.get("ANDROID_TV_CLIENT_NAME", "iChangeChannels").strip()
        or "iChangeChannels",
    )


def _choose_host(configured_host: str) -> str:
    if configured_host:
        print(f"Configured ANDROID_TV_HOST: {configured_host}")
        answer = input("Use this host? [Y/n]: ").strip().lower()
        if answer in {"", "y", "yes"}:
            return configured_host

    devices = _discover_android_tvs()
    if devices:
        print("")
        print("Discovered Android TV devices:")
        for index, device in enumerate(devices, start=1):
            print(f"{index}. {device.name} - {device.host}:{device.port}")

        while True:
            answer = input("Choose a device number, or press Enter for manual host: ").strip()
            if not answer:
                break
            try:
                selected = devices[int(answer) - 1]
                return selected.host
            except (ValueError, IndexError):
                print("Choose one of the listed numbers.")
    else:
        print("No Android TV Remote Protocol devices were discovered.")

    while True:
        host = input("Enter the Android TV IP address or hostname: ").strip()
        if host:
            return host


def _discover_android_tvs() -> list[DiscoveredDevice]:
    print(f"Scanning for Android TV devices for {DISCOVERY_SECONDS}s...")
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    except ImportError:
        print("zeroconf is not installed. Install requirements.txt or enter the host manually.")
        return []

    class Listener(ServiceListener):
        def __init__(self) -> None:
            self.devices: dict[str, DiscoveredDevice] = {}

        def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            info = zc.get_service_info(type_, name)
            if info is None:
                return
            addresses = info.parsed_addresses()
            if not addresses:
                return
            clean_name = name.removesuffix(f".{type_}").strip(".")
            self.devices[name] = DiscoveredDevice(clean_name, addresses[0], info.port)

        def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            self.add_service(zc, type_, name)

        def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            self.devices.pop(name, None)

    zeroconf = Zeroconf()
    listener = Listener()
    try:
        ServiceBrowser(zeroconf, MDNS_SERVICE, listener)
        time.sleep(DISCOVERY_SECONDS)
        return sorted(listener.devices.values(), key=lambda device: device.name.lower())
    finally:
        zeroconf.close()


def _maybe_update_env_host(host: str) -> None:
    if not ENV_FILE.exists():
        print(f"No .env found. Add ANDROID_TV_HOST={host} before running the bot.")
        return

    answer = input(f"Update .env with ANDROID_TV_HOST={host}? [Y/n]: ").strip().lower()
    if answer not in {"", "y", "yes"}:
        return

    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    updated = False
    next_lines: list[str] = []
    for line in lines:
        if line.strip().startswith("ANDROID_TV_HOST="):
            next_lines.append(f"ANDROID_TV_HOST={host}")
            updated = True
        else:
            next_lines.append(line)

    if not updated:
        next_lines.append(f"ANDROID_TV_HOST={host}")

    ENV_FILE.write_text("\n".join(next_lines) + "\n", encoding="utf-8")
    print(".env updated.")


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return ROOT / path


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
