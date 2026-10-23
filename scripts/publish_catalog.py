"""Publish immutable release assets before advertising them in catalog.json."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from build_catalog import REPOSITORY, build


def safe_error(value: str) -> str:
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        secret = os.environ.get(name)
        if secret:
            value = value.replace(secret, "[redacted]")
    value = re.sub(r"(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]+", "[redacted]", value)
    value = re.sub(r"(?im)authorization\s*[:=][^\r\n]*", "Authorization: [redacted]", value)
    def safe_url(match):
        try:
            url = urlsplit(match.group())
            return urlunsplit((url.scheme, url.hostname or "[redacted]", url.path, "", ""))
        except ValueError:
            return "[redacted URL]"
    value = re.sub(r"https?://[^\s]+", safe_url, value)
    value = "".join(c for c in value if unicodedata.category(c) != "Cc" or c in "\r\n\t")
    return value.strip()[:2000]


class GitHubError(RuntimeError):
    def __init__(self, result):
        detail = safe_error(result.stderr or "GitHub CLI returned no error details")
        super().__init__(f"GitHub operation failed (exit {result.returncode}): {detail}")


def gh(*args, check=True):
    result = subprocess.run(["gh", *args], check=False, capture_output=True, text=True)
    if check and result.returncode:
        raise GitHubError(result)
    return result


def status(result) -> int | None:
    try:
        value = json.loads(result.stdout).get("status")
        if value is not None:
            return int(value)
    except (ValueError, TypeError, AttributeError):
        pass
    match = re.search(r"\bHTTP (\d{3})\b", result.stderr)
    return int(match[1]) if match else None


def get_optional(path: str) -> dict | None:
    result = gh("api", path, check=False)
    if not result.returncode:
        return json.loads(result.stdout)
    if status(result) == 404:
        return None
    raise GitHubError(result)


def api_write(method: str, path: str, payload: dict, *, check=True):
    with tempfile.TemporaryDirectory() as directory:
        body = Path(directory) / "request.json"
        body.write_text(json.dumps(payload), encoding="utf-8")
        return gh("api", "--method", method, path, "--input", str(body), check=check)


def main_head() -> str:
    return json.loads(gh("api", f"repos/{REPOSITORY}/git/ref/heads/main").stdout)["object"]["sha"]


def publish(root: Path):
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise RuntimeError("Publishing is restricted to the canonical repository")
    source = os.environ.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[a-fA-F0-9]{40}", source):
        raise RuntimeError("Publishing requires the exact checked-out GITHUB_SHA")
    output = root / "dist"
    catalog = build(root, output)
    if main_head() != source:
        print("main advanced; skipped publication from the older source commit")
        return False
    for entry in catalog["assistants"]:
        filename = entry["packageUrl"].rsplit("/", 1)[1]
        tag = entry["packageUrl"].split("/")[-2]
        state = get_optional(f"repos/{REPOSITORY}/releases/tags/{tag}")
        if state is None:
            gh("release", "create", tag, str(output / filename), "--repo", REPOSITORY,
               "--target", source,
               "--title", f"{entry['name']} {entry['version']}",
               "--notes", entry["description"], "--latest=false")
        else:
            if state["draft"] or state["prerelease"]:
                raise RuntimeError(f"Release {tag} is not a public stable release")
        with tempfile.TemporaryDirectory() as directory:
            gh("release", "download", tag, "--repo", REPOSITORY, "--pattern", filename, "--dir", directory)
            downloaded = (Path(directory) / filename).read_bytes()
            if (len(downloaded) != entry["packageBytes"]
                    or hashlib.sha256(downloaded).hexdigest() != entry["sha256"]):
                raise RuntimeError(f"Published {tag} differs; increment the package version")

    current_data = get_optional(f"repos/{REPOSITORY}/contents/catalog.json")
    encoded = (output / "catalog.json").read_bytes()
    if current_data and base64.b64decode(current_data["content"]) == encoded:
        print("Catalog is already current")
        return True
    if main_head() != source:
        print("main advanced during package verification; catalog was not changed")
        return False
    # A Contents API PUT checks only the old file blob. It can silently create a commit
    # atop newer source. Anchor our commit to the exact build input instead, then ask
    # GitHub for an atomic non-forced fast-forward; a concurrent source push must win.
    base_tree = json.loads(gh("api", f"repos/{REPOSITORY}/git/commits/{source}").stdout)["tree"]["sha"]
    blob = json.loads(api_write("POST", f"repos/{REPOSITORY}/git/blobs", {
        "content": base64.b64encode(encoded).decode("ascii"), "encoding": "base64",
    }).stdout)["sha"]
    tree = json.loads(api_write("POST", f"repos/{REPOSITORY}/git/trees", {
        "base_tree": base_tree, "tree": [{"path": "catalog.json", "mode": "100644", "type": "blob", "sha": blob}],
    }).stdout)["sha"]
    commit = json.loads(api_write("POST", f"repos/{REPOSITORY}/git/commits", {
        "message": "Publish verified assistant catalog", "tree": tree, "parents": [source],
    }).stdout)["sha"]
    result = api_write("PATCH", f"repos/{REPOSITORY}/git/refs/heads/main", {"sha": commit, "force": False}, check=False)
    if result.returncode:
        non_fast_forward = re.search(r"(?:not a fast[ -]forward|non[ -]fast[ -]forward)",
                                     result.stdout + "\n" + result.stderr, re.IGNORECASE)
        if status(result) in {409, 422} and non_fast_forward and main_head() != source:
            print("main advanced during publication; atomic fast-forward rejected, catalog was not changed")
            return False
        # In particular, never report permission/authentication or branch-policy errors as a harmless race.
        raise GitHubError(result)
    print("Published catalog after verifying all release assets")
    return True


if __name__ == "__main__":
    publish(Path(__file__).resolve().parents[1])
