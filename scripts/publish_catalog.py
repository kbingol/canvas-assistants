"""Publish immutable release assets before advertising them in catalog.json."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

from build_catalog import REPOSITORY, build


def gh(*args, check=True):
    return subprocess.run(["gh", *args], check=check, capture_output=True, text=True)


def publish(root: Path):
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise RuntimeError("Publishing is restricted to the canonical repository")
    output = root / "dist"
    catalog = build(root, output)
    for entry in catalog["assistants"]:
        filename = entry["packageUrl"].rsplit("/", 1)[1]
        tag = entry["packageUrl"].split("/")[-2]
        release = gh("release", "view", tag, "--repo", REPOSITORY, "--json", "isDraft,isPrerelease", check=False)
        if release.returncode:
            gh("release", "create", tag, str(output / filename), "--repo", REPOSITORY,
               "--target", os.environ["GITHUB_SHA"],
               "--title", f"{entry['name']} {entry['version']}",
               "--notes", entry["description"], "--latest=false")
        else:
            state = json.loads(release.stdout)
            if state["isDraft"] or state["isPrerelease"]:
                raise RuntimeError(f"Release {tag} is not a public stable release")
        with tempfile.TemporaryDirectory() as directory:
            gh("release", "download", tag, "--repo", REPOSITORY, "--pattern", filename, "--dir", directory)
            downloaded = (Path(directory) / filename).read_bytes()
            if (len(downloaded) != entry["packageBytes"]
                    or hashlib.sha256(downloaded).hexdigest() != entry["sha256"]):
                raise RuntimeError(f"Published {tag} differs; increment the package version")

    current = gh("api", f"repos/{REPOSITORY}/contents/catalog.json", check=False)
    current_data = json.loads(current.stdout) if current.returncode == 0 else None
    encoded = (output / "catalog.json").read_bytes()
    if current_data and base64.b64decode(current_data["content"]) == encoded:
        print("Catalog is already current")
        return
    head = json.loads(gh("api", f"repos/{REPOSITORY}/git/ref/heads/main").stdout)["object"]["sha"]
    if head != os.environ["GITHUB_SHA"]:
        raise RuntimeError("main advanced during publication; rerun on the current main")
    request = {
        "message": "Publish verified assistant catalog", "branch": "main",
        "content": base64.b64encode(encoded).decode("ascii"),
    }
    if current_data:
        request["sha"] = current_data["sha"]
    with tempfile.TemporaryDirectory() as directory:
        body = Path(directory) / "catalog-request.json"
        body.write_text(json.dumps(request), encoding="utf-8")
        gh("api", "--method", "PUT", f"repos/{REPOSITORY}/contents/catalog.json", "--input", str(body))
    print("Published catalog after verifying all release assets")


if __name__ == "__main__":
    publish(Path(__file__).resolve().parents[1])
