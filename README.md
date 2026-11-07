# snowsky-disc-firmware-archive

Firmware archival for the SNOWSKY DISC DAP, using the vendor OTA service at
`https://discpick.fiio.net`. Firmware contents are untrusted data: no downloaded
binary or script is executed. Hardware installation has **not** been tested.

## Discovery and publication

`scripts/archive_ota.py` selects the highest numeric `vNNN` found in published
GitHub **release names**, not tag ordering or GitHub's “latest” designation.
Draft releases do not advance this baseline. The vendor's `ota_patch_user.json`
must contain exactly one mapping whose `last_version` equals that baseline.
Its `new_version`, recovery version and patch URL are checked against
`ota_config.in`. Main OS and recovery files come from their respective
`manifest.sha256` lists, together with completion markers and signature files.
For example, numeric firmware `257` maps to tag and rootfs branch `2.57`.
Release names use `{tag} ({build_date}_v{version})`, for example
`2.57 (20260909_v257)`. Its two assets are
`SNOWSKY_DISC_update_20260909_v257.zip` and
`SNOWSKY_DISC_update_20260909_v257_decrypted.zip`.

An available update produces an original encrypted ZIP, a decrypted ZIP and an
unpacked main OS rootfs. Both ZIPs are validated before publication. The build
date is UTC `YYYYMMDD` from the main SquashFS superblock's creation timestamp
(`mkfs_time`), **not** the archive runner's current date.
`etc/product_version/version.in` supplies the product, main OS and recovery
version metadata used for cross-checks; it does not contain the build date.

The version branch contains the extracted root filesystem; files at least
100 MiB use Git LFS. Git cannot faithfully preserve every filesystem feature
(such as ownership, special files and empty directories); release notes record
these omissions. The ZIP's SquashFS image remains the filesystem reference.
Branch and tag can have the same name, so publication explicitly addresses
`refs/heads/...`. Publication stages release assets in a draft before making
the release public. A retry resumes only an automation-marked draft matching
the same title, version and rootfs digest. Published or unrelated releases
are never overwritten. An existing matching rootfs branch is reused; a
different tree fails rather than being replaced.

If no newer update exists, the command prints only its no-update JSON result
and exits without publication. Successful builds print a JSON report containing
`version`, `tag`, `build_date`, absolute `original_zip`, `decrypted_zip` and
`rootfs_dir` paths, `rootfs_sha256`, and `source_url`. No-update output has
exactly the `current_version` and `update_available` fields.

## Archive format and trust

The original ZIP preserves the vendor payload bytes and directory layout,
without an enclosing archive-name directory:

```text
ota_config.in
md5_file_info.txt
main_os/ota_v<main-version>/
    ota_v<main-version>.ok
    manifest.sha256
    manifest.sha256.sig
    ota_update.in.enc
    <encrypted image chunks and SHA-256 metadata listed by the manifest>
recovery/ota_v<recovery-version>/
    ota_v<recovery-version>.ok
    manifest.sha256
    manifest.sha256.sig
    ota_update.in.enc
    <encrypted image chunks and SHA-256 metadata listed by the manifest>
```

`md5_file_info.txt` records archive-file MD5 checksums for the vendor-compatible
layout. MD5 is compatibility metadata, not a security guarantee. Every payload
file is checked against the vendor SHA-256 manifest. The manifest's own
self-entry is retained byte-for-byte but excluded from self-hash validation
(the vendor self-check is stale/impossible). Signature files are preserved
unchanged; this pipeline has no independent vendor signature trust anchor and
does **not** authenticate those signatures. Checksums detect corruption and
internal inconsistencies, not a compromised vendor server.

Encrypted objects use the OpenSSL `Salted__` envelope and AES-256-CBC with
PKCS#7 padding. The fixed vendor password is `fo123`; PBKDF2-HMAC-SHA256 with
10,000 iterations derives 48 bytes (32-byte key followed by 16-byte IV) using
the envelope's eight-byte salt. This published password provides no secrecy.
Image chunks are decrypted and concatenated in numeric order, with manifest
size/hash checks. The decrypted ZIP has exactly these root-level main OS files:

```text
ota_update.in
rootfs.squashfs
xImage
```

Recovery is separately decrypted and unsquashed for verification, not included
in the decrypted ZIP. This is an analysis artifact, not a claim of
device-installable firmware.

## Local use

Use Python 3.12, `unsquashfs` (Ubuntu package `squashfs-tools`), Git, Git LFS
and the GitHub CLI (`gh`). Authenticate `gh` or provide `GH_TOKEN`; normal
publication requires repository contents write access.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --requirement requirements.txt
python scripts/archive_ota.py --repo linuxct/snowsky-disc-firmware-archive
```

For a safe historical discovery/build from v240, with persistent local outputs:

```bash
python scripts/archive_ota.py --repo linuxct/snowsky-disc-firmware-archive \
  --current-version 240 --build-only --output ./output
```

`--build-only` never mutates the remote repository. `--current-version` is
allowed only with `--build-only`. `--output` must name a new directory.
Without `--output`, build files live in a temporary directory that is removed
when the command exits; paths in the report then describe files that no longer
exist. An explicit output directory retains the archives, rootfs and
`build.json` report for inspection.

## Automation

The workflow runs once daily at **04:17 UTC** and can also be started manually.
There are no push or pull-request triggers. Publication runs are serialized
without cancelling a run already in progress, with a 45-minute timeout and only
`contents: write` permission. Actions are pinned to commit SHAs.

Manual runs offer `build_only` (default false) and `current_version` (default
empty). A current-version override requires `build_only=true` to prevent
accidental historical publication. Inputs pass through environment variables
and a Bash argument array, never executable shell interpolation. Build-only
workflow outputs are temporary; no artifact upload is performed.

GitHub schedules can be delayed or skipped under load and are not an exact-time
service. Scheduled workflows in public repositories can be automatically
disabled after 60 days without repository activity; monitor Actions and
re-enable the schedule when necessary.
