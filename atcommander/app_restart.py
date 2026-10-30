"""Reopen through normal setup only after the old app releases its runtime lock."""
from pathlib import Path
import subprocess
import sys

RESTART_EXIT=75


def run_application(root,main,application_lock,*,launch=None):
    try:code=main()
    finally:application_lock.close()
    if code==RESTART_EXIT:
        (launch or launch_commander)(root)
        return 0
    return code


def launch_commander(root):
    from .launcher_bootstrap import hidden_process_options
    root=Path(root).resolve()
    starter=root/'AT Commander.exe'
    command=[str(starter)] if starter.is_file() else [sys.executable,str(root/'bootstrap.pyw')]
    return subprocess.Popen(command,cwd=root,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,**hidden_process_options())
