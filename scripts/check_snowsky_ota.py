#!/usr/bin/env python3
"""Check the SNOWSKY DISC OTA service for a firmware update."""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

DEFAULT_CURRENT_VERSION = 257
DEFAULT_OTA_SITE = "https://discpick.fiio.net"
REQUEST_TIMEOUT_SECONDS = 15
MAX_RESPONSE_BYTES = 1 << 20


class OtaCheckError(RuntimeError):
    """Raised when the OTA response is unavailable or invalid."""


@dataclass(frozen=True)
class OtaRelease:
    last_version: int
    new_version: int
    recovery_version: int
    patch_url: str


@dataclass(frozen=True)
class OtaPlan:
    release: OtaRelease
    discovery_response: tuple[Any, ...]
    ota_config: dict[str, str] | None
    manifest_urls: tuple[str, ...]
    firmware_urls: tuple[str, ...]


def fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SNOWSKY-DISC-OTA-Checker/1.0"},
    )
    context = ssl.create_default_context()

    try:
        with urllib.request.urlopen(
            request,
            timeout=REQUEST_TIMEOUT_SECONDS,
            context=context,
        ) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OtaCheckError(f"failed to fetch {url}: {exc}") from exc

    if len(data) > MAX_RESPONSE_BYTES:
        raise OtaCheckError(f"response from {url} exceeds 1 MiB")

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OtaCheckError(f"response from {url} is not UTF-8") from exc


def parse_patch_list(text: str) -> list[Any]:
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # The production endpoint currently emits a trailing comma before ']'.
        # Accept only that common extension rather than using a permissive parser.
        without_trailing_commas = re.sub(r",\s*([}\]])", r"\1", text)
        try:
            result = json.loads(without_trailing_commas)
        except json.JSONDecodeError as exc:
            raise OtaCheckError(f"OTA patch response is invalid JSON: {exc}") from exc

    if not isinstance(result, list):
        raise OtaCheckError("OTA patch response must be a JSON array")
    return result


def require_int(entry: dict[str, Any], key: str) -> int:
    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise OtaCheckError(f"OTA entry field {key!r} must be an integer")
    return value


