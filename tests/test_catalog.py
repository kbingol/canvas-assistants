import importlib.util
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("catalog", ROOT / "scripts" / "build_catalog.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


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


if __name__ == "__main__":
    unittest.main()
