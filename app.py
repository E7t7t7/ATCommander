if __name__ == "__main__":
    from pathlib import Path
    from atcommander.app_updater import startup, UpdateError
    try:
        _application_lock = startup(Path(__file__).resolve().parent, keep_lock=True)
    except UpdateError as error:
        from tkinter import messagebox
        messagebox.showerror("AT Commander", str(error))
        raise SystemExit(1)
    try:
        from atcommander.app import main
        from atcommander.app_restart import run_application
    except Exception:
        _application_lock.close()
        raise
    raise SystemExit(run_application(Path(__file__).resolve().parent,main,_application_lock))
