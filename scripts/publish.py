#!/usr/bin/env python3
"""Publish a validated Wally ZIP and verify the registry's actual contents.

Wally 0.3.2 exits successfully even when its registry rejects a publish. This
script uses the same endpoint and headers, then reads the package back. Tokens
must come from `wally login`; ordinary GitHub PATs do not belong to Wally's OAuth
app and are not accepted by the public registry.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import tomllib
import urllib.error
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass


PACKAGE_NAME = "ewanophobia/libmp-mirror"
REGISTRY_API = "https://api.wally.run"
REGISTRY_INDEX = "https://github.com/UpliftGames/wally-index"
WALLY_VERSION = "0.3.2"
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024
MAX_EXTRACTED_BYTES = 64 * 1024 * 1024
ALLOWED_ROOT_FILES = {
    "wally.toml",
    "default.project.json",
    "libmp.luau",
    "LICENSE",
    "README.md",
    "upstream.json",
}
REQUIRED_FILES = {"wally.toml", "default.project.json", "libmp.luau", "LICENSE"}
VERSION_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


class PublishError(Exception):
    """A safe-to-display validation or publication error."""


class RegistryError(PublishError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class Package:
    archive: bytes
    name: str
    version: str
    module_sha256: str
    file_sha256: dict[str, str]


def validate_archive(data: bytes) -> Package:
    if len(data) > MAX_ARCHIVE_BYTES:
        raise PublishError("Wally archive exceeds the registry's 2 MiB limit.")

    files: dict[str, bytes] = {}
    seen: set[str] = set()
    extracted_size = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for entry in archive.infolist():
                name = entry.filename
                if name in seen:
                    raise PublishError(f"Duplicate archive entry: {name!r}.")
                seen.add(name)
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise PublishError(f"Archive contains a symlink: {name!r}.")
                if entry.is_dir():
                    if name != "LICENSES/":
                        raise PublishError(f"Unexpected archive directory: {name!r}.")
                    continue
                is_license = re.fullmatch(r"LICENSES/[A-Za-z0-9][A-Za-z0-9._-]*", name)
                if name not in ALLOWED_ROOT_FILES and not is_license:
                    raise PublishError(f"Unexpected archive file: {name!r}.")
                extracted_size += entry.file_size
                if extracted_size > MAX_EXTRACTED_BYTES:
                    raise PublishError("Archive expands beyond the 64 MiB validation limit.")
                files[name] = archive.read(entry)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError, zlib.error) as error:
        raise PublishError("Could not read the package ZIP archive.") from error

    missing = REQUIRED_FILES - files.keys()
    if missing:
        raise PublishError(f"Archive is missing required files: {', '.join(sorted(missing))}.")
    if not files["libmp.luau"].strip():
        raise PublishError("The libmp.luau module is empty.")

    try:
        manifest = tomllib.loads(files["wally.toml"].decode("utf-8"))
        package = manifest["package"]
        name = package["name"]
        version = package["version"]
        project = json.loads(files["default.project.json"])
    except (UnicodeError, ValueError, KeyError, TypeError) as error:
        raise PublishError("Archive contains an invalid manifest or Rojo project.") from error

    if name != PACKAGE_NAME:
        raise PublishError(f"Archive must contain package {PACKAGE_NAME}.")
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise PublishError("Archive package version must be a semantic version.")
    prerelease = version.partition("+")[0].partition("-")[2]
    if any(part.isdigit() and len(part) > 1 and part[0] == "0" for part in prerelease.split(".")):
        raise PublishError("Semantic version prerelease numbers cannot have leading zeros.")
    if package.get("realm") != "shared":
        raise PublishError("Archive must declare the shared Wally realm.")
    registry = package.get("registry")
    if not isinstance(registry, str) or registry.lower().rstrip("/") != REGISTRY_INDEX.lower():
        raise PublishError("Archive must use the public Wally registry.")
    if package.get("private", False) is not False:
        raise PublishError("A private package cannot be published.")
    if not isinstance(project, dict) or project.get("name") != "libmp-mirror" or project.get("tree") != {"$path": "libmp.luau"}:
        raise PublishError("Rojo project must map libmp-mirror directly to libmp.luau.")

    file_sha256 = {
        path: hashlib.sha256(content).hexdigest()
        for path, content in files.items()
        if path != "README.md"
    }
    return Package(data, name, version, file_sha256["libmp.luau"], file_sha256)


def request(method: str, url: str, *, data: bytes | None = None, token: str | None = None) -> tuple[int, bytes]:
    headers = {"Accept": "application/json", "Wally-Version": WALLY_VERSION, "User-Agent": "libmp-mirror"}
    if data is not None:
        headers["Content-Type"] = "application/zip"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read(MAX_ARCHIVE_BYTES + 1)
            if len(body) > MAX_ARCHIVE_BYTES:
                raise RegistryError("Registry response exceeds the 2 MiB package limit.")
            return response.status, body
    except urllib.error.HTTPError as error:
        # Do not print server response bodies or request headers: they may echo
        # credentials. Status codes are sufficient for safe diagnostics.
        with error:
            return error.code, b""
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RegistryError("Could not reach the Wally registry.", retryable=True) from error


def fetch_published(package: Package) -> Package | None:
    url = f"{REGISTRY_API}/v1/package-contents/{package.name}/{package.version}"
    status, data = request("GET", url)
    if status == 404:
        return None
    if status != 200:
        raise RegistryError(
            f"Registry package lookup failed with HTTP {status}.",
            retryable=status == 429 or status >= 500,
        )
    return validate_archive(data)


def verify_matches(expected: Package, published: Package) -> None:
    if (published.name, published.version) != (expected.name, expected.version):
        raise PublishError("Registry returned a package with a different name or version.")
    if published.module_sha256 != expected.module_sha256:
        raise PublishError(
            f"Published {expected.name}@{expected.version} contains different libmp.luau bytes; "
            "Wally versions are immutable."
        )
    if published.file_sha256 != expected.file_sha256:
        paths = expected.file_sha256.keys() | published.file_sha256.keys()
        changed = sorted(path for path in paths if expected.file_sha256.get(path) != published.file_sha256.get(path))
        raise PublishError(
            f"Published {expected.name}@{expected.version} differs in: {', '.join(changed)}. "
            "Wally versions are immutable; only README.md may differ."
        )


def publish_archive(data: bytes, token: str | None, *, check_only: bool = False) -> str:
    package = validate_archive(data)
    identity = f"{package.name}@{package.version}"
    existing = fetch_published(package)
    if existing is not None:
        verify_matches(package, existing)
        return f"Verified {identity}: already published with matching package files (README.md excluded)."
    if check_only:
        return f"Validated {identity}: not yet published."
    token = token.strip() if token else ""
    if not token:
        raise PublishError(
            f"{identity} is not published. Set the WALLY_AUTH_TOKEN repository secret "
            "to the OAuth token generated by `wally login`."
        )
    if "\r" in token or "\n" in token:
        raise PublishError("WALLY_AUTH_TOKEN contains invalid newline characters.")

    post_error: RegistryError | None = None
    try:
        status, _ = request("POST", f"{REGISTRY_API}/v1/publish", data=data, token=token)
        if not 200 <= status < 300:
            post_error = RegistryError(
                f"Wally publish failed with HTTP {status}.",
                retryable=status in (409, 429) or status >= 500,
            )
    except RegistryError as error:
        # A lost response can follow a successful write. Read back before
        # deciding whether this run failed; never blindly republish.
        post_error = error

    attempts = 6 if post_error is None or post_error.retryable else 1
    last_error: RegistryError | None = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(2 ** (attempt - 1))
        try:
            published = fetch_published(package)
        except RegistryError as error:
            last_error = error
            if not error.retryable:
                raise
            continue
        if published is not None:
            verify_matches(package, published)
            return f"Published and verified {identity} (libmp.luau SHA-256: {package.module_sha256})."

    if post_error is not None:
        raise post_error
    if last_error is not None:
        raise RegistryError("Wally accepted the publish, but registry readback could not be verified.") from last_error
    raise PublishError("Wally accepted the publish, but the package was still absent after verification retries.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True, help="ZIP produced by `wally package`.")
    parser.add_argument("--check-only", action="store_true", help="Validate and report registry state without publishing.")
    args = parser.parse_args()
    try:
        if args.archive.stat().st_size > MAX_ARCHIVE_BYTES:
            raise PublishError("Wally archive exceeds the registry's 2 MiB limit.")
        result = publish_archive(args.archive.read_bytes(), os.environ.get("WALLY_AUTH_TOKEN"), check_only=args.check_only)
    except (PublishError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
