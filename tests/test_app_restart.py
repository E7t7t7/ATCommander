from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from atcommander.app_restart import run_application,launch_commander


class AppRestartTests(unittest.TestCase):
    def test_new_launcher_starts_only_after_old_app_finished_and_lock_released(self):
        steps=[];lock=SimpleNamespace(close=lambda:steps.append('lock released'))
        def main():steps.extend(['finished battle','saved queue','closed browser']);return 75
        code=run_application('/synthetic',main,lock,launch=lambda root:steps.append('new launcher'))
        self.assertEqual(code,0)
        self.assertEqual(steps,['finished battle','saved queue','closed browser','lock released','new launcher'])

    def test_normal_exit_does_not_restart(self):
        steps=[]
        self.assertEqual(run_application('/synthetic',lambda:0,SimpleNamespace(close=lambda:steps.append('closed')),launch=lambda _:steps.append('restart')),0)
        self.assertEqual(steps,['closed'])

    def test_failed_application_releases_lock_without_restart(self):
        steps=[]
        def fail():raise RuntimeError('synthetic')
        with self.assertRaises(RuntimeError):run_application('/synthetic',fail,SimpleNamespace(close=lambda:steps.append('closed')),launch=lambda _:steps.append('restart'))
        self.assertEqual(steps,['closed'])

    def test_native_starter_is_launched_directly_without_a_command_shell(self):
        with TemporaryDirectory() as folder:
            (Path(folder)/'AT Commander.exe').write_bytes(b'synthetic fixture')
            with patch('atcommander.app_restart.subprocess.Popen')as start:
                launch_commander(folder)
            self.assertEqual(start.call_args.args[0],[str(Path(folder)/'AT Commander.exe')])
            self.assertNotIn('shell',start.call_args.kwargs)
            self.assertNotEqual(start.call_args.kwargs['creationflags'],0)
