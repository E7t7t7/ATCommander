"""Opt-in GitHub source updates. No account data, shell, or game requests.

Downloads are pinned to one commit. Installation occurs under an exclusive
application lock, before importing the GUI, with a recoverable file journal.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import uuid
import zipfile
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler, Request
from urllib.parse import urlsplit

REPOSITORY = "E7t7t7/ATCommander"
BRANCH = "main"
MANIFEST = "commander-update.json"
STATE = ".commander-updates"
MAX_DOWNLOAD = 64 * 1024 * 1024
MAX_EXPANDED = 128 * 1024 * 1024
MAX_FILE = 12 * 1024 * 1024
ROOT_FILES = {"app.py", "bootstrap.pyw", "Start_AT_Commander.vbs", "run.bat",
              "Start_CM_Discord_Bot.bat", "requirements.txt", "requirements-discord.txt",
              "README.md", "COMMAND_REFERENCE.md", "BENDER_PARITY.md",
              "DISCORD_CM_BOT_SETUP.md", "build_launcher.ps1", "GITHUB_UPDATES.md", ".gitignore"}
REQUIRED = {"app.py", "bootstrap.pyw", "requirements.txt", "atcommander/app.py",
            "atcommander/app_updater.py", "atcommander/launcher_bootstrap.py"}


class UpdateError(Exception):
    pass


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def public_path(value):
    """Narrow public-source allowlist, including Windows path semantics."""
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        return False
    p = PurePosixPath(value)
    if p.is_absolute() or str(p) != value or any(x in {".", ".."} for x in p.parts):
        return False
    if any(not re.fullmatch(r"[A-Za-z0-9_. -]+", x) or x.endswith((".", " ")) or
           x.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)], *[f"LPT{i}" for i in range(10)]}
           for x in p.parts):
        return False
    return (value in ROOT_FILES or
            bool(re.fullmatch(r"README_1\.7(?:\.\d+)?_CHANGES\.md", value)) or
            bool(re.fullmatch(r"build_release_[0-9.]+\.ps1", value)) or
            (len(p.parts) == 2 and p.parts[0] in {"atcommander", "tests"} and p.suffix == ".py") or
            value == "launcher/ATCommanderLauncher.cs" or
            value == "tools/make_update_manifest.py" or
            value in {"tests/fixtures/public_combat_signing.json", "tests/fixtures/public_first_free_mom.json", "tests/native_research_ui_fixture.js"})


def validate_manifest(raw):
    if not isinstance(raw, dict) or set(raw) != {"schema", "repository", "version", "files"}:
        raise UpdateError("The update manifest is not supported.")
    if raw["schema"] != 1 or raw["repository"] != REPOSITORY or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}(?:[.-][A-Za-z0-9]+)*", str(raw["version"])):
        raise UpdateError("The update manifest is not supported.")
    files = raw["files"]
    if not isinstance(files, dict) or not REQUIRED <= files.keys() or not 6 <= len(files) <= 1024:
        raise UpdateError("The update package is incomplete.")
    seen = set()
    for path, digest in files.items():
        if not public_path(path) or path.casefold() in seen or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise UpdateError("The update package contains an unsupported file.")
        seen.add(path.casefold())
    return raw


def _safe_path(root, relative):
    root = Path(root).resolve()
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise UpdateError("An update destination is unavailable.")
    for part in [path, *path.parents]:
        if part == root:
            break
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise UpdateError("An update destination is unavailable.")
    return path


def _json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".new")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def _load(path):
    path = Path(path)
    if path.stat().st_size > 1024 * 1024:
        raise UpdateError("The saved update record is invalid.")
    return json.loads(path.read_text(encoding="utf-8"))


def _cancel(should_stop):
    if should_stop():
        raise UpdateError("Update cancelled. Commander was not changed.")


def _url_allowed(url):
    p = urlsplit(url)
    return p.scheme == "https" and p.hostname in {"api.github.com", "raw.githubusercontent.com", "codeload.github.com"} and p.port in (None, 443) and not p.username and not p.password


class _Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _url_allowed(newurl):
            raise UpdateError("GitHub returned an unsupported download destination.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url, limit, should_stop):
    if not _url_allowed(url):
        raise UpdateError("Unsupported update source.")
    # No cookies, credential manager, environment proxy, or game session.
    opener = build_opener(ProxyHandler({}), _Redirects())
    request = Request(url, headers={"User-Agent": "ATCommander-Updater/1", "Accept": "application/vnd.github+json" if "api.github.com/" in url else "*/*"})
    try:
        with opener.open(request, timeout=30) as response:
            chunks = []; total = 0
            while True:
                _cancel(should_stop)
                chunk = response.read(128 * 1024)
                if not chunk:
                    return b"".join(chunks)
                total += len(chunk)
                if total > limit:
                    raise UpdateError("The update download is too large.")
                chunks.append(chunk)
    except UpdateError:
        raise
    except Exception:
        raise UpdateError("Could not reach the Commander repository. It may be empty, unavailable, or temporarily rate limited. Try again later.") from None


class ApplicationLock:
    """OS-owned lock; a crash releases it automatically."""
    def __init__(self, root, name="runtime.lock"):
        directory = _safe_path(root, STATE)
        directory.mkdir(exist_ok=True)
        self.stream = _safe_path(root, f"{STATE}/{name}").open("a+b")
        self.stream.seek(0); self.stream.write(b"0"); self.stream.flush(); self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise UpdateError("Commander is already open. Close the other copy before reopening it to install an update.") from None

    def close(self):
        if not self.stream.closed:
            if os.name == "nt":
                import msvcrt
                self.stream.seek(0); msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            self.stream.close()

    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def prepare_update(root=None, *, progress=lambda text: None, should_stop=lambda: False, fetch=None):
    """Download and validate only. Never change the running application's files."""
    root = Path(root or Path(__file__).resolve().parent.parent)
    fetch = fetch or _download
    with ApplicationLock(root, "download.lock"):
        state = _safe_path(root, STATE)
        if (state / "transaction.json").exists():
            raise UpdateError("Restart Commander to finish recovering its previous update first.")
        progress("Checking Commander on GitHub…")
        _cancel(should_stop)
        try:
            reference = json.loads(fetch(f"https://api.github.com/repos/{REPOSITORY}/git/ref/heads/{BRANCH}", 1024 * 1024, should_stop))["object"]
            if reference["type"] != "commit":
                raise UpdateError("The repository branch does not point to an update revision.")
            commit = reference["sha"]
        except UpdateError:
            raise
        except Exception:
            raise UpdateError("The repository has no usable Commander update yet.") from None
        if not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40}", commit):
            raise UpdateError("GitHub returned an invalid revision.")
        try:
            manifest = validate_manifest(json.loads(fetch(f"https://raw.githubusercontent.com/{REPOSITORY}/{commit}/{MANIFEST}", 1024 * 1024, should_stop)))
        except UpdateError:
            raise
        except Exception:
            raise UpdateError("This repository does not contain a supported Commander update.") from None
        # Compare actual public files, not just a version label or saved receipt.
        if all(_matches(_safe_path(root, path), digest) for path, digest in manifest["files"].items()):
            _cancel(should_stop)
            (state / "pending.json").unlink(missing_ok=True)
            return {"ready": False, "message": f"Commander {manifest['version']} is up to date.", "commit": commit}
        progress(f"Downloading Commander {manifest['version']}…")
        archive = fetch(f"https://codeload.github.com/{REPOSITORY}/zip/{commit}", MAX_DOWNLOAD, should_stop)
        ticket = uuid.uuid4().hex
        destination = state / ticket
        destination.mkdir()
        try:
            _extract(archive, manifest, destination, should_stop)
            _cancel(should_stop)
            _json(destination / MANIFEST, manifest)
            _json(state / "pending.json", {"schema": 1, "ticket": ticket, "commit": commit})
        except Exception:
            # Only remove the freshly allocated private staging directory.
            shutil.rmtree(destination)
            raise
        return {"ready": True, "message": f"Commander {manifest['version']} is ready. Close Commander after your active work finishes, then reopen it to install.", "commit": commit}


