"""Separate bundled read-only assets from persistent user data."""
import os
from pathlib import Path
import sys

RESOURCE_HOME = Path(__file__).resolve().parent


def user_data_home():
    """Persistent per-user directory: %LOCALAPPDATA%\\PBZTrainer when frozen, the source folder otherwise."""
    if getattr(sys, 'frozen', False):
        local = os.environ.get('LOCALAPPDATA')
        base = Path(local) if local and Path(local).is_absolute() else Path.home() / 'AppData' / 'Local'
        return base / 'PBZTrainer'
    return RESOURCE_HOME


def log_directory():
    return user_data_home() / 'logs'


def hash_cache_path():
    return user_data_home() / 'hash-cache.json'


def settings_path():
    override = os.environ.get('PBZ_TRAINER_SETTINGS')
    if override:
        return Path(override)
    return user_data_home() / 'settings.json'
