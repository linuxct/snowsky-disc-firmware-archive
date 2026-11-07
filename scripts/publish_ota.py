#!/usr/bin/env python3
"""Publish validated OTA artifacts without checking out or executing firmware."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote


AUTOMATION_MARKER = "<!-- snowsky-disc-ota-automation:v1 -->"
LFS_THRESHOLD = 100 * 1024 * 1024
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"


class PublishError(RuntimeError):
    """Publication stopped without replacing an existing artifact."""


class _Publisher:
    def __init__(self, directory: Path, repo: str):
        self.directory = directory
        self.gitdir = directory / "rootfs.git"
        self.repo = repo
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_INDEX_FILE": str(directory / "index"),
            "GIT_AUTHOR_NAME": BOT_NAME,
            "GIT_AUTHOR_EMAIL": BOT_EMAIL,
            "GIT_COMMITTER_NAME": BOT_NAME,
            "GIT_COMMITTER_EMAIL": BOT_EMAIL,
        })
        hooks = directory / "empty-hooks"
        hooks.mkdir()
        template = directory / "empty-template"
        template.mkdir()
        self.run(["git", "init", "--bare", f"--template={template}", str(self.gitdir)])
        self.git("config", "core.hooksPath", str(hooks))
        self.git("config", "core.autocrlf", "false")
        self.git("config", "core.attributesFile", os.devnull)
        self.git("config", "core.protectHFS", "true")
        self.git("config", "core.protectNTFS", "true")
        self.git("config", "credential.helper", "")
        if self.env.get("GH_TOKEN"):
            askpass = directory / "askpass"
            askpass.write_text(
                '#!/bin/sh\ncase "$1" in\n'
                '  *[Uu]sername*) printf "%s\\n" "x-access-token" ;;\n'
                '  *) printf "%s\\n" "$GH_TOKEN" ;;\nesac\n',
                encoding="utf-8",
            )
            askpass.chmod(0o700)
            self.env["GIT_ASKPASS"] = str(askpass)
            remote = f"https://github.com/{repo}.git"
        else:
            remote = f"git@github.com:{repo}.git"
        self.git("remote", "add", "origin", remote)
        self.lfs_oids: set[str] = set()
        self.special_count = 0
        self.empty_count = 0

    def redact(self, text: str) -> str:
        for variable in ("GH_TOKEN", "GITHUB_TOKEN"):
            token = self.env.get(variable)
            if token:
                text = text.replace(token, "[redacted]")
        return text

    def run(self, args: list[str], *, data: bytes | None = None, stdin=None,
            check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            args, input=data, stdin=stdin, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=self.directory, env=self.env,
        )
        if check and result.returncode:
            detail = self.redact(result.stderr.decode("utf-8", "replace").strip())
            raise PublishError(f"{args[0]} operation failed: {detail}")
        return result

    def git(self, *args: str, **kwargs) -> subprocess.CompletedProcess:
        return self.run(["git", f"--git-dir={self.gitdir}", *args], **kwargs)

    def api(self, endpoint: str, *, method: str = "GET", payload=None,
            missing_ok: bool = False):
        args = ["gh", "api", "--include", "--method", method, endpoint]
        data = None
        if payload is not None:
            args.extend(["--input", "-"])
            data = json.dumps(payload).encode("utf-8")
        result = self.run(args, data=data, check=False)
        header, separator, body = result.stdout.partition(b"\r\n\r\n")
        if not separator:
            header, separator, body = result.stdout.partition(b"\n\n")
        status = re.match(rb"HTTP/\S+\s+(\d{3})\b", header)
        if status is None:
            detail = self.redact(result.stderr.decode("utf-8", "replace").strip())
            raise PublishError(f"GitHub API returned no HTTP status for {endpoint}: {detail}")
        code = int(status[1])
        if code == 404 and missing_ok:
            return None
        if result.returncode or not 200 <= code < 300:
            detail = self.redact(result.stderr.decode("utf-8", "replace").strip())
            raise PublishError(f"GitHub API {method} {endpoint} failed (HTTP {code}): {detail}")
        if not body.strip():
            return None
        try:
            return json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise PublishError(f"GitHub API returned invalid JSON for {endpoint}") from exc

    def release(self, tag: str):
        published = self.api(
            f"repos/{self.repo}/releases/tags/{quote(tag, safe='')}", missing_ok=True,
        )
        if published is not None:
            return published
        # The by-tag endpoint may omit drafts. Authenticated listing includes them.
        matches: list[dict] = []
        page = 1
        while True:
            batch = self.api(f"repos/{self.repo}/releases?per_page=100&page={page}")
            matches.extend(item for item in batch if item.get("tag_name") == tag)
            if len(batch) < 100:
                break
            page += 1
        if len(matches) > 1:
            raise PublishError("Multiple releases use this tag; refusing ambiguous draft recovery")
        return matches[0] if matches else None

    def refs(self, tag: str) -> dict[str, str]:
        result = self.git(
            "ls-remote", "origin", "refs/heads/main", f"refs/heads/{tag}",
            f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}",
        )
        return dict(line.split("\t", 1)[::-1]
                    for line in result.stdout.decode("ascii").splitlines())

    def blob(self, data: bytes) -> str:
        return self.git("hash-object", "-w", "--no-filters", "--stdin", data=data).stdout.decode("ascii").strip()

    @staticmethod
    def check_component(name: str) -> None:
        # Git's HFS protection also ignores several Unicode formatting characters.
        folded = "".join(char for char in name.casefold()
                         if char not in "\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u206a\u206b\u206c\u206d\u206e\u206f\ufeff").rstrip(" .")
        if (folded.startswith(".git") or folded == ".lfsconfig"
                or re.fullmatch(r"git~[0-9]+", folded) or ":" in name):
            raise PublishError(f"Unsafe firmware path component: {name!r}")

    def large_blob(self, source) -> str:
        objects = self.gitdir / "lfs" / "objects"
        objects.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        with tempfile.NamedTemporaryFile(dir=objects, delete=False) as destination:
            temporary = Path(destination.name)
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                destination.write(chunk)
                size += len(chunk)
        oid = digest.hexdigest()
        target = objects / oid[:2] / oid[2:4] / oid
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            temporary.unlink()
        else:
            temporary.rename(target)
        self.lfs_oids.add(oid)
        return self.blob(
            f"version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {size}\n".encode("ascii")
        )

    @staticmethod
    def attribute(path: bytes) -> bytes:
        # First quote glob metacharacters, then quote the Git C-style string.
        pattern = b"/" + b"".join(b"\\" + bytes([char]) if char in b"\\*?[]" else bytes([char])
                                  for char in path)
        quoted = bytearray(b'"')
        for char in pattern:
            if char in (34, 92):
                quoted.extend(b"\\" + bytes([char]))
            elif char < 32 or char >= 127:
                quoted.extend(f"\\{char:03o}".encode("ascii"))
            else:
                quoted.append(char)
        return bytes(quoted) + b'" filter=lfs diff=lfs merge=lfs -text\n'

    def tree(self, root: Path) -> str:
        entries: list[bytes] = []
        attributes: list[bytes] = []

        def add(mode: str, oid: str, path: bytes) -> None:
            entries.append(mode.encode("ascii") + b" " + oid.encode("ascii") + b"\t" + path + b"\0")

        def visit(directory_fd: int, prefix: bytes) -> None:
            with os.scandir(directory_fd) as iterator:
                children = sorted(iterator, key=lambda entry: os.fsencode(entry.name))
            if not children and prefix:
                self.empty_count += 1
            for entry in children:
                self.check_component(entry.name)
                name = os.fsencode(entry.name)
                path = prefix + name
                metadata = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
                if stat.S_ISLNK(metadata.st_mode):
                    target = os.fsencode(os.readlink(entry.name, dir_fd=directory_fd))
                    add("120000", self.blob(target), path)
                elif stat.S_ISDIR(metadata.st_mode):
                    child_fd = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=directory_fd)
                    try:
                        visit(child_fd, path + b"/")
                    finally:
                        os.close(child_fd)
                elif stat.S_ISREG(metadata.st_mode):
                    descriptor = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
                    with os.fdopen(descriptor, "rb") as source:
                        opened = os.fstat(source.fileno())
                        if (not stat.S_ISREG(opened.st_mode)
                                or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)):
                            raise PublishError(f"Firmware file changed during archival: {path!r}")
                        if opened.st_size >= LFS_THRESHOLD:
                            oid = self.large_blob(source)
                            attributes.append(self.attribute(path))
                        else:
                            oid = self.git("hash-object", "-w", "--no-filters", "--stdin", stdin=source).stdout.decode("ascii").strip()
                        after = os.fstat(source.fileno())
                        if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
                                opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns):
                            raise PublishError(f"Firmware file changed during archival: {path!r}")
                    mode = "100755" if opened.st_mode & 0o111 else "100644"
                    add(mode, oid, path)
                else:
                    self.special_count += 1

        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            visit(descriptor, b"")
        finally:
            os.close(descriptor)
        if attributes:
            add("100644", self.blob(b"".join(attributes)), b".gitattributes")
        self.git("read-tree", "--empty")
        self.git("update-index", "-z", "--index-info", data=b"".join(entries))
        return self.git("write-tree").stdout.decode("ascii").strip()

    def check_branch(self, commit: str | None, tree: str) -> None:
        if commit is None:
            return
        info = self.api(f"repos/{self.repo}/git/commits/{commit}")
        if info["tree"]["sha"] != tree:
            raise PublishError("Existing firmware branch has a different tree; refusing to replace it")

    def asset_digest(self, asset: dict) -> str:
        digest = asset.get("digest")
        if isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            return digest[7:]
        args = ["gh", "api", f"repos/{self.repo}/releases/assets/{asset['id']}",
                "--header", "Accept: application/octet-stream"]
        digestor = hashlib.sha256()
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors,
                                       cwd=self.directory, env=self.env)
            assert process.stdout is not None
            with process.stdout:
                while chunk := process.stdout.read(1024 * 1024):
                    digestor.update(chunk)
            if process.wait():
                errors.seek(0)
                detail = self.redact(errors.read().decode("utf-8", "replace"))
                raise PublishError(f"Failed to verify existing release asset: {detail}")
        return digestor.hexdigest()

    def assets(self, release_id: int) -> list[dict]:
        assets: list[dict] = []
        page = 1
        while True:
            batch = self.api(f"repos/{self.repo}/releases/{release_id}/assets?per_page=100&page={page}")
            assets.extend(batch)
            if len(batch) < 100:
                return assets
            page += 1

    def check_draft(self, release_id: int, tag: str, marker: str, target: str) -> None:
        current = self.api(f"repos/{self.repo}/releases/{release_id}")
        body = current.get("body") or ""
        if (not current.get("draft") or AUTOMATION_MARKER not in body or marker not in body
                or current.get("target_commitish") != target or current.get("tag_name") != tag):
            raise PublishError("Draft release changed; refusing to modify it")

    def ensure_assets(self, release_id: int, tag: str, assets: list[tuple[Path, int, str]],
                      marker: str, target: str) -> None:
        existing = {asset["name"]: asset for asset in self.assets(release_id)}
        for path, size, digest in assets:
            asset = existing.get(path.name)
            if asset is None:
                self.check_draft(release_id, tag, marker, target)
                self.run(["gh", "release", "upload", tag, str(path), "--repo", self.repo])
                matching = [item for item in self.assets(release_id) if item["name"] == path.name]
                if len(matching) != 1:
                    raise PublishError(f"Uploaded asset not found uniquely: {path.name}")
                asset = matching[0]
            if (asset.get("state") != "uploaded" or asset.get("size") != size
                    or self.asset_digest(asset) != digest):
                raise PublishError(f"Existing asset differs or is incomplete; refusing to overwrite {path.name}")


def _asset(path_value: str) -> tuple[Path, int, str]:
    path = Path(path_value).absolute()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise PublishError(f"Release asset is not a regular file: {path}")
        digest = hashlib.file_digest(source, "sha256").hexdigest()
        after = os.fstat(source.fileno())
        if (metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise PublishError(f"Release asset changed while hashing: {path}")
    return path, metadata.st_size, digest


def publish(build: dict, repo: str) -> None:
    """Create or resume our draft, preserving all unrelated remote artifacts."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise PublishError("Expected a GitHub owner/repository name")
    tag = build["tag"]
    version = build["version"]
    date = build["build_date"]
    rootfs_digest = build["rootfs_sha256"]
    if (not isinstance(tag, str) or not re.fullmatch(r"[0-9]+\.[0-9]+", tag)
            or not isinstance(version, int) or isinstance(version, bool) or version < 0
            or not isinstance(date, str) or not re.fullmatch(r"[0-9]{8}", date)
            or not isinstance(rootfs_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", rootfs_digest)):
        raise PublishError("Invalid build publication metadata")
    assets = [_asset(build["original_zip"]), _asset(build["decrypted_zip"])]
    if assets[0][0].name == assets[1][0].name:
        raise PublishError("Original and decrypted ZIP assets must have distinct names")
    build_marker = f"<!-- ota-build:v{version}:{rootfs_digest} -->"
    title = f"{tag} ({date}_v{version})"
    with tempfile.TemporaryDirectory(prefix="snowsky-publish-") as temporary:
        publisher = _Publisher(Path(temporary), repo)
        release = publisher.release(tag)
        if release is not None:
            body = release.get("body") or ""
            if (not release.get("draft") or AUTOMATION_MARKER not in body
                    or build_marker not in body or release.get("name") != title):
                raise PublishError("Existing release is published, unrelated, or belongs to a different build")
        tree = publisher.tree(Path(build["rootfs_dir"]).absolute())
        refs = publisher.refs(tag)
        main = refs.get("refs/heads/main")
        branch_ref = f"refs/heads/{tag}"
        tag_ref = f"refs/tags/{tag}"
        if main is None:
            raise PublishError("Remote main branch does not exist")
        publisher.check_branch(refs.get(branch_ref), tree)
        if release is None and tag_ref in refs:
            raise PublishError("An existing tag has no automation draft; refusing to reuse it")
        target = release.get("target_commitish") if release is not None else main
        if not isinstance(target, str) or not re.fullmatch(r"[0-9a-f]{40}", target):
            raise PublishError("Automation draft does not have an immutable main commit target")
        publisher.git("fetch", "--no-tags", "origin", "refs/heads/main:refs/remotes/origin/main")
        ancestry = publisher.git("merge-base", "--is-ancestor", target,
                                 "refs/remotes/origin/main", check=False)
        if ancestry.returncode:
            raise PublishError("Release tag target is not an ancestor of remote main")
        tag_target = refs.get(tag_ref + "^{}", refs.get(tag_ref))
        if tag_target is not None and tag_target != target:
            raise PublishError("Existing release tag does not point to the draft's main commit")
        if release is None:
            body = (
                f"{AUTOMATION_MARKER}\n{build_marker}\n\n"
                f"Source URL: {build['source_url']}\n\n"
                f"Rootfs image SHA-256: `{rootfs_digest}`\n\n"
                f"Naming date: `{date}` (UTC YYYYMMDD from the main rootfs SquashFS creation "
                "`mkfs_time`, not upload time).\n\n"
                "The original encrypted OTA ZIP is reconstructed from the OTA downloads; its encrypted "
                "firmware payload bytes are preserved. The decrypted ZIP is for inspection only and is "
                "not flashable. Hardware installability has not been verified.\n\n"
                f"The `{tag}` branch contains the extracted root filesystem at the repository root, "
                "in an orphan commit. Regular-file bytes, Git's executable bit, and symlink targets "
                "are preserved without following firmware symlinks or normalizing line endings. "
                "Git cannot preserve ownership, timestamps, other permission bits, hard-link identity, "
                "special files, or empty directories. "
                f"Omitted: {publisher.special_count} special files and {publisher.empty_count} empty directories. "
                "Regular files of at least 100 MiB use Git LFS; when needed, the archive adds its own "
                "`.gitattributes`. Firmware is untrusted and no payload code was executed.\n"
            )
            release = publisher.api(f"repos/{repo}/releases", method="POST", payload={
                "tag_name": tag, "target_commitish": target, "name": title,
                "body": body, "draft": True,
            })
        release_id = release["id"]
        publisher.check_draft(release_id, tag, build_marker, target)
        publisher.ensure_assets(release_id, tag, assets, build_marker, target)
        if publisher.lfs_oids:
            publisher.git("lfs", "push", "--object-id", "--stdin", "origin",
                          data=("\n".join(sorted(publisher.lfs_oids)) + "\n").encode("ascii"))
        latest = publisher.refs(tag)
        publisher.check_branch(latest.get(branch_ref), tree)
        if branch_ref not in latest:
            commit = publisher.git("commit-tree", tree, "-m", f"Archive SNOWSKY DISC rootfs v{version}").stdout.decode("ascii").strip()
            publisher.git("push", "origin", f"{commit}:{branch_ref}")
        final_refs = publisher.refs(tag)
        if branch_ref not in final_refs:
            raise PublishError("Firmware branch disappeared before release publication")
        publisher.check_branch(final_refs[branch_ref], tree)
        tag_target = final_refs.get(tag_ref + "^{}", final_refs.get(tag_ref))
        if tag_target is not None and tag_target != target:
            raise PublishError("Release tag changed before publication")
        publisher.ensure_assets(release_id, tag, assets, build_marker, target)
        publisher.check_draft(release_id, tag, build_marker, target)
        publisher.api(f"repos/{repo}/releases/{release_id}", method="PATCH", payload={"draft": False})
