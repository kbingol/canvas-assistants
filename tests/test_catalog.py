import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("catalog", ROOT / "scripts" / "build_catalog.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
PUBLISH_SPEC = importlib.util.spec_from_file_location("publisher", ROOT / "scripts" / "publish_catalog.py")
PUBLISHER = importlib.util.module_from_spec(PUBLISH_SPEC)
with mock.patch.dict(sys.modules, {"build_catalog": MODULE}):
    PUBLISH_SPEC.loader.exec_module(PUBLISHER)


class CatalogChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        shutil.copytree(ROOT / "assistants", self.root / "assistants")
        self.package = self.root / "assistants" / "turkish-translator" / "package"
        self.manifest = self.package / "assistant.json"

    def tearDown(self):
        self.temporary.cleanup()

    def change(self, **updates):
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        data.update(updates)
        self.manifest.write_text(json.dumps(data), encoding="utf-8")

    def test_deterministic_archive_identity_hash_and_portability(self):
        first = MODULE.build(self.root, self.root / "a")
        second = MODULE.build(self.root, self.root / "b")
        self.assertEqual(first, second)
        entry = first["assistants"][0]
        filename = entry["packageUrl"].rsplit("/", 1)[1]
        archive = self.root / "a" / filename
        self.assertEqual(archive.read_bytes(), (self.root / "b" / filename).read_bytes())
        with zipfile.ZipFile(archive) as package:
            self.assertEqual(set(package.namelist()), {
                "assistant.json", "SKILL.md", "skills/translation/SKILL.md"})
            self.assertEqual(json.loads(package.read("assistant.json"))["id"], entry["id"])
        self.assertEqual(archive.stat().st_size, entry["packageBytes"])

    def test_undeclared_file_rejected(self):
        (self.package / "run.exe").write_bytes(b"not allowed")
        with self.assertRaises(ValueError):
            MODULE.build(self.root, self.root / "dist")

    def test_unsafe_skill_path_rejected(self):
        self.change(skills=[{"id": "bad", "version": "1.0.0", "path": "../outside.md"}])
        with self.assertRaises(ValueError):
            MODULE.build(self.root, self.root / "dist")

    def test_duplicate_identity_rejected(self):
        shutil.copytree(self.package.parent, self.package.parent.parent / "duplicate")
        with self.assertRaises(ValueError):
            MODULE.build(self.root, self.root / "dist")

    def test_unknown_tool_rejected(self):
        self.change(tools=["execute_arbitrary_code"])
        with self.assertRaises(ValueError):
            MODULE.build(self.root, self.root / "dist")

    def test_instruction_size_bounded(self):
        (self.package / "SKILL.md").write_text("x" * 65537)
        with self.assertRaises(ValueError):
            MODULE.build(self.root, self.root / "dist")

    def test_duplicate_json_key_rejected(self):
        self.manifest.write_text('{"schemaVersion":1,"schemaVersion":1}', encoding="utf-8")
        with self.assertRaises(ValueError):
            MODULE.build(self.root, self.root / "dist")

    def test_unsafe_version_rejected(self):
        self.change(version="1.0.0/../bad")
        with self.assertRaises(ValueError):
            MODULE.build(self.root, self.root / "dist")

    def reject_manifest(self, mutate):
        original = self.manifest.read_bytes()
        data = json.loads(original)
        mutate(data)
        self.manifest.write_text(json.dumps(data), encoding="utf-8")
        try:
            with self.assertRaises(ValueError):
                MODULE.build(self.root, self.root / "dist")
        finally:
            self.manifest.write_bytes(original)

    def test_existing_release_bytes_remain_immutable(self):
        built = MODULE.build(self.root, self.root / "dist")["assistants"]
        published = {(entry["id"], entry["version"]): entry for entry in
                     json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))["assistants"]}
        for entry in built:
            prior = published.get((entry["id"], entry["version"]))
            if prior is not None:
                self.assertEqual(entry["sha256"], prior["sha256"])
                self.assertEqual(entry["packageBytes"], prior["packageBytes"])

    def test_manifest_field_contract_rejected(self):
        for field in ("contextPreamble", "budget", "acceptance", "skills"):
            with self.subTest(missing=field):
                self.reject_manifest(lambda data, field=field: data.pop(field))
        for field, value in (("unexpected", 1), ("schemaVersion", True), ("contextPreamble", " "),
                             ("icon", "executable"), ("icon", {}), ("name", "😀" * 65)):
            with self.subTest(field=field, value=value):
                self.reject_manifest(lambda data, field=field, value=value: data.update({field: value}))

    def test_identifier_version_and_control_contract_rejected(self):
        for field, value in (("id", "community..translator"), ("id", "community-translator-"),
                             ("pluginId", "community._translator"), ("id", "a" * 129),
                             ("id", "con"), ("id", "aux.translator"), ("id", "com1.translator"),
                             ("version", "1" * 61 + ".0.0")):
            with self.subTest(field=field, value=value):
                self.reject_manifest(lambda data, field=field, value=value: data.update({field: value}))
        for character in ("\x7f", "\x80", "\x85", "\x9f"):
            with self.subTest(control=ord(character)):
                self.reject_manifest(lambda data, character=character: data.update(description="Text" + character))
        self.reject_manifest(lambda data: data.update(id="a" * 128, version="1" * 60 + ".0.0"))

    def test_budget_fields_ranges_and_types_rejected(self):
        limits = {"maxModelRequests": (1, 128), "maxToolCalls": (0, 512),
                  "maxValidationAttempts": (1, 32), "timeoutSeconds": (1, 3600),
                  "maxOutputTokens": (1, 131072), "maxToolCallsPerResponse": (1, 64)}
        for field, (minimum, maximum) in limits.items():
            for value in (minimum - 1, maximum + 1, True, 1.0, "1", None):
                with self.subTest(field=field, value=value):
                    self.reject_manifest(lambda data, field=field, value=value: data["budget"].update({field: value}))
        self.reject_manifest(lambda data: data["budget"].pop("maxModelRequests"))
        self.reject_manifest(lambda data: data["budget"].update(unknown=1))

    def test_acceptance_contract_rejected(self):
        for field, value in (("requiredSkills", ["missing"]), ("requiredSkills", ["translation", "translation"]),
                             ("maximumTextCharacters", 0), ("maximumTextCharacters", 1024 * 1024 + 1),
                             ("disallowedPreamblePrefixes", ["prefix\n"]), ("disallowedPreamblePrefixes", ["x", "x"]),
                             ("rejectOpaqueCitationMarkers", 1), ("allowLegacyTextEnvelope", "false"),
                             ("requireNonblankText", None), ("images", None), ("unknown", True)):
            with self.subTest(field=field, value=value):
                self.reject_manifest(lambda data, field=field, value=value: data["acceptance"].update({field: value}))
        self.reject_manifest(lambda data: data["acceptance"].pop("requireNonblankText"))

    def test_skill_fields_and_budget_consistency_rejected(self):
        for field, value in (("id", "bad..skill"), ("version", "latest"), ("unknown", "x"),
                             ("path", "skills/CON.md"), ("path", "skills/trailing./SKILL.md"),
                             ("path", "skills/control\x85/SKILL.md")):
            with self.subTest(field=field, value=value):
                self.reject_manifest(lambda data, field=field, value=value: data["skills"][0].update({field: value}))
        self.reject_manifest(lambda data: data["skills"].append(dict(data["skills"][0])))
        self.reject_manifest(lambda data: data.update(tools=["inspect_image"]))
        self.reject_manifest(lambda data: data["budget"].update(maxToolCalls=0))
        self.reject_manifest(lambda data: data.update(skills=[]))
        self.reject_manifest(lambda data: data.update(tools=["read_skill", "read_skill"]))

    def test_image_generation_acceptance_is_agentic_and_bounded(self):
        image_rules = {"maximumOutputs": 8, "maximumRevisionsPerOutput": 3,
                       "minimumReviewCharacters": 12, "maximumReviewCharacters": 2000}
        data = json.loads(self.manifest.read_bytes())
        data["tools"].append("generate_image")
        data["acceptance"]["images"] = image_rules
        self.manifest.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(len(MODULE.build(self.root, self.root / "valid-image")["assistants"]), 1)
        self.reject_manifest(lambda data: data["tools"].remove("inspect_image"))
        self.reject_manifest(lambda data: data["acceptance"].pop("images"))
        self.reject_manifest(lambda data: data["acceptance"].update(requiredSkills=[]))
        self.reject_manifest(lambda data: data["budget"].update(maxToolCalls=1))
        self.reject_manifest(lambda data: data["budget"].update(maxToolCallsPerResponse=2))
        for field, value in (("maximumOutputs", 9), ("maximumRevisionsPerOutput", 4),
                             ("minimumReviewCharacters", 11), ("maximumReviewCharacters", 11), ("unknown", 1)):
            with self.subTest(field=field, value=value):
                self.reject_manifest(lambda data, field=field, value=value: data["acceptance"]["images"].update({field: value}))

    def test_blank_and_null_skill_content_rejected(self):
        path = self.package / "SKILL.md"
        for content in (b" ", b"\xef\xbb\xbf", b"Instructions\0hidden"):
            with self.subTest(content=content):
                path.write_bytes(content)
                with self.assertRaises(ValueError):
                    MODULE.build(self.root, self.root / "dist")

    def test_listing_control_characters_rejected(self):
        listing_path = self.package.parent / "listing.json"
        listing = json.loads(listing_path.read_bytes())
        listing["publisher"] = "Publisher\x85hidden"
        listing_path.write_text(json.dumps(listing), encoding="utf-8")
        with self.assertRaises(ValueError):
            MODULE.build(self.root, self.root / "dist")


