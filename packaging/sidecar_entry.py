"""Standalone PyInstaller entrypoint for the AI Work Tracker desktop sidecar."""
import multiprocessing

from awt.cli import cli

if __name__ == "__main__":
    multiprocessing.freeze_support()
    cli()
