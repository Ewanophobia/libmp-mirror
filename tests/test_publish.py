"""Release safety tests; no external network or credentials are used."""

import importlib.util
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error
import zipfile


SPEC = importlib.util.spec_from_file_location("libmp_publish", Path(__file__).parents[1] / "scripts" / "publish.py")
publish = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = publish
SPEC.loader.exec_module(publish)


def package_zip(*, source=b"return {}\n", version="1.1.0", extra=None):
    manifest = f'''[package]
name = "ewanophobia/libmp-mirror"
version = "{version}"
realm = "shared"
registry = "https://github.com/UpliftGames/wally-index"
license = "MIT"
'''
    files = {
        "wally.toml": manifest.encode(),
        "default.project.json": json.dumps({"name": "libmp-mirror", "tree": {"$path": "libmp.luau"}}).encode(),
        "libmp.luau": source,
        "LICENSE": b"Example license for test fixtures.\n",
        "LICENSES/example.txt": b"An upstream dependency license.\n",
    }
    if extra:
        files.update(extra)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return output.getvalue()


class ArchiveTests(unittest.TestCase):
    def test_expected_distribution(self):
        result = publish.validate_archive(package_zip())
        self.assertEqual(result.name, "ewanophobia/libmp-mirror")
        self.assertEqual(result.version, "1.1.0")

    def test_forbidden_archive_paths(self):
        for path in (".github/token", "../secret", "LICENSES/../secret", "scripts/publish.py"):
            with self.subTest(path=path), self.assertRaises(publish.PublishError):
                publish.validate_archive(package_zip(extra={path: b"unexpected"}))

    def test_invalid_module_and_project(self):
        with self.assertRaises(publish.PublishError):
            publish.validate_archive(package_zip(source=b"\n"))
        with self.assertRaises(publish.PublishError):
            publish.validate_archive(package_zip(extra={"default.project.json": b'{"name":"libmp-mirror","tree":{"$path":"wrong.luau"}}'}))

    def test_invalid_registry_type(self):
        bad_manifest = b'''[package]
name = "ewanophobia/libmp-mirror"
version = "1.1.0"
realm = "shared"
registry = 42
'''
        with self.assertRaises(publish.PublishError):
            publish.validate_archive(package_zip(extra={"wally.toml": bad_manifest}))

    def test_dependencies_are_rejected(self):
        original = publish.validate_archive(package_zip())
        with zipfile.ZipFile(io.BytesIO(original.archive)) as archive:
            manifest = archive.read("wally.toml")
        for section in ("dependencies", "server-dependencies", "dev-dependencies"):
            extra = manifest + f'\n[{section}]\nOther = "someone/other@1.0.0"\n'.encode()
            with self.subTest(section=section), self.assertRaisesRegex(publish.PublishError, "no dependencies"):
                publish.validate_archive(package_zip(extra={"wally.toml": extra}))

    def test_invalid_version(self):
        for version in ("01.1.0", "1.0", "1.0.0-beta.01", "1.0.0/elsewhere"):
            with self.subTest(version=version), self.assertRaises(publish.PublishError):
                publish.validate_archive(package_zip(version=version))

    def test_size_limit(self):
        with self.assertRaises(publish.PublishError):
            publish.validate_archive(b"x" * (publish.MAX_ARCHIVE_BYTES + 1))


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.data = package_zip()

    @patch.object(publish, "request")
    def test_existing_identical_package_needs_no_token(self, request):
        request.return_value = (200, self.data)
        self.assertIn("already published", publish.publish_archive(self.data, None))
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0], "GET")

    @patch.object(publish, "request")
    def test_existing_mismatch_is_not_overwritten(self, request):
        request.return_value = (200, package_zip(source=b"return {changed = true}\n"))
        with self.assertRaisesRegex(publish.PublishError, "different libmp.luau"):
            publish.publish_archive(self.data, "test-token")
        self.assertEqual(request.call_count, 1)

    @patch.object(publish, "request")
    def test_operational_readme_edits_do_not_require_republication(self, request):
        request.return_value = (200, package_zip(extra={"README.md": b"Original operating instructions."}))
        self.assertIn("already published", publish.publish_archive(self.data, None))

    @patch.object(publish, "request")
    def test_known_original_version_can_be_reused_after_license_move(self, request):
        request.return_value = (200, self.data)
        updated = package_zip(extra={
            "LICENSE": b"Repository Unlicense.\n",
            "LICENSES/libmp.txt": b"Example license for test fixtures.\n",
            "upstream.json": b'{"wally_version":"1.1.0"}',
        })
        with patch.object(publish, "ORIGINAL_1_1_0_SHA256", hashlib.sha256(self.data).hexdigest()):
            self.assertIn("already published", publish.publish_archive(updated, None))
        with self.assertRaisesRegex(publish.PublishError, "differs in"):
            publish.publish_archive(updated, None)
        request.return_value = (200, package_zip(source=b"return {changed = true}\n"))
        with self.assertRaisesRegex(publish.PublishError, "different libmp.luau"):
            publish.publish_archive(updated, None)

    @patch.object(publish, "request")
    def test_original_version_still_requires_identical_upstream_license(self, request):
        request.return_value = (200, self.data)
        updated = package_zip(extra={
            "LICENSE": b"Repository Unlicense.\n",
            "LICENSES/libmp.txt": b"Different upstream license.\n",
        })
        with patch.object(publish, "ORIGINAL_1_1_0_SHA256", hashlib.sha256(self.data).hexdigest()):
            with self.assertRaisesRegex(publish.PublishError, "differs in"):
                publish.publish_archive(updated, None)

    @patch.object(publish, "request")
    def test_license_or_provenance_mismatch_is_rejected(self, request):
        for path in ("LICENSE", "LICENSES/example.txt", "upstream.json"):
            with self.subTest(path=path):
                request.return_value = (200, package_zip(extra={path: b"Changed distribution file"}))
                with self.assertRaisesRegex(publish.PublishError, "differs in"):
                    publish.publish_archive(self.data, "test-token")

    @patch.object(publish, "request")
    def test_registry_mismatched_version_is_rejected(self, request):
        request.return_value = (200, package_zip(version="1.0.0"))
        with self.assertRaisesRegex(publish.PublishError, "different name or version"):
            publish.publish_archive(self.data, "test-token")

    @patch.object(publish, "request")
    def test_missing_token_fails_only_for_absent_package(self, request):
        request.return_value = (404, b"")
        with self.assertRaisesRegex(publish.PublishError, "WALLY_AUTH_TOKEN"):
            publish.publish_archive(self.data, None)
        self.assertEqual(request.call_count, 1)

    @patch.object(publish, "request")
    def test_lookup_failure_is_not_treated_as_absence(self, request):
        request.return_value = (500, b"")
        with self.assertRaisesRegex(publish.RegistryError, "HTTP 500"):
            publish.publish_archive(self.data, "test-token")
        self.assertEqual(request.call_count, 1)

    @patch.object(publish.time, "sleep")
    @patch.object(publish, "request")
    def test_publish_retries_readback_until_visible(self, request, sleep):
        request.side_effect = [(404, b""), (200, b"{}"), (404, b""), (503, b""), (200, self.data)]
        self.assertIn("Published and verified", publish.publish_archive(self.data, "test-token"))
        posts = [call for call in request.call_args_list if call.args[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].kwargs, {"data": self.data, "token": "test-token"})
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])

    @patch.object(publish.time, "sleep")
    @patch.object(publish, "request")
    def test_conflict_is_success_only_after_matching_readback(self, request, sleep):
        request.side_effect = [(404, b""), (409, b""), (200, self.data)]
        self.assertIn("verified", publish.publish_archive(self.data, "test-token"))
        sleep.assert_not_called()

    @patch.object(publish, "request")
    def test_lost_publish_response_is_recovered_by_readback(self, request):
        request.side_effect = [(404, b""), publish.RegistryError("Lost response", retryable=True), (200, self.data)]
        self.assertIn("verified", publish.publish_archive(self.data, "test-token"))

    @patch.object(publish.time, "sleep")
    @patch.object(publish, "request")
    def test_rejected_publish_fails(self, request, sleep):
        request.side_effect = [(404, b""), (401, b""), (404, b"")]
        with self.assertRaisesRegex(publish.RegistryError, "HTTP 401"):
            publish.publish_archive(self.data, "test-token")
        sleep.assert_not_called()

    @patch.object(publish.time, "sleep")
    @patch.object(publish, "request")
    def test_accepted_publish_without_readback_fails(self, request, sleep):
        request.side_effect = [(404, b""), (200, b"{}")] + [(404, b"")] * 6
        with self.assertRaisesRegex(publish.PublishError, "still absent"):
            publish.publish_archive(self.data, "test-token")
        self.assertEqual(request.call_count, 8)

    @patch.object(publish, "request")
    def test_check_only_does_not_publish(self, request):
        request.return_value = (404, b"")
        self.assertIn("not yet published", publish.publish_archive(self.data, None, check_only=True))
        self.assertEqual(request.call_count, 1)


class HttpTests(unittest.TestCase):
    @patch.object(publish.urllib.request, "urlopen")
    def test_required_headers_and_bearer_auth(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = b"{}"
        self.assertEqual(publish.request("POST", "https://api.wally.run/v1/publish", data=b"zip", token="test-token"), (200, b"{}"))
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Wally-version"), "0.3.2")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        self.assertEqual(request.get_header("Content-type"), "application/zip")

    @patch.object(publish.urllib.request, "urlopen")
    def test_http_error_body_is_not_exposed(self, urlopen):
        urlopen.side_effect = urllib.error.HTTPError("https://api.wally.run/v1/publish", 401, "No", {}, io.BytesIO(b"sensitive response"))
        self.assertEqual(publish.request("POST", "https://api.wally.run/v1/publish", data=b"zip", token="test-token"), (401, b""))


if __name__ == "__main__":
    unittest.main()
