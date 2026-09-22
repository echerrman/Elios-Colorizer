"""Separate entry point so PyInstaller can resolve the package imports."""
if __name__ == '__main__':
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
        from pathlib import Path
        import traceback
        try:
            from elios_colorizer.selftest import run
            run(sys.argv[2])
        except Exception:
            folder = Path(sys.argv[2])
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'selftest-error.txt').write_text(traceback.format_exc())
            raise SystemExit(1)
    else:
        from elios_colorizer.ui import main
        raise SystemExit(main())
