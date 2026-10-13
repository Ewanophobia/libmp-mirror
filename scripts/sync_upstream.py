#!/usr/bin/env python3
"""Mirror the highest numbered stable Roblox/libmp release; standard library only."""

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tomllib
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "Roblox/libmp"
API = f"https://api.github.com/repos/{REPOSITORY}"
LICENSE_PATHS = {
    "LICENSES/libmp.txt": "LICENSE",
    "LICENSES/flatbuffers.txt": "src/dependencies/flatbuffers/LICENSE",
    "LICENSES/lz4.txt": "src/dependencies/lz4/LICENSE",
    "LICENSES/spider_runtime.txt": "src/dependencies/spider_runtime/LICENSE",
}
LIMIT = 64 * 1024 * 1024


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def fetch(url, *, github_api=False):
    headers = {"User-Agent": "ewanophobia-libmp-mirror"}
    if github_api:
        if not url.startswith(API + "/"):
            raise ValueError("Unexpected GitHub API URL")
        headers["Accept"] = "application/vnd.github+json"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=60) as response:
        data = response.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError(f"Download exceeds {LIMIT} bytes: {url}")
    return data


def api(path):
    return json.loads(fetch(f"{API}/{path}", github_api=True))


def parse_version(tag):
    match = re.fullmatch(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?", tag)
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def version_text(version):
    return ".".join(map(str, version))


def select_release(releases):
    candidates = [
        (parse_version(release["tag_name"]), release)
        for release in releases
        if not release["draft"] and not release["prerelease"]
        and parse_version(release["tag_name"]) is not None
    ]
    if not candidates:
        raise ValueError("No numbered stable upstream release found")
    candidates.sort(key=lambda item: (item[0], item[1]["published_at"]), reverse=True)
    return candidates[0][1]


def newest_release():
    releases = []
    page = 1
    while True:
        batch = api(f"releases?per_page=100&page={page}")
        releases.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return select_release(releases)


def resolve_commit(tag):
    obj = api(f"git/ref/tags/{quote(tag, safe='')}")["object"]
    for _ in range(8):
        if obj["type"] == "commit":
            if not re.fullmatch(r"[0-9a-f]{40}", obj["sha"]):
                raise ValueError("Invalid release commit")
            return obj["sha"]
        if obj["type"] != "tag":
            break
        obj = api(f"git/tags/{obj['sha']}")["object"]
    raise ValueError(f"Cannot resolve release tag {tag!r} to a commit")


def download_asset(asset):
    url = asset["browser_download_url"]
    if not url.startswith(f"https://github.com/{REPOSITORY}/releases/download/"):
        raise ValueError("Unexpected upstream asset URL")
    data = fetch(url)
    if len(data) != asset["size"]:
        raise ValueError(f"Asset size mismatch: {asset['name']}")
    digest = asset.get("digest")
    if digest and digest != "sha256:" + sha256(data):
        raise ValueError(f"Asset digest mismatch: {asset['name']}")
    return data


def unique(items, label, *, required=True):
    if len(items) > 1 or (required and not items):
        raise ValueError(f"Expected exactly one {label}; found {len(items)}")
    return items[0] if items else None


def read_member(archive, member):
    if member.file_size > LIMIT:
        raise ValueError("Uncompressed archive member too large")
    return archive.read(member)


def extract_bundle(data):
    """Read selected files only; never extract arbitrary ZIP paths to disk."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        module = unique([
            item for item in members if PurePosixPath(item.filename).name.lower() == "libmp.luau"
        ], "LibMP.luau in release archive")
        files = {"libmp.luau": read_member(archive, module)}
        for target, dependency in [
            ("LICENSES/libmp.txt", "libmp"), ("LICENSES/flatbuffers.txt", "flatbuffers"),
            ("LICENSES/lz4.txt", "lz4"), ("LICENSES/spider_runtime.txt", "spider_runtime"),
        ]:
            member = unique([
                item for item in members
                if item.filename.lower().endswith(f"/licenses/{dependency}/license")
                or item.filename.lower() == f"licenses/{dependency}/license"
            ], f"{dependency} license", required=False)
            if member:
                files[target] = read_member(archive, member)
        return files


def release_files(release, commit):
    assets = api(f"releases/{release['id']}/assets?per_page=100")
    direct = unique([
        item for item in assets if item["name"].lower() == "libmp.luau"
    ], "direct LibMP.luau asset", required=False)
    bundle = unique([
        item for item in assets
        if item["name"].lower().endswith(".zip")
        and "libmp" in item["name"].lower() and "luau" in item["name"].lower()
    ], "LibMP Luau ZIP", required=False)
    if not direct and not bundle:
        raise ValueError("Release has no LibMP.luau or LibMP Luau ZIP asset")
    files, provenance = {}, []
    for asset in [item for item in (bundle, direct) if item]:
        data = download_asset(asset)
        provenance.append({
            "id": asset["id"], "name": asset["name"], "url": asset["browser_download_url"],
            "sha256": sha256(data),
        })
        if asset is bundle:
            files = extract_bundle(data)
        else:
            if "libmp.luau" in files and files["libmp.luau"] != data:
                raise ValueError("Direct LibMP.luau differs from the release ZIP")
            files["libmp.luau"] = data
    for target, source in LICENSE_PATHS.items():
        if target not in files:
            files[target] = fetch(f"https://raw.githubusercontent.com/{REPOSITORY}/{commit}/{source}")
    for name, data in files.items():
        if not data.strip():
            raise ValueError(f"Empty upstream file: {name}")
        data.decode("utf-8")
    if "return" not in files["libmp.luau"].decode("utf-8"):
        raise ValueError("Downloaded Luau file is not a module")
    return files, provenance


def next_version(upstream_version, previous):
    base = parse_version(upstream_version)
    if previous is None:
        return version_text(base)
    old_source = parse_version(previous["upstream"]["tag"])
    if base < old_source:
        raise ValueError("Refusing to downgrade to an older upstream release")
    old_package = parse_version(previous["wally_version"])
    if base > old_package:
        return version_text(base)
    return version_text((old_package[0], old_package[1], old_package[2] + 1))


def sync(root=ROOT):
    release = newest_release()
    commit = resolve_commit(release["tag_name"])
    files, assets = release_files(release, commit)
    hashes = {name: sha256(data) for name, data in sorted(files.items())}
    metadata_path = root / "upstream.json"
    previous = json.loads(metadata_path.read_text()) if metadata_path.exists() else None
    if previous:
        if parse_version(release["tag_name"]) < parse_version(previous["upstream"]["tag"]):
            raise ValueError("Refusing to downgrade to an older upstream release")
        for name, digest in previous["files"].items():
            if sha256((root / name).read_bytes()) != digest:
                raise ValueError(f"Local mirrored file changed unexpectedly: {name}")
        manifest = tomllib.loads((root / "wally.toml").read_text())
        if manifest["package"]["version"] != previous["wally_version"]:
            raise ValueError("Manifest version disagrees with upstream.json")
        if previous["upstream"]["tag"] == release["tag_name"] and previous["files"] == hashes:
            print(f"Already current: upstream {release['tag_name']}, Wally {previous['wally_version']}")
            return False
    version = next_version(release["tag_name"], previous)
    metadata = {
        "schema_version": 1,
        "wally_version": version,
        "upstream": {
            "repository": REPOSITORY, "tag": release["tag_name"], "commit": commit,
            "release_id": release["id"], "release_url": release["html_url"],
            "published_at": release["published_at"], "assets": assets,
        },
        "files": hashes,
    }
    manifest_path = root / "wally.toml"
    manifest, count = re.subn(r'^version = "[^"]+"$', f'version = "{version}"', manifest_path.read_text(), flags=re.M)
    if count != 1:
        raise ValueError("Expected one Wally package version")
    for name, data in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    manifest_path.write_text(manifest)
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    readme_path = root / "README.md"
    if readme_path.exists():
        readme = re.sub(r'ewanophobia/libmp-mirror@[0-9]+\.[0-9]+\.[0-9]+', f'ewanophobia/libmp-mirror@{version}', readme_path.read_text())
        readme_path.write_text(readme)
    print(f"Mirrored upstream {release['tag_name']} as Wally {version}")
    return True


if __name__ == "__main__":
    try:
        sync()
    except Exception as error:
        print(f"Upstream sync failed: {error}", file=sys.stderr)
        sys.exit(1)
