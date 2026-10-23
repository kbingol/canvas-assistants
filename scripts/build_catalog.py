"""Build bounded, deterministic .flowast packages and their public catalog."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

REPOSITORY = "kbingol/canvas-assistants"
IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?\Z")
KNOWN_TOOLS = {"read_skill", "inspect_image", "web_search", "generate_image"}
MAX_EXPANDED = 512 * 1024
MAX_ARCHIVE = 2 * 1024 * 1024


def text(value: object, field: str, maximum: int) -> str:
    if (not isinstance(value, str) or not value.strip()
            or len(value.encode("utf-16-le")) // 2 > maximum):
        raise ValueError(f"Invalid {field}")
    if any(unicodedata.category(c) == "Cc" and c not in "\n\r\t" for c in value):
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
    data = path.read_bytes()
    if not data or len(data) > 65536:
        raise ValueError(f"JSON file exceeds Canvas limits: {path.name}")
    def invalid_constant(value):
        raise ValueError(f"Unsupported JSON constant: {value}")
    result = json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique,
                        parse_constant=invalid_constant)
    if not isinstance(result, dict):
        raise ValueError(f"Expected JSON object: {path}")
    def depth(value, level=1):
        if isinstance(value, (dict, list)):
            if level > 16:
                raise ValueError("JSON exceeds Canvas depth limit")
            for child in value.values() if isinstance(value, dict) else value:
                depth(child, level + 1)
    depth(result)
    return result


def fields(value: object, required: set[str], optional: set[str] | None = None):
    if (not isinstance(value, dict) or not required <= value.keys()
            or not value.keys() <= required | (optional or set())):
        raise ValueError("Missing or unknown assistant manifest fields")


def identifier(value: object, field: str) -> str:
    value = text(value, field, 128)
    if not IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid {field}")
    return value


def version(value: object) -> str:
    value = text(value, "version", 64)
    if not VERSION.fullmatch(value):
        raise ValueError("Version must contain major.minor.patch")
    return value


def integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"Invalid {field}")
    return value


def strings(value: object, field: str, maximum_count: int, maximum_length: int) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum_count:
        raise ValueError(f"Invalid {field}")
    for item in value:
        text(item, field, maximum_length)
        if any(unicodedata.category(c) == "Cc" for c in item):
            raise ValueError(f"Control character in {field}")
    if len(value) != len(set(value)):
        raise ValueError(f"Duplicate {field}")
    return value


def safe_path(value: object) -> str:
    path = text(value, "skill path", 512)
    if "\\" in path or path.startswith("/"):
        raise ValueError("Unsafe skill path")
    for part in path.split("/"):
        if (part in ("", ".", "..") or part.endswith((".", " "))
                or any(unicodedata.category(c) == "Cc" or c in '<>:"\\|?*' for c in part)):
            raise ValueError("Unsafe skill path")
        if reserved_device(part):
            raise ValueError("Reserved Windows device name in skill path")
    if PurePosixPath(path).suffix.lower() != ".md":
        raise ValueError("Skills must be Markdown files")
    return path


def reserved_device(part: str) -> bool:
    basename = part.split(".")[0].rstrip(" ").upper()
    return (basename in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$"}
            or len(basename) == 4 and basename[:3] in {"COM", "LPT"}
            and basename[3] in "123456789¹²³")


def validate_manifest(manifest: dict) -> tuple[str, str, str, list[str], list[str]]:
    """Mirror Canvas's bounded data-only AssistantPackageLoader contract."""
    fields(manifest, {"schemaVersion", "id", "pluginId", "version", "name", "description",
                      "contextPreamble", "tools", "skills", "budget", "acceptance"}, {"icon"})
    integer(manifest["schemaVersion"], "schemaVersion", 1, 1)
    assistant_id = identifier(manifest["id"], "assistant ID")
    if reserved_device(assistant_id):
        raise ValueError("Assistant ID cannot name a reserved Windows installation directory")
    plugin_id = identifier(manifest["pluginId"], "plugin ID")
    package_version = version(manifest["version"])
    text(manifest["name"], "name", 128)
    text(manifest["description"], "description", 4096)
    text(manifest["contextPreamble"], "contextPreamble", 8192)
    if "icon" in manifest:
        if text(manifest["icon"], "icon", 16) not in {"document", "text", "image", "video"}:
            raise ValueError("Unknown assistant icon")
    tools = strings(manifest["tools"], "tools", 4, 128)
    if not set(tools) <= KNOWN_TOOLS:
        raise ValueError("Unknown host tool")
    budget = manifest["budget"]
    limits = {"maxModelRequests": (1, 128), "maxToolCalls": (0, 512),
              "maxValidationAttempts": (1, 32), "timeoutSeconds": (1, 3600),
              "maxOutputTokens": (1, 131072), "maxToolCallsPerResponse": (1, 64)}
    fields(budget, set(limits))
    for field, bounds in limits.items():
        integer(budget[field], field, *bounds)
    acceptance = manifest["acceptance"]
    fields(acceptance, {"requiredSkills", "maximumTextCharacters", "disallowedPreamblePrefixes",
                        "rejectOpaqueCitationMarkers", "allowLegacyTextEnvelope", "requireNonblankText"}, {"images"})
    required = strings(acceptance["requiredSkills"], "requiredSkills", 32, 128)
    strings(acceptance["disallowedPreamblePrefixes"], "disallowedPreamblePrefixes", 128, 256)
    integer(acceptance["maximumTextCharacters"], "maximumTextCharacters", 1, 1024 * 1024)
    for field in ("rejectOpaqueCitationMarkers", "allowLegacyTextEnvelope", "requireNonblankText"):
        if type(acceptance[field]) is not bool:
            raise ValueError(f"Invalid {field}")
    images = acceptance.get("images")
    if "images" in acceptance:
        fields(images, {"maximumOutputs", "maximumRevisionsPerOutput", "minimumReviewCharacters", "maximumReviewCharacters"})
        integer(images["maximumOutputs"], "maximumOutputs", 1, 8)
        integer(images["maximumRevisionsPerOutput"], "maximumRevisionsPerOutput", 1, 3)
        minimum = integer(images["minimumReviewCharacters"], "minimumReviewCharacters", 12, 2000)
        integer(images["maximumReviewCharacters"], "maximumReviewCharacters", minimum, 2000)
    skills = manifest["skills"]
    if not isinstance(skills, list) or len(skills) > 32:
        raise ValueError("Invalid skills")
    paths, skill_ids = ["assistant.json", "SKILL.md"], set()
    for skill in skills:
        fields(skill, {"id", "version", "path"})
        skill_id = identifier(skill["id"], "skill ID")
        version(skill["version"])
        if skill_id in skill_ids:
            raise ValueError("Duplicate skill ID")
        skill_ids.add(skill_id)
        paths.append(safe_path(skill["path"]))
    if len(paths) != len({path.casefold() for path in paths}) or len(paths) > 128:
        raise ValueError("Duplicate package path or too many files")
    if ("read_skill" in tools and not skills or not set(required) <= skill_ids
            or required and ("read_skill" not in tools or budget["maxToolCalls"] < len(required))):
        raise ValueError("Required skills must exist and be readable within the tool budget")
    if "generate_image" in tools and (images is None or "read_skill" not in tools
            or "inspect_image" not in tools or not required or budget["maxToolCalls"] <= len(required)
            or budget["maxToolCallsPerResponse"] != 1):
        raise ValueError("Image generation requires skills, sequential inspection and bounded acceptance rules")
    # Release URLs must also satisfy the app's bounded public-catalog download path contract.
    if len(f"{assistant_id}-{package_version}.flowast") > 200:
        raise ValueError("Package identity/version makes the public release filename too long")
    return assistant_id, plugin_id, package_version, tools, paths


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
        assistant_id, plugin_id, package_version, tools, paths = validate_manifest(manifest)
        if assistant_id in ids or plugin_id in plugins:
            raise ValueError("Duplicate assistant or plugin ID")
        ids.add(assistant_id)
        plugins.add(plugin_id)
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
                content = data.decode("utf-8-sig")
                expanded += len(data)
                limit = 65536 if path in ("assistant.json", "SKILL.md") else 32768
                if (not content.strip() or "\0" in content or len(data) > limit
                        or expanded > MAX_EXPANDED):
                    raise ValueError("Package exceeds Canvas content limits")
                info = zipfile.ZipInfo(path, (1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data)
        data = payload.getvalue()
        if len(data) > MAX_ARCHIVE:
            raise ValueError("Package exceeds 2 MB")
        filename = f"{assistant_id}-{package_version}.flowast"
        tag = f"{assistant_id}-v{package_version}"
        (output / filename).write_bytes(data)
        entries.append({
            "id": assistant_id, "pluginId": plugin_id, "version": package_version,
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
