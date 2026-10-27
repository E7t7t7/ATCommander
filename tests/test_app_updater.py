import hashlib
import io
import json
import multiprocessing
from pathlib import Path
import stat
import re
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from atcommander import app_updater as updater


REVISION = "a" * 40


def digest(content): return hashlib.sha256(content).hexdigest()


def bundle(extra=None):
    files = {name: b"# public source\n" for name in updater.REQUIRED}
    files["requirements.txt"] = b"requests>=2.32\n"
    files.update(extra or {})
    manifest = {"schema": 1, "repository": updater.REPOSITORY, "version": "1.7.26",
                "files": {name: digest(data) for name, data in files.items()}}
    return files, manifest


def zip_bytes(files, entries=()):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("ATCommander-test/", b"")
        for name, data in files.items(): archive.writestr("ATCommander-test/" + name, data)
        for name, data in entries: archive.writestr(name, data)
    return output.getvalue()


def fake_fetch(files, manifest, calls=None, archive=None):
    def fetch(url, limit, stop):
        if calls is not None: calls.append(url)
        if "/commits/" in url: return json.dumps({"sha": REVISION}).encode()
        if "raw.githubusercontent.com" in url: return json.dumps(manifest).encode()
        if "codeload.github.com" in url: return archive or zip_bytes(files)
        raise AssertionError("Unexpected source")
    return fetch


def check_lock(root, result):
    try:
        with updater.ApplicationLock(root): pass
        result.put("unlocked")
    except updater.UpdateError:
        result.put("locked")


class AppUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.files, self.manifest = bundle()

    def prepare(self, **kwargs):
        return updater.prepare_update(self.root, fetch=fake_fetch(self.files, self.manifest), **kwargs)

    def write_live(self):
        for name in self.files:
            target = self.root / name; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"# previous working source\n")

    def test_download_is_commit_pinned_and_does_not_touch_running_source(self):
        self.write_live(); calls = []
        result = updater.prepare_update(self.root, fetch=fake_fetch(self.files, self.manifest, calls))
        self.assertTrue(result["ready"])
        self.assertEqual((self.root / "app.py").read_bytes(), b"# previous working source\n")
        self.assertIn(f"/{REVISION}/commander-update.json", calls[1])
        self.assertTrue(calls[2].endswith("/zip/" + REVISION))
        self.assertTrue((self.root / updater.STATE / "pending.json").exists())

    def test_startup_install_preserves_private_runtime_and_backups(self):
        self.write_live()
        private = self.root / ".atcommander" / "settings.json"
        private.parent.mkdir(); private.write_bytes(b"private local preferences")
        self.prepare()
        with updater.ApplicationLock(self.root): self.assertTrue(updater.apply_pending_update(self.root))
        self.assertEqual(private.read_bytes(), b"private local preferences")
        for name, data in self.files.items(): self.assertEqual((self.root / name).read_bytes(), data)
        backups = list((self.root / updater.STATE).glob("*/backup/app.py"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b"# previous working source\n")
        self.assertFalse((self.root / updater.STATE / "pending.json").exists())

    def test_identical_files_are_current_without_archive_download(self):
        for name, data in self.files.items():
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        calls = []
        result = updater.prepare_update(self.root, fetch=fake_fetch(self.files, self.manifest, calls))
        self.assertFalse(result["ready"]); self.assertEqual(len(calls), 2)

    def test_same_display_version_with_changed_content_still_updates(self):
        self.write_live(); self.prepare()
        updater.apply_pending_update(self.root)
        self.files["app.py"] = b"# a later fix with the same version\n"
        self.manifest["files"]["app.py"] = digest(self.files["app.py"])
        self.assertTrue(self.prepare()["ready"])

    def test_bad_manifest_paths_cannot_target_account_or_system_files(self):
        for path in ["../settings.json", "C:/Windows/file.py", "/tmp/file.py", "atcommander\\app.py", "atcommander/../app.py", ".atcommander/settings.json", ".venv/site.py", "settings.json", "diagnostics/capture.json", "atcommander/NUL.py", "atcommander/app.py ", "atcommander/app.py:secret", "AT Commander.exe"]:
            with self.subTest(path=path):
                changed = json.loads(json.dumps(self.manifest)); changed["files"][path] = "b" * 64
                with self.assertRaises(updater.UpdateError): updater.validate_manifest(changed)

    def test_duplicate_windows_paths_are_rejected(self):
        self.manifest["files"]["atcommander/APP.py"] = "b" * 64
        with self.assertRaises(updater.UpdateError): self.prepare()

    def test_missing_required_file_is_rejected(self):
        del self.manifest["files"]["app.py"]
        with self.assertRaises(updater.UpdateError): self.prepare()

    def test_empty_or_unavailable_repository_has_safe_error(self):
        with self.assertRaisesRegex(updater.UpdateError, "no usable"):
            updater.prepare_update(self.root, fetch=lambda *args: b"{}")

    def test_invalid_commit_cannot_be_used_as_a_download_path(self):
        with self.assertRaisesRegex(updater.UpdateError, "invalid revision"):
            updater.prepare_update(self.root, fetch=lambda *args: b'{"sha":"../main"}')

    def test_bad_hash_never_sets_pending_and_preserves_prepared_update(self):
        self.prepare(); before = (self.root / updater.STATE / "pending.json").read_bytes()
        self.files["app.py"] = b"# content changed without manifest\n"
        with self.assertRaises(updater.UpdateError): self.prepare()
        self.assertEqual((self.root / updater.STATE / "pending.json").read_bytes(), before)

    def test_syntax_error_does_not_prepare_unloadable_program(self):
        self.files["app.py"] = b"this is invalid Python !\n"
        self.manifest["files"]["app.py"] = digest(self.files["app.py"])
        with self.assertRaisesRegex(updater.UpdateError, "source file is invalid"):
            self.prepare()

    def test_traversal_entries_rejected_even_if_not_in_manifest(self):
        bad = zip_bytes(self.files, [("ATCommander-test/../outside.py", b"# nope")])
        with self.assertRaises(updater.UpdateError):
            updater.prepare_update(self.root, fetch=fake_fetch(self.files, self.manifest, archive=bad))
        self.assertFalse((self.root / updater.STATE / "pending.json").exists())

    def test_symlink_zip_entries_are_rejected(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            entry = zipfile.ZipInfo("ATCommander-test/app.py")
            entry.create_system = 3; entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, b"outside.py")
        with self.assertRaises(updater.UpdateError):
            updater._extract(stream.getvalue(), self.manifest, self.root, lambda: False)

    def test_missing_archive_member_rejected(self):
        broken = dict(self.files); del broken["app.py"]
        with self.assertRaises(updater.UpdateError):
            updater.prepare_update(self.root, fetch=fake_fetch(self.files, self.manifest, archive=zip_bytes(broken)))

    def test_cancellation_leaves_current_app_and_previous_pending_untouched(self):
        self.write_live(); self.prepare()
        pending = (self.root / updater.STATE / "pending.json").read_bytes()
        with self.assertRaisesRegex(updater.UpdateError, "cancelled"):
            self.prepare(should_stop=lambda: True)
        self.assertEqual((self.root / "app.py").read_bytes(), b"# previous working source\n")
        self.assertEqual((self.root / updater.STATE / "pending.json").read_bytes(), pending)

    def test_tampered_stage_is_not_installed(self):
        self.write_live(); self.prepare()
        ticket = json.loads((self.root / updater.STATE / "pending.json").read_text())["ticket"]
        (self.root / updater.STATE / ticket / "app.py").write_bytes(b"tampered")
        with self.assertRaises(updater.UpdateError): updater.apply_pending_update(self.root)
        self.assertEqual((self.root / "app.py").read_bytes(), b"# previous working source\n")

    def test_partial_replacement_rolls_back_every_file(self):
        self.write_live(); self.prepare(); original = updater._replace; calls = []
        def fail_once(source, target):
            calls.append(target)
            if len(calls) == 3: raise PermissionError("Synthetic locked file")
            return original(source, target)
        with patch.object(updater, "_replace", side_effect=fail_once):
            with self.assertRaisesRegex(updater.UpdateError, "restored"):
                updater.apply_pending_update(self.root)
        for name in self.files: self.assertEqual((self.root / name).read_bytes(), b"# previous working source\n")
        self.assertFalse((self.root / updater.STATE / "transaction.json").exists())

    def test_process_interruption_restores_next_start_and_removes_new_files(self):
        self.write_live(); (self.root / "app.py").unlink(); self.prepare()
        original = updater._replace; calls = []
        def interrupt(source, target):
            original(source, target); calls.append(target)
            if len(calls) == 3: raise KeyboardInterrupt()
        with patch.object(updater, "_replace", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt): updater.apply_pending_update(self.root)
        self.assertTrue((self.root / updater.STATE / "transaction.json").exists())
        self.assertFalse(updater.apply_pending_update(self.root))
        self.assertFalse((self.root / "app.py").exists())
        for name in self.files:
            if name != "app.py": self.assertEqual((self.root / name).read_bytes(), b"# previous working source\n")

    def test_local_changes_after_crash_are_retained_instead_of_overwritten(self):
        self.write_live(); self.prepare()
        original = updater._replace
        def interrupt(source, target): original(source, target); raise KeyboardInterrupt()
        with patch.object(updater, "_replace", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt): updater.apply_pending_update(self.root)
        (self.root / "app.py").write_bytes(b"# edited after interruption\n")
        with self.assertRaisesRegex(updater.UpdateError, "changed during"):
            updater.apply_pending_update(self.root)
        self.assertEqual((self.root / "app.py").read_bytes(), b"# edited after interruption\n")

    def test_existing_unknown_files_are_preserved(self):
        (self.root / "user_notes.txt").write_text("keep")
        self.prepare(); updater.apply_pending_update(self.root)
        self.assertEqual((self.root / "user_notes.txt").read_text(), "keep")

    def test_runtime_lock_blocks_a_separate_process(self):
        context = multiprocessing.get_context("spawn"); result = context.Queue()
        with updater.ApplicationLock(self.root):
            process = context.Process(target=check_lock, args=(str(self.root), result))
            process.start(); process.join(10)
            self.assertFalse(process.is_alive()); self.assertEqual(result.get(timeout=3), "locked")
        with updater.ApplicationLock(self.root): pass

    def test_startup_releases_lock_when_install_fails(self):
        with patch.object(updater, "apply_pending_update", side_effect=updater.UpdateError("test")):
            with self.assertRaises(updater.UpdateError): updater.startup(self.root)
        with updater.ApplicationLock(self.root): pass

    def test_network_hosts_and_redirects_are_limited(self):
        for url in ["http://api.github.com/x", "https://evil.example/x", "https://api.github.com.evil/x", "https://user:password@api.github.com/x", "https://api.github.com:444/x"]:
            self.assertFalse(updater._url_allowed(url))
        self.assertTrue(updater._url_allowed("https://raw.githubusercontent.com/E7t7t7/ATCommander/main/commander-update.json"))

    def test_public_fixtures_store_only_one_way_signature_answer_digests(self):
        for name in ('public_combat_signing.json', 'public_first_free_mom.json'):
            value = json.loads((Path(__file__).parent / 'fixtures' / name).read_text(encoding='utf-8'))
            self.assertNotIn('expected', value)
            self.assertTrue(all(re.fullmatch(r'[a-f0-9]{64}', item) for item in value['expected_sha256'].values()))
            self.assertTrue(str(value.get('identity') or value.get('user_id')).startswith(('fixture-', 'synthetic-')))


if __name__ == "__main__": unittest.main()
