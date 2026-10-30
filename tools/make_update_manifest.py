"""Generate the public source-update inventory; never traverse runtime folders."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from atcommander.app_updater import public_path, validate_manifest, REPOSITORY, MANIFEST
from atcommander import __version__


def build(root=ROOT, version=__version__):
    root = Path(root)
    candidates = [p for p in root.iterdir() if p.is_file()]
    for folder in ("atcommander", "tests", "launcher", "tools"):
        candidates += list((root / folder).glob("*"))
    candidates += list((root / "tests" / "fixtures").glob("*"))
    files = {}
    for path in sorted(candidates):
        relative = path.relative_to(root).as_posix()
        if path.is_file() and public_path(relative):
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = validate_manifest({"schema": 1, "repository": REPOSITORY, "version": version, "files": files})
    (root / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default=__version__)
    args = parser.parse_args()
    manifest = build(version=args.version)
    print(f"Verified public manifest: {len(manifest['files'])} files, version {manifest['version']}.")
