"""Separate bundled read-only assets from persistent user data."""
import os
from pathlib import Path
import sys

RESOURCE_HOME = Path(__file__).resolve().parent


def log_directory():
    if getattr(sys, 'frozen', False):
        local = os.environ.get('LOCALAPPDATA')
        base = Path(local) if local and Path(local).is_absolute() else Path.home() / 'AppData' / 'Local'
        return base / 'PBZTrainer' / 'logs'
    return RESOURCE_HOME / 'logs'
