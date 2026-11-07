#!/usr/bin/env python3
"""Reconstruct and archive new SNOWSKY DISC OTA releases; never execute firmware."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile

from check_snowsky_ota import (
    DEFAULT_OTA_SITE, MANIFEST_LINE, OtaCheckError, OtaPlan, check_for_update,
    parse_manifest, parse_ota_config,
)
from fiio_extract_rootfs import (
    ExtractionError, MAX_ROOTFS_SIZE, OTA_KEY, SQUASH_MAGIC,
    find_rootfs_chunks, openssl_aes_decrypt, validate_image_name,
)
from publish_ota import PublishError, publish

MAX_FILE_BYTES = 16 << 20
RELEASE_NAME = re.compile(r"(\d+)\.(\d{2}) \((\d{8})_v(\d+)\)(?: \(Beta\))?")


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True)


def latest_version(repo: str) -> int:
    pages = json.loads(run("gh", "api", "--paginate", "--slurp", f"repos/{repo}/releases"))
    versions = []
    for release in (release for page in pages for release in page):
        if release["draft"]:
            continue
        match = RELEASE_NAME.fullmatch(release.get("name") or "")
        if not match:
            continue
        major, minor, _, numeric = match.groups()
        version = int(numeric)
        if int(major) * 100 + int(minor) != version:
            raise OtaCheckError(f"inconsistent release name: {release['name']}")
        versions.append(version)
    if not versions:
        raise OtaCheckError("no published firmware release has the expected name format")
    return max(versions)


def download(url: str, destination: Path) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "discpick.fiio.net":
        raise OtaCheckError(f"unexpected OTA download origin: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "SNOWSKY-DISC-OTA-Archive/1.0"})
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=60) as response:
        final = urllib.parse.urlsplit(response.url)
        if final.scheme != "https" or final.hostname != parsed.hostname:
            raise OtaCheckError(f"unexpected OTA redirect: {response.url}")
        with destination.open("xb") as out:
            size = 0
            while data := response.read(1 << 20):
                size += len(data)
                if size > MAX_FILE_BYTES:
                    raise OtaCheckError(f"OTA file exceeds 16 MiB: {url}")
                out.write(data)


def checksum(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm, usedforsecurity=False)
    with path.open("rb") as source:
        while data := source.read(1 << 20):
            digest.update(data)
    return digest.hexdigest()


def download_component(base_url: str, staging: Path, component: str, version: int) -> Path:
    relative = f"{component}/ota_v{version}"
    directory = staging / relative
    manifest = directory / "manifest.sha256"
    download(f"{base_url}/{relative}/manifest.sha256", manifest)
    text = manifest.read_text(encoding="utf-8")
    names = parse_manifest(text, f"{base_url}/{relative}/manifest.sha256")
    if len(names) > 1024 or len(names) != len(set(names)):
        raise OtaCheckError(f"invalid manifest entry count or duplicate entries: {relative}")
    for name in names:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise OtaCheckError(f"unsafe OTA filename: {name!r}")
    required = {f"ota_v{version}.ok", "manifest.sha256.sig", "ota_update.in.enc"}
    filenames = sorted((set(names) | required) - {"manifest.sha256"})

    def fetch(name: str) -> None:
        download(f"{base_url}/{relative}/{name}", directory / name)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(fetch, filenames))
    for line in text.splitlines():
        if not line.strip():
            continue
        match = MANIFEST_LINE.fullmatch(line.strip())
        assert match is not None
        name, expected = match.groups()
        # Vendor manifests contain a stale self-hash from before the final write.
        # Preserve it verbatim for the device; do not pretend it can verify itself.
        if name == "manifest.sha256":
            continue
        if checksum(directory / name) != expected.lower():
            raise OtaCheckError(f"encrypted file SHA-256 mismatch: {relative}/{name}")
    log(f"Verified {relative}: {len(filenames)} downloaded files plus manifest")
    return directory


def image_manifest(text: str, version: int) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    header: dict[str, str] = {}
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        if not raw.strip():
            continue
        if "=" not in raw:
            raise OtaCheckError(f"invalid decrypted OTA manifest line: {raw!r}")
        key, value = (part.strip() for part in raw.split("=", 1))
        if key == "img_type":
            current = {key: value}
            images.append(current)
        else:
            fields = header if current is None else current
            if key in fields:
                raise OtaCheckError(f"duplicate decrypted manifest field: {key}")
            fields[key] = value
    if header.get("ota_version") != str(version):
        raise OtaCheckError("decrypted OTA manifest version does not match discovery")
    kinds = set()
    names = set()
    for image in images:
        if set(image) != {"img_type", "img_name", "img_size", "img_sha256"}:
            raise OtaCheckError("incomplete or unsupported OTA image manifest")
        validate_image_name(image["img_name"])
        if image["img_type"] in kinds or image["img_name"] in names:
            raise OtaCheckError("duplicate OTA image type or filename")
        kinds.add(image["img_type"])
        names.add(image["img_name"])
        if image["img_name"] in {"ota_update.in", "ota_update.in.enc"}:
            raise OtaCheckError("OTA image name collides with manifest")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", image["img_sha256"]):
            raise OtaCheckError("invalid OTA image SHA-256")
        if not image["img_size"].isdigit() or not 0 < int(image["img_size"]) <= MAX_ROOTFS_SIZE:
            raise OtaCheckError("OTA image size is invalid or exceeds 256 MiB")
    if not {"kernel", "rootfs"} <= kinds:
        raise OtaCheckError("OTA requires both kernel and rootfs images")
    return images


def decrypt_component(directory: Path, output: Path, version: int) -> Path:
    output.mkdir(parents=True)
    manifest = openssl_aes_decrypt((directory / "ota_update.in.enc").read_bytes(), OTA_KEY)
    images = image_manifest(manifest.decode("utf-8"), version)
    (output / "ota_update.in").write_bytes(manifest)
    rootfs = None
    for image in images:
        name = image["img_name"]
        chunks = find_rootfs_chunks(directory, name)
        list_path = directory / f"ota_sha256_{name}.{image['img_sha256']}.enc"
        chunk_hashes = openssl_aes_decrypt(list_path.read_bytes(), OTA_KEY).decode("ascii").splitlines()
        if len(chunk_hashes) != len(chunks):
            raise OtaCheckError(f"chunk checksum count mismatch: {name}")
        destination = output / name
        with destination.open("xb") as out:
            for chunk, expected in zip(chunks, chunk_hashes, strict=True):
                data = openssl_aes_decrypt(chunk.read_bytes(), OTA_KEY)
                if hashlib.sha256(data).hexdigest() != expected.strip():
                    raise OtaCheckError(f"decrypted chunk SHA-256 mismatch: {chunk.name}")
                out.write(data)
        if destination.stat().st_size != int(image["img_size"]):
            raise OtaCheckError(f"assembled image size mismatch: {name}")
        if checksum(destination) != image["img_sha256"].lower():
            raise OtaCheckError(f"assembled image SHA-256 mismatch: {name}")
        if image["img_type"] == "rootfs":
            with destination.open("rb") as source:
                if source.read(4) != SQUASH_MAGIC:
                    raise OtaCheckError("rootfs is not SquashFS")
            rootfs = destination
        log(f"Decrypted and verified {directory.parent.name} {name}")
    assert rootfs is not None
    # Check the SquashFS metadata before full extraction in build_firmware().
    subprocess.run(["unsquashfs", "-s", str(rootfs)], check=True, stdout=sys.stderr)
    return rootfs


def make_zip(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(source.rglob("*")):
            name = path.relative_to(source).as_posix()
            if path.is_dir():
                info = zipfile.ZipInfo(f"{name}/", date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = (0o40755 << 16) | 0x10
                archive.writestr(info, b"")
                continue
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            with path.open("rb") as src, archive.open(info, "w") as dst:
                shutil.copyfileobj(src, dst, length=1 << 20)
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise OtaCheckError(f"ZIP integrity check failed: {destination}")


def build_firmware(plan: OtaPlan, output: Path) -> dict:
    release = plan.release
    staging = output / "firmware"
    staging.mkdir(parents=True)
    download(f"{release.patch_url}/ota_config.in", staging / "ota_config.in")
    config = parse_ota_config((staging / "ota_config.in").read_text())
    if config != plan.ota_config:
        raise OtaCheckError("OTA configuration changed while building")
    main_dir = download_component(release.patch_url, staging, "main_os", release.new_version)
    recovery_dir = download_component(release.patch_url, staging, "recovery", release.recovery_version)
    decrypted = output / "decrypted"
    rootfs = decrypt_component(main_dir, decrypted, release.new_version)
    recovery_rootfs = decrypt_component(recovery_dir, output / "recovery-decrypted", release.recovery_version)
    # Extract both images fully to force decompression and catch damaged data blocks.
    rootfs_dir = output / "rootfs"
    for image, destination in ((rootfs, rootfs_dir), (recovery_rootfs, output / "recovery-rootfs")):
        subprocess.run([
            "unsquashfs", "-processors", "2", "-no-progress", "-no-xattrs", "-strict-errors",
            "-d", str(destination), str(image),
        ], check=True, stdout=sys.stderr)
    metadata = run("unsquashfs", "-cat", str(rootfs), "etc/product_version/version.in")
    device = parse_ota_config(metadata)
    if (device.get("PRODUCT"), device.get("MAIN_OS_VER"), device.get("RECOVERY_OS_VER")) != (
        "SNOWSKY_DISC", str(release.new_version), str(release.recovery_version),
    ):
        raise OtaCheckError("rootfs device/version metadata does not match selected OTA")
    with rootfs.open("rb") as source:
        header = source.read(12)
    timestamp = struct.unpack_from("<I", header, 8)[0]
    build_date = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y%m%d")
    md5_lines = [
        f"{checksum(path, 'md5')}  {path.relative_to(staging).as_posix()}\n"
        for path in sorted(staging.rglob("*")) if path.is_file()
    ]
    (staging / "md5_file_info.txt").write_text("".join(md5_lines), encoding="ascii")
    stem = f"SNOWSKY_DISC_update_{build_date}_v{release.new_version}"
    original_zip = output / f"{stem}.zip"
    decrypted_zip = output / f"{stem}_decrypted.zip"
    make_zip(staging, original_zip)
    make_zip(decrypted, decrypted_zip)
    tag = f"{release.new_version // 100}.{release.new_version % 100:02d}"
    return {
        "version": release.new_version, "tag": tag, "build_date": build_date,
        "original_zip": str(original_zip), "decrypted_zip": str(decrypted_zip),
        "rootfs_dir": str(rootfs_dir), "rootfs_sha256": checksum(rootfs),
        "source_url": release.patch_url,
    }


def execute(args: argparse.Namespace, output: Path) -> None:
    current = args.current_version if args.current_version is not None else latest_version(args.repo)
    log(f"Checking OTA from known firmware v{current}")
    plan = check_for_update(current, DEFAULT_OTA_SITE)
    if plan.release.new_version <= current:
        print(json.dumps({"current_version": current, "update_available": False}, indent=2))
        return
    build = build_firmware(plan, output)
    if not args.build_only:
        publish(build, args.repo)
    report = {"current_version": current, "update_available": True, "published": not args.build_only, **build}
    (output / "build.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "linuxct/snowsky-disc-firmware-archive"))
    parser.add_argument("--current-version", type=int, help="override known version (requires --build-only)")
    parser.add_argument("--build-only", action="store_true", help="validate/build without changing GitHub")
    parser.add_argument("--output", type=Path, help="retain generated files in a new output directory")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        parser.error("--repo must be owner/name")
    if args.current_version is not None and (not args.build_only or args.current_version < 0):
        parser.error("--current-version requires --build-only and a nonnegative version")
    try:
        if args.output:
            output = args.output.resolve()
            output.mkdir(parents=True, exist_ok=False)
            execute(args, output)
        else:
            with tempfile.TemporaryDirectory(prefix="snowsky-ota-") as work:
                execute(args, Path(work))
    except (OtaCheckError, ExtractionError, PublishError, OSError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
        log(f"error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