def _matches(path, digest):
    return path.is_file() and path.stat().st_size <= MAX_FILE and _hash(path.read_bytes()) == digest


def _extract(data, manifest, destination, should_stop):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 2048 or sum(x.file_size for x in entries) > MAX_EXPANDED:
                raise UpdateError("The update archive is too large.")
            found = {}; seen = set(); prefix = None
            for entry in entries:
                _cancel(should_stop)
                name = entry.filename
                if "\\" in name or ":" in name or name.startswith("/") or ".." in PurePosixPath(name).parts:
                    raise UpdateError("The update archive contains an unsafe path.")
                parts = name.split("/", 1)
                if len(parts) != 2 or not parts[0] or prefix not in (None, parts[0]):
                    raise UpdateError("The update archive layout is unsupported.")
                prefix = parts[0]
                if entry.is_dir(): continue
                relative = parts[1]
                if name.casefold() in seen or stat.S_ISLNK(entry.external_attr >> 16):
                    raise UpdateError("The update archive contains an unsafe entry.")
                seen.add(name.casefold())
                if relative not in manifest["files"]: continue
                if entry.file_size > MAX_FILE:
                    raise UpdateError("An update file is too large.")
                content = archive.read(entry)
                if _hash(content) != manifest["files"][relative]:
                    raise UpdateError("An update file failed its integrity check.")
                if relative.endswith((".py", ".pyw")):
                    try: compile(content, relative, "exec")
                    except (SyntaxError, ValueError):
                        raise UpdateError("An update source file is invalid.") from None
                target = _safe_path(destination, relative)
                target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(content)
                found[relative] = True
            if found.keys() != manifest["files"].keys():
                raise UpdateError("The update archive is incomplete.")
    except UpdateError:
        raise
    except Exception:
        raise UpdateError("The update archive could not be verified.") from None


