import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

from atcommander import launcher_bootstrap as bootstrap


class LauncherBootstrapTests(unittest.TestCase):
    def test_native_launcher_uses_windows_gui_subsystem(self):
        raw = (Path(__file__).resolve().parent.parent / 'AT Commander.exe').read_bytes()
        pe_offset = struct.unpack_from('<I', raw, 0x3c)[0]
        self.assertEqual(raw[:2], b'MZ')
        self.assertEqual(raw[pe_offset:pe_offset + 4], b'PE\0\0')
        self.assertEqual(struct.unpack_from('<H', raw, pe_offset + 24 + 68)[0], 2)

    def test_every_python_child_has_no_window_and_hidden_startup_flags(self):
        options = bootstrap.hidden_process_options()
        self.assertEqual(options['creationflags'], bootstrap.subprocess.CREATE_NO_WINDOW)
        self.assertTrue(options['startupinfo'].dwFlags & bootstrap.subprocess.STARTF_USESHOWWINDOW)
        self.assertEqual(options['startupinfo'].wShowWindow, bootstrap.subprocess.SW_HIDE)

    def test_launch_prefers_pythonw_and_keeps_console_streams_out_of_ui(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bootstrap, 'ROOT', Path(directory)), \
             patch.object(bootstrap, 'PYW', Mock(exists=lambda: True, __str__=lambda _self: 'fixture-pythonw.exe')), \
             patch.object(bootstrap.subprocess, 'Popen') as popen:
            bootstrap.launch_app()
        args, options = popen.call_args
        self.assertEqual(args[0][0], 'fixture-pythonw.exe')
        self.assertEqual(options['creationflags'], bootstrap.subprocess.CREATE_NO_WINDOW)
        self.assertEqual(options['stdin'], bootstrap.subprocess.DEVNULL)
        self.assertEqual(options['stdout'], bootstrap.subprocess.DEVNULL)

    def test_marker_is_invalidated_when_requirements_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requirements = root / 'requirements.txt'
            marker = root / '.atc_ready'
            requirements.write_bytes(b'requests>=2.32\n')
            marker.write_text(hashlib.sha256(requirements.read_bytes()).hexdigest(), encoding='utf-8')
            with patch.object(bootstrap, 'ROOT', root), patch.object(bootstrap, 'MARKER', marker), \
                 patch.object(bootstrap, 'PY', Mock(exists=lambda: True)), patch.object(bootstrap, 'PYW', Mock(exists=lambda: True)):
                self.assertTrue(bootstrap.runtime_ready())
                requirements.write_bytes(b'requests>=2.33\n')
                self.assertFalse(bootstrap.runtime_ready())
                marker.write_text('1.6', encoding='utf-8')
                self.assertFalse(bootstrap.runtime_ready())

    def test_setup_error_callback_keeps_error_text_after_exception_scope(self):
        callbacks = []
        setup = bootstrap.Bootstrap.__new__(bootstrap.Bootstrap)
        setup.root = Mock(after=lambda _delay, callback: callbacks.append(callback))
        setup.bar = Mock()
        setup.status = Mock()
        setup.log = Mock()
        setup.run = Mock(side_effect=RuntimeError('Synthetic setup failure'))
        with patch.object(bootstrap, 'PY', Mock(exists=lambda: False)), patch.object(bootstrap.messagebox, 'showerror') as error:
            setup.work()
            callbacks[-1]()
        self.assertIn('Synthetic setup failure', error.call_args[0][1])


if __name__ == '__main__':
    unittest.main()
