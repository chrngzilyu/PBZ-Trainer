"""User settings persistence (fork step 5). Pure Python, no Tk.

Only the four numeric inputs are restored, and only into the input boxes; the user must
still press "apply" before anything is written to the game. Switch names from the last
session are kept *only* to build a text hint. Nothing in this module can turn a switch on:
``load`` never returns switch states, and ``hint_text`` returns a plain string.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import time

SCHEMA = 1
DEFAULTS = {'attack': '2.0', 'incoming': '1.0', 'movement': '1.5', 'cooldown': '0.1'}
# Same ranges as the Spinboxes in app.py (the self-check asserts they match).
RANGES = {'attack': (0.1, 20.0), 'incoming': (0.1, 20.0), 'movement': (1.0, 5.0), 'cooldown': (0.1, 2.0)}
# Switch variable name -> label used in the hint. Order = display order.
SWITCH_LABELS = {'floor_lock': '强锁血', 'auto_health': '自动回血', 'auto_shachi': '锁杀气',
                 'auto_spirit_shadow': '锁暗源', 'auto_gourd': '锁葫芦', 'auto_wine': '锁酒',
                 'auto_ultimate': '锁精纯杀气', 'one_hit': '一键秒杀', 'teleport_unlocked': '随地传送解锁'}


@dataclass
class Settings:
    values: dict = field(default_factory=lambda: dict(DEFAULTS))
    last_switches: list = field(default_factory=list)
    read_only: bool = False
    notice: str = ''


def valid_value(key, text):
    if key not in RANGES or not isinstance(text, str):
        return False
    try:
        number = float(text)
    except ValueError:
        return False
    low, high = RANGES[key]
    return math.isfinite(number) and low <= number <= high


def clean_switches(names):
    if not isinstance(names, list):
        return []
    return [name for name in SWITCH_LABELS if name in names]


def hint_text(names):
    labels = [SWITCH_LABELS[name] for name in clean_switches(names)]
    if not labels:
        return ''
    return '上次开过：' + '、'.join(labels) + '（仅提示，本次均未开启，需要时请手动打开）'


def _quarantine(path):
    try:
        target = path.with_name(path.name + time.strftime('.bad-%Y%m%d-%H%M%S'))
        os.replace(path, target)
        return target.name
    except OSError:
        return ''


def load(path):
    path = Path(path)
    try:
        text = path.read_text(encoding='utf8')
    except FileNotFoundError:
        return Settings()
    except (OSError, UnicodeDecodeError) as exc:
        return Settings(read_only=True, notice='设置文件读取失败，本次使用默认值：' + str(exc))
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, dict) or type(data.get('schema')) is not int:
        backup = _quarantine(path)
        return Settings(notice='设置文件损坏，已改用默认值' + ('，原文件改名为 ' + backup if backup else ''))
    if data['schema'] > SCHEMA:
        return Settings(read_only=True, notice='设置文件来自更新的版本，本次使用默认值且不覆盖它')
    settings = Settings()
    values = data.get('values')
    if isinstance(values, dict):
        for key in DEFAULTS:
            if valid_value(key, values.get(key)):
                settings.values[key] = values[key]
    settings.last_switches = clean_switches(data.get('last_switches'))
    return settings


def save(path, values, last_switches=(), app_version=''):
    """Atomically write the whitelisted values; returns False on IO failure."""
    path = Path(path)
    data = {'schema': SCHEMA, 'app_version': app_version,
            'values': {key: values[key] if valid_value(key, values.get(key)) else DEFAULTS[key] for key in DEFAULTS},
            'last_switches': clean_switches(list(last_switches))}
    tmp = path.with_name(path.name + '.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open('w', encoding='utf8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
