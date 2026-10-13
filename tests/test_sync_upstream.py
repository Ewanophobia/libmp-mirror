"""Offline regression tests for release selection and safe mirror updates."""

import importlib.util
import io
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch
import zipfile


SPEC = importlib.util.spec_from_file_location(
    "libmp_sync_upstream", Path(__file__).parents[1] / "scripts" / "sync_upstream.py"
)
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)

COMMIT = "a" * 40
SOURCE = b"-- upstream fixture\nreturn {Version = '1.1'}\n"
FILES = {
    "libmp.luau": SOURCE,
    "LICENSE": b"Fixture LibMP license\n",
    "LICENSES/flatbuffers.txt": b"Fixture FlatBuffers license\n",
    "LICENSES/lz4.txt": b"Fixture LZ4 license\n",
    "LICENSES/spider_runtime.txt": b"Fixture Spider license\n",
}


def release(tag="1.1", **changes):
    result = {
        "id": 123,
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "published_at": "2026-10-08T19:46:26Z",
        "html_url": f"https://github.com/Roblox/libmp/releases/tag/{tag}",
    }
    result.update(changes)
    return result


def asset(name, data, **changes):
    result = {
        "id": 456,
        "name": name,
        "size": len(data),
        "digest": "sha256:" + sync.sha256(data),
        "browser_download_url": f"https://github.com/Roblox/libmp/releases/download/1.1/{name}",
    }
    result.update(changes)
    return result


def bundle(members=None):
    if members is None:
        members = {"libmp-all-languages/luau/LibMP.luau": SOURCE}
        for target, dependency in (
            ("LICENSE", "libmp"), ("LICENSES/flatbuffers.txt", "flatbuffers"),
            ("LICENSES/lz4.txt", "lz4"), ("LICENSES/spider_runtime.txt", "spider_runtime"),
        ):
            members[f"libmp-all-languages/licenses/{dependency}/LICENSE"] = FILES[target]
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return result.getvalue()


class OfflineTests(unittest.TestCase):
    def setUp(self):
        # A missing mock must fail immediately instead of contacting GitHub.
        self.network = patch.object(sync, "urlopen", side_effect=AssertionError("Network forbidden in tests"))
        self.network.start()
        self.addCleanup(self.network.stop)


class VersionTests(OfflineTests):
    def test_semver_normalization_and_invalid_tags(self):
        for tag, expected in (("1.1", "1.1.0"), ("v2.3.4", "2.3.4"), ("0.0", "0.0.0")):
            with self.subTest(tag=tag):
                self.assertEqual(sync.version_text(sync.parse_version(tag)), expected)
        for tag in ("latest", "1", "01.1", "1.01.0", "1.2.03", "1.2.3-beta.1", "1.2.3+build", "1.2.3.4"):
            with self.subTest(tag=tag):
                self.assertIsNone(sync.parse_version(tag))

    def test_highest_stable_version_wins_over_dates_and_rolling_tag(self):
        expected = release("1.10", published_at="2026-09-01T00:00:00Z")
        releases = [
            release("latest"), release("1.9"), expected,
            release("2.0", prerelease=True), release("3.0", draft=True),
            release("4.0.0-beta.1"),
        ]
        self.assertIs(sync.select_release(releases), expected)
        with self.assertRaisesRegex(ValueError, "No numbered stable"):
            sync.select_release([release("latest"), release("2.0", prerelease=True)])

    def test_release_listing_includes_later_pages(self):
        with patch.object(sync, "api", side_effect=[[release("1.0")] * 100, [release("2.0")]]) as api:
            self.assertEqual(sync.newest_release()["tag_name"], "2.0")
        self.assertEqual([call.args[0] for call in api.call_args_list], [
            "releases?per_page=100&page=1", "releases?per_page=100&page=2",
        ])

    def test_replacement_and_upstream_patch_collisions_stay_monotonic(self):
        self.assertEqual(sync.next_version("v1.1", None), "1.1.0")
        previous = {"upstream": {"tag": "1.1"}, "wally_version": "1.1.3"}
        for tag, expected in (("1.1", "1.1.4"), ("1.1.2", "1.1.4"), ("1.2", "1.2.0")):
            with self.subTest(tag=tag):
                self.assertEqual(sync.next_version(tag, previous), expected)
        with self.assertRaisesRegex(ValueError, "downgrade"):
            sync.next_version("1.0.9", previous)