def parse_release(entry: Any) -> OtaRelease:
    if not isinstance(entry, dict):
        raise OtaCheckError("each OTA patch entry must be a JSON object")

    patch_url = entry.get("patch_url")
    if not isinstance(patch_url, str):
        raise OtaCheckError("OTA entry field 'patch_url' must be a string")

    parsed_url = urllib.parse.urlparse(patch_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise OtaCheckError(f"refusing non-HTTPS patch URL: {patch_url!r}")

    return OtaRelease(
        last_version=require_int(entry, "last_version"),
        new_version=require_int(entry, "new_version"),
        recovery_version=require_int(entry, "recovery"),
        patch_url=patch_url.rstrip("/"),
    )


def parse_ota_config(text: str) -> dict[str, str]:
    config: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise OtaCheckError(f"invalid ota_config.in line: {raw_line!r}")
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip()
    return config


MANIFEST_LINE = re.compile(
    r"^SHA256\(ota/([^)]+)\)=\s*([0-9a-fA-F]{64})$"
)


def parse_manifest(text: str, manifest_url: str) -> list[str]:
    filenames: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = MANIFEST_LINE.fullmatch(line)
        if match is None:
            raise OtaCheckError(
                f"invalid SHA-256 manifest line from {manifest_url}: {raw_line!r}"
            )
        filename = match.group(1)
        path = PurePosixPath(filename)
        if path.name != filename or filename in {".", ".."}:
            raise OtaCheckError(
                f"unsafe filename in SHA-256 manifest: {filename!r}"
            )
        filenames.append(filename)
    if not filenames:
        raise OtaCheckError(f"empty SHA-256 manifest: {manifest_url}")
    return filenames


def component_urls(base_url: str, component: str, version: int) -> list[str]:
    directory_url = f"{base_url}/{component}/ota_v{version}"
    manifest_url = f"{directory_url}/manifest.sha256"
    filenames = parse_manifest(fetch_text(manifest_url), manifest_url)

    ordered_names = [
        f"ota_v{version}.ok",
        "manifest.sha256",
        "manifest.sha256.sig",
        *filenames,
    ]
    unique_names = list(dict.fromkeys(ordered_names))
    return [f"{directory_url}/{name}" for name in unique_names]


def check_for_update(current_version: int, ota_site: str) -> OtaPlan:
    endpoint = f"{ota_site.rstrip('/')}/ota_patch_user.json"
    entries = parse_patch_list(fetch_text(endpoint))
    matching_entries = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("last_version") == current_version
    ]

    if not matching_entries:
        raise OtaCheckError(
            f"OTA service has no release mapping for firmware v{current_version}"
        )
    if len(matching_entries) != 1:
        raise OtaCheckError(
            f"OTA service returned multiple mappings for firmware v{current_version}"
        )

    selected_entry = matching_entries[0]
    release = parse_release(selected_entry)
    if release.new_version <= current_version:
        return OtaPlan(
            release=release,
            discovery_response=(selected_entry,),
            ota_config=None,
            manifest_urls=(),
            firmware_urls=(),
        )
    config_url = f"{release.patch_url}/ota_config.in"
    config = parse_ota_config(fetch_text(config_url))

    try:
        configured_version = int(config["current_version"])
        configured_recovery = int(config["recovery_version"])
    except (KeyError, ValueError) as exc:
        raise OtaCheckError(f"invalid version fields in {config_url}") from exc

    if configured_version != release.new_version:
        raise OtaCheckError(
            "OTA discovery and payload configuration disagree: "
            f"v{release.new_version} != v{configured_version}"
        )
    if configured_recovery != release.recovery_version:
        raise OtaCheckError(
            "OTA discovery and payload recovery versions disagree: "
            f"{release.recovery_version} != {configured_recovery}"
        )

    main_manifest_url = (
        f"{release.patch_url}/main_os/ota_v{configured_version}/manifest.sha256"
    )
    recovery_manifest_url = (
        f"{release.patch_url}/recovery/ota_v{configured_recovery}/manifest.sha256"
    )
    firmware_urls = [
        config_url,
        *component_urls(
            release.patch_url,
            "main_os",
            configured_version,
        ),
        *component_urls(
            release.patch_url,
            "recovery",
            configured_recovery,
        ),
    ]
    return OtaPlan(
        release=release,
        discovery_response=(selected_entry,),
        ota_config=config,
        manifest_urls=(main_manifest_url, recovery_manifest_url),
        firmware_urls=tuple(firmware_urls),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check the SNOWSKY DISC OTA service for an update."
    )
    parser.add_argument(
        "current_version",
        nargs="?",
        type=int,
        default=DEFAULT_CURRENT_VERSION,
        help=f"installed numeric firmware version (default: {DEFAULT_CURRENT_VERSION})",
    )
    parser.add_argument(
        "--ota-site",
        default=DEFAULT_OTA_SITE,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    if args.current_version < 0:
        parser.error("current_version must be non-negative")

    try:
        plan = check_for_update(args.current_version, args.ota_site)
    except OtaCheckError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    release = plan.release
    update_available = release.new_version > args.current_version
    result: dict[str, Any] = {
        "current_version": args.current_version,
        "update_available": update_available,
    }
    if update_available:
        result["discovery"] = {
            "url": f"{args.ota_site.rstrip('/')}/ota_patch_user.json",
            "response": list(plan.discovery_response),
        }
        result["ota_config"] = {
            "url": f"{release.patch_url}/ota_config.in",
            "values": plan.ota_config,
        }
        result["manifest_urls"] = list(plan.manifest_urls)
        result["firmware_file_urls"] = list(plan.firmware_urls)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
