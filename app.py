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
        raise SystemExit(main())
    finally:
        _application_lock.close()