class PublicationChecks(unittest.TestCase):
    SOURCE = "a" * 40
    ADVANCED = "b" * 40

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        shutil.copytree(ROOT / "assistants", self.root / "assistants")
        self.calls = []
        self.head = self.SOURCE
        self.patch_result = None
        self.download_mismatch = False
        self.release_missing = False

    def tearDown(self):
        self.temporary.cleanup()

    def fake_gh(self, *args, check=True):
        payload = None
        if "--input" in args:
            payload = json.loads(Path(args[args.index("--input") + 1]).read_text(encoding="utf-8"))
        self.calls.append((args, payload))
        def result(data=None, code=0, stderr=""):
            return subprocess.CompletedProcess(args, code, json.dumps(data or {}), stderr)
        if args[:2] == ("release", "download"):
            directory = Path(args[args.index("--dir") + 1])
            filename = args[args.index("--pattern") + 1]
            data = (self.root / "dist" / filename).read_bytes()
            (directory / filename).write_bytes(data + b"modified" if self.download_mismatch else data)
            return result()
        if args[:2] == ("release", "create"):
            return result()
        if args[0] != "api":
            raise AssertionError(f"Unexpected GitHub call: {args}")
        method = args[args.index("--method") + 1] if "--method" in args else "GET"
        path = args[args.index("--method") + 2] if "--method" in args else args[1]
        if path.endswith("/git/ref/heads/main"):
            return result({"object": {"sha": self.head}})
        if "/releases/tags/" in path:
            return result({"status": "404"}, 1, "gh: Not Found (HTTP 404)") if self.release_missing else result({"draft": False, "prerelease": False})
        if path.endswith("/contents/catalog.json"):
            return result({"status": "404"}, 1, "gh: Not Found (HTTP 404)")
        if method == "GET" and "/git/commits/" in path:
            return result({"tree": {"sha": "original-tree"}})
        if method == "POST" and path.endswith("/git/blobs"):
            return result({"sha": "new-blob"})
        if method == "POST" and path.endswith("/git/trees"):
            return result({"sha": "new-tree"})
        if method == "POST" and path.endswith("/git/commits"):
            return result({"sha": "new-commit"})
        if method == "PATCH" and path.endswith("/git/refs/heads/main"):
            if self.patch_result:
                self.head = self.ADVANCED
                return self.patch_result
            self.head = "new-commit"
            return result({"object": {"sha": self.head}})
        raise AssertionError(f"Unexpected GitHub API call: {args}")

    def publish(self):
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": MODULE.REPOSITORY, "GITHUB_SHA": self.SOURCE}), \
                mock.patch.object(PUBLISHER, "gh", side_effect=self.fake_gh), mock.patch("builtins.print"):
            return PUBLISHER.publish(self.root)

    def test_catalog_commit_is_anchored_to_exact_source_with_atomic_fast_forward(self):
        self.assertTrue(self.publish())
        commits = [body for args, body in self.calls if args[:3] == ("api", "--method", "POST") and args[3].endswith("/git/commits")]
        self.assertEqual(commits[0]["parents"], [self.SOURCE])
        updates = [body for args, body in self.calls if args[:3] == ("api", "--method", "PATCH")]
        self.assertEqual(updates, [{"sha": "new-commit", "force": False}])
        self.assertFalse(any("PUT" in args for args, _ in self.calls))
        tree = next(body for args, body in self.calls if args[:3] == ("api", "--method", "POST") and args[3].endswith("/git/trees"))
        self.assertEqual(tree["base_tree"], "original-tree")
        self.assertEqual(tree["tree"], [{"path": "catalog.json", "mode": "100644", "type": "blob", "sha": "new-blob"}])

    def test_source_push_race_cannot_publish_stale_catalog(self):
        self.patch_result = subprocess.CompletedProcess([], 1,
            json.dumps({"message": "Update is not a fast forward", "status": "422"}),
            "gh: Update is not a fast forward (HTTP 422)")
        self.assertFalse(self.publish())
        self.assertEqual(self.head, self.ADVANCED)
        patch = next(body for args, body in self.calls if "PATCH" in args)
        self.assertFalse(patch["force"])
        commit = next(body for args, body in self.calls if args[:3] == ("api", "--method", "POST") and args[3].endswith("/git/commits"))
        self.assertEqual(commit["parents"], [self.SOURCE])

    def test_already_stale_source_skips_all_release_mutations(self):
        self.head = self.ADVANCED
        self.assertFalse(self.publish())
        self.assertEqual(len(self.calls), 1)

    def test_permission_failure_is_not_hidden_as_source_push_race(self):
        self.patch_result = subprocess.CompletedProcess([], 1, json.dumps({"status": "403"}),
            "gh: Resource not accessible by integration (HTTP 403)")
        with self.assertRaisesRegex(PUBLISHER.GitHubError, "HTTP 403"):
            self.publish()

    def test_existing_version_bytes_cannot_be_replaced(self):
        self.download_mismatch = True
        with self.assertRaisesRegex(RuntimeError, "increment the package version"):
            self.publish()
        self.assertFalse(any("--method" in args for args, _ in self.calls))

    def test_missing_release_created_without_replacing_assets(self):
        self.release_missing = True
        self.assertTrue(self.publish())
        created = [args for args, _ in self.calls if args[:2] == ("release", "create")]
        self.assertEqual(len(created), 1)
        self.assertIn(self.SOURCE, created[0])
        self.assertFalse(any("upload" in args or "--clobber" in args for args, _ in self.calls))

    def test_github_authentication_errors_keep_safe_actionable_diagnostics(self):
        secret = "super-secret-env-token"
        failure = subprocess.CompletedProcess([], 1, "", "gh: HTTP 403\nAuthorization: Bearer " + secret +
            "\nhttps://user:password@api.github.com/repos/example?token=query-secret\nghp_abcdef123456")
        with mock.patch.dict(os.environ, {"GH_TOKEN": secret}), \
                mock.patch.object(PUBLISHER.subprocess, "run", return_value=failure):
            with self.assertRaises(PUBLISHER.GitHubError) as caught:
                PUBLISHER.gh("api", "repos/example")
        message = str(caught.exception)
        self.assertIn("HTTP 403", message)
        for sensitive in (secret, "password", "query-secret", "ghp_abcdef123456", "Bearer"):
            self.assertNotIn(sensitive, message)

    def test_optional_missing_lookup_does_not_hide_auth_failure(self):
        failure = subprocess.CompletedProcess([], 1, json.dumps({"status": "403"}), "gh: Forbidden (HTTP 403)")
        with mock.patch.object(PUBLISHER, "gh", return_value=failure):
            with self.assertRaisesRegex(PUBLISHER.GitHubError, "HTTP 403"):
                PUBLISHER.get_optional("repos/example/releases/tags/v1")


if __name__ == "__main__":
    unittest.main()
