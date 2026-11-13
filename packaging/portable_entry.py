"""Standalone PyInstaller entrypoint for the AI Work Tracker pure-green Windows portable app."""
import multiprocessing
import sys

from awt.portable import run_portable

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(run_portable(sys.argv[1:]))
