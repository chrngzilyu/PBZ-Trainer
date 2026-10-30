"""Step 5: Panel-side settings glue, using fake Tk variables (no window needed)."""
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

import settings as S

try:
    import app
except ImportError:  # tkinter missing (non-Windows dev box); CI runs these
    app = None


class Var:
    def __init__(self, value):
        self.value, self.sets = value, 0

    def get(self):
        return self.value

    def set(self, value):
        self.value, self.sets = value, self.sets + 1


def fake_panel(persist):
    p = types.SimpleNamespace(persist=persist, settings_read_only=False, settings_save_warned=False,
                              used_switches=set(), last_switch_hint=Var(''), issues=mock.Mock())
    for key, value in S.DEFAULTS.items():
        setattr(p, key, Var(value))
    for name in S.SWITCH_LABELS:
        setattr(p, name, Var(False))
    return p


@unittest.skipIf(app is None, 'tkinter not available')
class PanelSettings(unittest.TestCase):
    def test_apply_settings_never_touches_switches(self):
        p = fake_panel(True)
        loaded = S.Settings(values={'attack': '5', 'incoming': '2', 'movement': '3', 'cooldown': '1'},
                            last_switches=list(S.SWITCH_LABELS))
        app.Panel.apply_settings(p, loaded)
        for name in S.SWITCH_LABELS:
            self.assertIs(getattr(p, name).get(), False, name)
            self.assertEqual(getattr(p, name).sets, 0, name)
        self.assertEqual(p.attack.get(), '5')
        self.assertIn('随地传送解锁', p.last_switch_hint.get())

    def test_used_switches_recorded_only_when_on(self):
        p = fake_panel(True)
        p.floor_lock.set(True)
        app.Panel.note_switch(p, 'floor_lock')
        app.Panel.note_switch(p, 'auto_shachi')
        self.assertEqual(p.used_switches, {'floor_lock'})

    def test_save_respects_persist_and_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'settings.json'
            with mock.patch.dict(os.environ, {'PBZ_TRAINER_SETTINGS': str(path)}):
                app.Panel.save_settings(fake_panel(False))
                self.assertFalse(path.exists())
                ro = fake_panel(True)
                ro.settings_read_only = True
                app.Panel.save_settings(ro)
                self.assertFalse(path.exists())
                p = fake_panel(True)
                p.attack.set('7')
                p.used_switches.add('one_hit')
                app.Panel.save_settings(p)
                loaded = S.load(path)
                self.assertEqual((loaded.values['attack'], loaded.last_switches), ('7', ['one_hit']))

    def test_save_failure_reports_once(self):
        p = fake_panel(True)
        with mock.patch('settings.save', return_value=False):
            app.Panel.save_settings(p)
            app.Panel.save_settings(p)
        self.assertEqual(p.issues.report.call_count, 1)

    def test_self_check_and_preview_disable_persistence(self):
        text = (Path(__file__).resolve().parent / 'app.py').read_text(encoding='utf-8')
        self.assertIn('persist=not (args.self_check or args.preview)', text)


if __name__ == '__main__':
    unittest.main()
