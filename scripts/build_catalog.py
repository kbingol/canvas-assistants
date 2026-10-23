"""Build bounded, deterministic .flowast packages and their public catalog."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

REPOSITORY = "kbingol/canvas-assistants"
IDENTIFIER = re.compile(r"[a-z][a-z0-9._-]{0,127}\Z")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
KNOWN_TOOLS = {"read_skill", "inspect_image", "web_search", "generate_image"}
MAX_EXPANDED = 512 * 1024
MAX_ARCHIVE = 2 * 1024 * 1024


def text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"Invalid {field}")
    if any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise ValueError(f"Control character in {field}")
    return value


def load_json(path: Path) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    if not isinstance(result, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return result


def build(root: Path, output: Path) -> dict:
    root, output = root.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    entries, ids, plugins = [], set(), set()
    for directory in sorted((root / "assistants").iterdir()):
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError("Assistant entries must be ordinary directories")
        listing = load_json(directory / "listing.json")
        if set(listing) != {"publisher", "usage"}:
            raise ValueError("listing.json requires publisher and usage only")
        package_root = directory / "package"
        if package_root.is_symlink():
            raise ValueError("Package root cannot be a link")
        manifest = load_json(package_root / "assistant.json")
        if manifest.get("schemaVersion") != 1:
            raise ValueError("Unsupported package schema")
        assistant_id, plugin_id, version = (
            manifest.get("id"), manifest.get("pluginId"), manifest.get("version"))
        if not isinstance(assistant_id, str) or not IDENTIFIER.fullmatch(assistant_id):
            raise ValueError("Invalid assistant ID")
        if not isinstance(plugin_id, str) or not IDENTIFIER.fullmatch(plugin_id):
            raise ValueError("Invalid plugin ID")
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            raise ValueError("Version must be major.minor.patch")
        if assistant_id in ids or plugin_id in plugins:
            raise ValueError("Duplicate assistant or plugin ID")
        ids.add(assistant_id)
        plugins.add(plugin_id)
        tools = manifest.get("tools")
        if (not isinstance(tools, list) or any(not isinstance(t, str) for t in tools)
                or len(tools) != len(set(tools)) or not set(tools) <= KNOWN_TOOLS):
            raise ValueError("Unknown or duplicate host tools")
        skills = manifest.get("skills")
        if not isinstance(skills, list) or len(skills) > 32:
            raise ValueError("Invalid skills")
        paths = ["assistant.json", "SKILL.md"]
        for skill in skills:
            path = skill.get("path") if isinstance(skill, dict) else None
            if (not isinstance(path, str) or "\\" in path or ":" in path
                    or path.startswith("/") or any(p in ("", ".", "..") for p in path.split("/"))
                    or PurePosixPath(path).suffix.lower() != ".md"):
                raise ValueError("Unsafe skill path")
            paths.append(path)
        if len(paths) != len(set(p.casefold() for p in paths)) or len(paths) > 128:
            raise ValueError("Duplicate package path or too many files")
        actual = []
        for file in package_root.rglob("*"):
            if file.is_symlink():
                raise ValueError("Links are not allowed in packages")
            if file.is_file():
                actual.append(file.relative_to(package_root).as_posix())
        if sorted(paths) != sorted(actual):
            raise ValueError("Package contains missing or undeclared files")
        payload = io.BytesIO()
        expanded = 0
        with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_STORED) as archive:
            for path in sorted(paths):
                data = (package_root / path).read_bytes()
                data.decode("utf-8")
                expanded += len(data)
                limit = 65536 if path in ("assistant.json", "SKILL.md") else 32768
                if not data or len(data) > limit or expanded > MAX_EXPANDED:
                    raise ValueError("Package exceeds Canvas content limits")
                info = zipfile.ZipInfo(path, (1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data)
        data = payload.getvalue()
        if len(data) > MAX_ARCHIVE:
            raise ValueError("Package exceeds 2 MB")
        filename = f"{assistant_id}-{version}.flowast"
        tag = f"{assistant_id}-v{version}"
        (output / filename).write_bytes(data)
        entries.append({
            "id": assistant_id, "pluginId": plugin_id, "version": version,
            "name": text(manifest.get("name"), "name", 128),
            "description": text(manifest.get("description"), "description", 4096),
            "publisher": text(listing.get("publisher"), "publisher", 128),
            "usage": text(listing.get("usage"), "usage", 8192),
            "packageUrl": f"https://github.com/{REPOSITORY}/releases/download/{tag}/{filename}",
            "sha256": hashlib.sha256(data).hexdigest(), "packageBytes": len(data),
            "requiredTools": tools,
        })
    if not entries or len(entries) > 256:
        raise ValueError("The catalog must contain between 1 and 256 assistants")
    catalog = {"schemaVersion": 1, "assistants": entries}
    encoded = (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > 1024 * 1024:
        raise ValueError("Catalog exceeds 1 MB")
    (output / "catalog.json").write_bytes(encoded)
    return catalog


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()
    catalog = build(Path(__file__).resolve().parents[1], args.output)
    print(f"Built {len(catalog['assistants'])} assistant(s) in {args.output}")