def _replace(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".commander-new")
    shutil.copyfile(source, temp)
    os.replace(temp, target)


def _recover(root, state):
    journal = state / "transaction.json"
    if not journal.exists(): return
    record = _load(journal)
    ticket = record.get("ticket", "")
    if not re.fullmatch(r"[a-f0-9]{32}", ticket):
        raise UpdateError("The update recovery record is invalid.")
    backup = _safe_path(root, f"{STATE}/{ticket}/backup")
    entries = record.get("files")
    if not isinstance(entries, dict) or len(entries) > 1024:
        raise UpdateError("The update recovery record is invalid.")
    for relative, saved in entries.items():
        if not public_path(relative) or not isinstance(saved, dict) or set(saved) != {"before", "after"}:
            raise UpdateError("The update recovery record is invalid.")
        before, after = saved["before"], saved["after"]
        if not isinstance(after, str) or not re.fullmatch(r"[a-f0-9]{64}", after) or (before is not None and (not isinstance(before, str) or not re.fullmatch(r"[a-f0-9]{64}", before))):
            raise UpdateError("The update recovery record is invalid.")
        target = _safe_path(root, relative)
        source = _safe_path(backup, relative)
        if before is not None and not _matches(source, before):
            raise UpdateError("An update backup could not be verified. Your backup was retained.")
        if target.exists() and not (_matches(target, after) or before and _matches(target, before)):
            raise UpdateError("A file changed during update recovery. The backup was retained for inspection.")
    for relative, saved in entries.items():
        target = _safe_path(root, relative)
        if saved["before"] is None: target.unlink(missing_ok=True)
        else: _replace(_safe_path(backup, relative), target)
    journal.unlink()
    (state / "pending.json").unlink(missing_ok=True)


def apply_pending_update(root):
    """Caller must own ApplicationLock. Backup all files before first replacement."""
    root = Path(root)
    state = _safe_path(root, STATE)
    _recover(root, state)
    pending = state / "pending.json"
    if not pending.exists(): return False
    try:
        record = _load(pending)
        ticket = record["ticket"]; commit = record["commit"]
        if not re.fullmatch(r"[a-f0-9]{32}", ticket) or not re.fullmatch(r"[a-f0-9]{40}", commit):
            raise UpdateError("The pending update record is invalid.")
        staged = _safe_path(root, f"{STATE}/{ticket}")
        manifest = validate_manifest(_load(staged / MANIFEST))
        changes = {}
        for relative, digest in manifest["files"].items():
            source = _safe_path(staged, relative); target = _safe_path(root, relative)
            if not _matches(source, digest): raise UpdateError("A staged update file failed verification.")
            if _matches(target, digest): continue
            before = None
            if target.exists():
                if not target.is_file() or target.stat().st_size > MAX_FILE: raise UpdateError("An update destination is unavailable.")
                before = _hash(target.read_bytes())
                backup = _safe_path(staged / "backup", relative)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(target, backup)
                if not _matches(backup, before): raise UpdateError("An update backup failed verification.")
            changes[relative] = {"before": before, "after": digest}
        _json(state / "transaction.json", {"ticket": ticket, "files": changes})
        try:
            for relative in changes:
                _replace(_safe_path(staged, relative), _safe_path(root, relative))
            for relative, digest in manifest["files"].items():
                if not _matches(_safe_path(root, relative), digest): raise UpdateError("The installed update failed verification.")
            _json(state / "installed.json", {"commit": commit, "version": manifest["version"]})
            _json(root / MANIFEST, manifest)
        except Exception:
            _recover(root, state)
            raise UpdateError("The update could not be installed. Commander was restored from its backup; try checking for updates again.") from None
        (state / "transaction.json").unlink()
        pending.unlink(missing_ok=True)
        return True
    except UpdateError:
        raise
    except Exception:
        raise UpdateError("The pending update could not be installed. The saved files and backup were retained.") from None


def startup(root, *, keep_lock=False):
    """Standard-library startup hook shared by EXE/bootstrap and direct app.py."""
    lock = ApplicationLock(root)
    try:
        apply_pending_update(root)
    except Exception:
        lock.close()
        raise
    if keep_lock: return lock
    lock.close()