class ArtifactTests(OfflineTests):
    def test_bundle_has_exact_module_and_license_contents(self):
        self.assertEqual(sync.extract_bundle(bundle()), FILES)

    def test_zip_paths_never_become_output_paths(self):
        # The importer reads members into a fixed allowlist; it never extracts
        # member paths to the filesystem, even for a malicious archive.
        data = bundle({
            "../../LibMP.luau": SOURCE,
            "../../licenses/libmp/LICENSE": FILES["LICENSE"],
            "/absolute/other.txt": b"must be ignored",
            "../../outside.txt": b"must be ignored",
        })
        self.assertEqual(sync.extract_bundle(data), {"libmp.luau": SOURCE, "LICENSE": FILES["LICENSE"]})

    def test_ambiguous_or_absent_module_and_ambiguous_license_fail(self):
        fixtures = [
            {"a/LibMP.luau": SOURCE, "b/libmp.luau": SOURCE},
            {"unrelated.luau": SOURCE},
            {"LibMP.luau": SOURCE, "a/licenses/libmp/LICENSE": b"one", "b/licenses/libmp/LICENSE": b"two"},
        ]
        for members in fixtures:
            with self.subTest(members=list(members)), self.assertRaisesRegex(ValueError, "Expected exactly one"):
                sync.extract_bundle(bundle(members))

    def test_uncompressed_member_limit(self):
        with patch.object(sync, "LIMIT", 8), self.assertRaisesRegex(ValueError, "too large"):
            sync.extract_bundle(bundle({"LibMP.luau": b"x" * 9}))

    def test_download_size_digest_and_origin_validation(self):
        good = asset("LibMP.luau", SOURCE)
        with patch.object(sync, "fetch", return_value=SOURCE) as fetch:
            self.assertEqual(sync.download_asset(good), SOURCE)
            for changes, message in (({"size": len(SOURCE) + 1}, "size mismatch"),
                                     ({"digest": "sha256:" + "0" * 64}, "digest mismatch")):
                with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, message):
                    sync.download_asset({**good, **changes})
            fetch.reset_mock()
            with self.assertRaisesRegex(ValueError, "Unexpected upstream asset URL"):
                sync.download_asset({**good, "browser_download_url": "https://example.com/LibMP.luau"})
            fetch.assert_not_called()

    def test_direct_and_zip_disagreement_is_rejected(self):
        zipped = bundle()
        direct = asset("LibMP.luau", b"return {different = true}\n")
        zipped_asset = asset("LibMP-Luau.zip", zipped)
        with patch.object(sync, "api", return_value=[zipped_asset, direct]), \
             patch.object(sync, "download_asset", side_effect=[zipped, b"return {different = true}\n"]), \
             self.assertRaisesRegex(ValueError, "differs from the release ZIP"):
            sync.release_files(release(), COMMIT)

    def test_direct_only_release_fetches_licenses_from_pinned_commit(self):
        with patch.object(sync, "api", return_value=[asset("LibMP.luau", SOURCE)]), \
             patch.object(sync, "download_asset", return_value=SOURCE), \
             patch.object(sync, "fetch", side_effect=lambda url: b"Pinned fixture license\n") as fetch:
            files, provenance = sync.release_files(release(), COMMIT)
        self.assertEqual(set(files), set(FILES))
        self.assertEqual(files["libmp.luau"], SOURCE)
        self.assertEqual(provenance[0]["sha256"], sync.sha256(SOURCE))
        self.assertEqual({call.args[0] for call in fetch.call_args_list}, {
            f"https://raw.githubusercontent.com/Roblox/libmp/{COMMIT}/{path}"
            for path in sync.LICENSE_PATHS.values()
        })


class MirrorTests(OfflineTests):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "wally.toml").write_text('[package]\nname = "ewanophobia/libmp-mirror"\nversion = "0.0.0"\n')
        (self.root / "README.md").write_text('LibMP = "ewanophobia/libmp-mirror@0.0.0"\n')
        self.upstream = release()
        self.files = dict(FILES)
        for name, replacement in (
            ("newest_release", lambda: self.upstream),
            ("resolve_commit", lambda tag: COMMIT),
            ("release_files", lambda release, commit: (dict(self.files), [])),
        ):
            mocked = patch.object(sync, name, side_effect=replacement)
            mocked.start()
            self.addCleanup(mocked.stop)

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def test_initial_sync_preserves_bytes_and_repeated_sync_is_idempotent(self):
        self.assertTrue(sync.sync(self.root))
        for name, expected in FILES.items():
            self.assertEqual((self.root / name).read_bytes(), expected)
        metadata = json.loads((self.root / "upstream.json").read_text())
        self.assertEqual(metadata["wally_version"], "1.1.0")
        self.assertEqual(metadata["upstream"]["commit"], COMMIT)
        self.assertEqual(metadata["files"], {name: sync.sha256(data) for name, data in FILES.items()})
        self.assertIn("ewanophobia/libmp-mirror@1.1.0", (self.root / "README.md").read_text())
        before = self.snapshot()
        mtimes = {path: path.stat().st_mtime_ns for path in self.root.rglob("*") if path.is_file()}
        self.assertFalse(sync.sync(self.root))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual({path: path.stat().st_mtime_ns for path in mtimes}, mtimes)

    def test_changed_release_asset_bumps_then_colliding_upstream_version_bumps_again(self):
        sync.sync(self.root)
        self.files["libmp.luau"] = b"return {replaced = true}\n"
        self.assertTrue(sync.sync(self.root))
        self.assertEqual(tomllib.loads((self.root / "wally.toml").read_text())["package"]["version"], "1.1.1")
        self.upstream = release("1.1.1")
        self.files["libmp.luau"] = b"return {newRelease = true}\n"
        self.assertTrue(sync.sync(self.root))
        self.assertEqual(tomllib.loads((self.root / "wally.toml").read_text())["package"]["version"], "1.1.2")

    def test_local_module_tampering_stops_without_writing(self):
        sync.sync(self.root)
        (self.root / "libmp.luau").write_bytes(b"return {localEdit = true}\n")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "Local mirrored file changed"):
            sync.sync(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_manifest_tampering_stops_without_writing(self):
        sync.sync(self.root)
        path = self.root / "wally.toml"
        path.write_text(path.read_text().replace('version = "1.1.0"', 'version = "9.0.0"'))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "Manifest version disagrees"):
            sync.sync(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_upstream_rollback_is_rejected_without_writing(self):
        sync.sync(self.root)
        self.upstream = release("1.0")
        self.files["libmp.luau"] = b"return {old = true}\n"
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "downgrade"):
            sync.sync(self.root)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
