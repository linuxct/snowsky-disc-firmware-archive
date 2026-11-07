#!/usr/bin/env python3
"""
Standalone FiiO firmware rootfs extractor.

Original extraction logic adapted from:
    b0hemia/diskos
    https://github.com/b0hemia/diskos

In particular, this script follows the FiiO firmware extraction approach from:
    diskos_installer/imagebuild.py

The original project discovered/documented that FiiO's OTA format stores the
main OS manifest and rootfs chunks as OpenSSL-compatible AES-256-CBC blobs,
using PBKDF2 and the fixed password "fo123".

This standalone script:
  1. Safely extracts the supplied FiiO firmware ZIP to a temporary directory.
  2. Locates main_os/ota_v*/ota_update.in.enc.
  3. Decrypts the manifest using the FiiO OTA password.
  4. Finds the manifest block with img_type=rootfs.
  5. Locates the corresponding encrypted rootfs chunks.
  6. Decrypts and concatenates them in numeric order.
  7. Verifies the expected size and SquashFS magic.
  8. Writes the reconstructed stock rootfs image.

Dependency:
    pip install pycryptodome
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

try:
    from Crypto.Cipher import AES
except ImportError:
    print(
        "error: PyCryptodome is required.\n"
        "Install it with:\n"
        "    python -m pip install pycryptodome",
        file=sys.stderr,
    )
    raise SystemExit(2)


OTA_KEY = "fo123"
SQUASH_MAGIC = b"hsqs"

MAX_ZIP_ENTRIES = 20_000
MAX_UNCOMPRESSED_SIZE = 4 * (1 << 30)   # 4 GiB
MAX_ROOTFS_SIZE = 256 * (1 << 20)       # 256 MiB


class ExtractionError(RuntimeError):
    pass


def openssl_aes_decrypt(data: bytes, password: str) -> bytes:
    """
    Replicate:

        openssl enc -d -aes-256-cbc -pbkdf2 -k <password>

    Expected OpenSSL envelope:
        b"Salted__" + 8-byte salt + ciphertext

    KDF:
        PBKDF2-HMAC-SHA256
        iterations = 10000
        output      = 48 bytes
        key         = first 32 bytes
        IV          = next 16 bytes

    Padding:
        PKCS#7
    """
    if len(data) < 16 or data[:8] != b"Salted__":
        raise ExtractionError(
            "encrypted blob is missing the OpenSSL 'Salted__' header"
        )

    salt = data[8:16]
    ciphertext = data[16:]

    if not ciphertext or len(ciphertext) % 16 != 0:
        raise ExtractionError(
            "encrypted blob has an invalid ciphertext length"
        )

    key_iv = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        10_000,
        48,
    )
    key = key_iv[:32]
    iv = key_iv[32:48]

    plaintext = AES.new(key, AES.MODE_CBC, iv).decrypt(ciphertext)

    if not plaintext:
        raise ExtractionError("decryption produced empty plaintext")

    pad = plaintext[-1]
    if pad < 1 or pad > 16:
        raise ExtractionError(
            "invalid PKCS#7 padding after AES decryption "
            "(wrong key or unsupported firmware format?)"
        )

    if plaintext[-pad:] != bytes([pad]) * pad:
        raise ExtractionError(
            "invalid PKCS#7 padding after AES decryption "
            "(wrong key or unsupported firmware format?)"
        )

    return plaintext[:-pad]


def parse_rootfs_manifest(text: str) -> tuple[str | None, int | None]:
    """
    Find the manifest block whose img_type is rootfs, then return:
        (img_name, img_size)
    """
    in_rootfs = False
    name: str | None = None
    size: int | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if (
            line == "img_type=rootfs"
            or (
                line.startswith("img_type")
                and line.endswith("=rootfs")
            )
        ):
            in_rootfs = True
            continue

        if not in_rootfs:
            continue

        if line.startswith("img_name="):
            name = line[len("img_name="):].strip()

        elif line.startswith("img_size="):
            value = line[len("img_size="):].strip()

            if not value.isdigit():
                raise ExtractionError(
                    f"invalid rootfs img_size in OTA manifest: {value!r}"
                )

            size = int(value)

        if name is not None and size is not None:
            break

    return name, size


def safe_extract_zip(fw_zip: Path, destination: Path) -> None:
    """
    Extract ZIP contents while rejecting:
      - path traversal
      - absolute paths
      - suspicious entry counts
      - very large aggregate expansion
    """
    with zipfile.ZipFile(fw_zip) as zf:
        infos = zf.infolist()

        if len(infos) > MAX_ZIP_ENTRIES:
            raise ExtractionError(
                f"ZIP contains {len(infos)} entries; refusing as implausible"
            )

        total_uncompressed = sum(info.file_size for info in infos)
        if total_uncompressed > MAX_UNCOMPRESSED_SIZE:
            raise ExtractionError(
                "ZIP expands beyond 4 GiB; refusing"
            )

        destination_real = destination.resolve()

        for info in infos:
            name = info.filename.replace("\\", "/")

            if name.startswith("/") or ".." in name.split("/"):
                raise ExtractionError(
                    f"unsafe path in ZIP: {info.filename!r}"
                )

            target = (destination / info.filename).resolve()

            try:
                target.relative_to(destination_real)
            except ValueError:
                raise ExtractionError(
                    f"ZIP entry escapes extraction directory: "
                    f"{info.filename!r}"
                )

            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)

            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1 << 20)


def find_manifest(extracted_root: Path) -> Path:
    matches = sorted(
        extracted_root.glob("**/main_os/ota_v*/ota_update.in.enc")
    )

    if not matches:
        raise ExtractionError(
            "could not find main_os/ota_v*/ota_update.in.enc; "
            "this may not be a supported FiiO main-OS firmware ZIP"
        )

    if len(matches) > 1:
        raise ExtractionError(
            f"found {len(matches)} matching main_os OTA manifests; "
            "refusing ambiguous firmware"
        )

    return matches[0]


def validate_image_name(name: str) -> None:
    if not name:
        raise ExtractionError("OTA manifest contains an empty rootfs img_name")

    if Path(name).name != name or "/" in name or "\\" in name:
        raise ExtractionError(
            f"unsafe rootfs image name in OTA manifest: {name!r}"
        )

    if name in (".", ".."):
        raise ExtractionError(
            f"unsafe rootfs image name in OTA manifest: {name!r}"
        )


def find_rootfs_chunks(ota_dir: Path, image_name: str) -> list[Path]:
    """
    Match:
        <img_name>.<numeric-index>.<anything>.enc

    Example:
        rootfs.squashfs.0000.<hash>.enc
        rootfs.squashfs.0001.<hash>.enc
    """
    pattern = str(ota_dir / (glob.escape(image_name) + ".*.enc"))

    by_index: dict[int, Path] = {}

    for filename in glob.glob(pattern):
        path = Path(filename)
        basename = path.name

        rest = basename[len(image_name) + 1:]
        index_text = rest.split(".", 1)[0]

        if not index_text.isdigit():
            continue

        index = int(index_text)

        if index in by_index:
            raise ExtractionError(
                f"duplicate rootfs chunk index {index}"
            )

        by_index[index] = path

    if not by_index:
        raise ExtractionError(
            f"no encrypted chunks found for rootfs image {image_name!r}"
        )

    indices = sorted(by_index)

    expected = list(range(len(indices)))
    if indices != expected:
        raise ExtractionError(
            "rootfs chunk indices are not contiguous: "
            f"expected 0..{len(indices) - 1}, got {indices}"
        )

    return [by_index[i] for i in indices]


def extract_rootfs(
    firmware_zip: Path,
    output: Path,
    keep_workdir: bool = False,
) -> Path:
    if not firmware_zip.is_file():
        raise ExtractionError(
            f"firmware file does not exist: {firmware_zip}"
        )

    if not zipfile.is_zipfile(firmware_zip):
        raise ExtractionError(
            f"not a valid ZIP archive: {firmware_zip}"
        )

    temp_dir = Path(tempfile.mkdtemp(prefix="fiio-fw-extract-"))

    try:
        extracted = temp_dir / "fw_unzip"
        extracted.mkdir()

        print(f"[+] Unpacking firmware ZIP: {firmware_zip}")
        safe_extract_zip(firmware_zip, extracted)

        manifest_path = find_manifest(extracted)
        ota_dir = manifest_path.parent

        print(
            "[+] Found OTA manifest:",
            manifest_path.relative_to(extracted),
        )

        encrypted_manifest = manifest_path.read_bytes()
        manifest_text = openssl_aes_decrypt(
            encrypted_manifest,
            OTA_KEY,
        ).decode("utf-8", errors="ignore")

        image_name, expected_size = parse_rootfs_manifest(
            manifest_text
        )

        if image_name is None:
            raise ExtractionError(
                "OTA manifest does not contain an img_type=rootfs block "
                "with img_name"
            )

        validate_image_name(image_name)

        if expected_size is not None:
            if not (0 < expected_size <= MAX_ROOTFS_SIZE):
                raise ExtractionError(
                    f"implausible rootfs size in manifest: "
                    f"{expected_size} bytes"
                )

        print(f"[+] Rootfs image name: {image_name}")
        if expected_size is not None:
            print(f"[+] Expected size: {expected_size} bytes")

        chunks = find_rootfs_chunks(ota_dir, image_name)
        print(f"[+] Found {len(chunks)} encrypted rootfs chunks")

        output.parent.mkdir(parents=True, exist_ok=True)

        temp_output = temp_dir / "rootfs.assembled"

        with temp_output.open("wb") as out:
            for index, chunk_path in enumerate(chunks):
                print(
                    f"[+] Decrypting chunk {index + 1}/{len(chunks)}: "
                    f"{chunk_path.name}"
                )

                decrypted = openssl_aes_decrypt(
                    chunk_path.read_bytes(),
                    OTA_KEY,
                )
                out.write(decrypted)

        actual_size = temp_output.stat().st_size

        if expected_size is not None and actual_size != expected_size:
            raise ExtractionError(
                f"assembled rootfs size mismatch: "
                f"got {actual_size}, expected {expected_size}"
            )

        with temp_output.open("rb") as f:
            magic = f.read(4)

        if magic != SQUASH_MAGIC:
            raise ExtractionError(
                "assembled output is not a SquashFS image "
                f"(magic={magic!r}, expected={SQUASH_MAGIC!r})"
            )

        shutil.move(str(temp_output), str(output))

        sha256 = hashlib.sha256()
        with output.open("rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                sha256.update(chunk)

        print(f"[+] Extracted rootfs: {output}")
        print(f"[+] Size: {output.stat().st_size} bytes")
        print(f"[+] SHA-256: {sha256.hexdigest()}")

        return output

    finally:
        if keep_workdir:
            print(f"[+] Keeping temporary directory: {temp_dir}")
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract and reconstruct the stock rootfs SquashFS from a "
            "FiiO firmware ZIP."
        )
    )

    parser.add_argument(
        "firmware",
        type=Path,
        help="path to the official FiiO firmware ZIP",
    )

    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("stock_rootfs.squashfs"),
        help="output path (default: stock_rootfs.squashfs)",
    )

    parser.add_argument(
        "--keep-workdir",
        action="store_true",
        help="keep the temporary extracted firmware directory for inspection",
    )

    args = parser.parse_args()

    try:
        extract_rootfs(
            firmware_zip=args.firmware,
            output=args.output,
            keep_workdir=args.keep_workdir,
        )
        return 0

    except ExtractionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    except (OSError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
