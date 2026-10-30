"""Step 5: settings persistence. Switch states are never restored; names only feed a text hint."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import runtime_paths
import settings as S


class SettingsFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'sub' / 'settings.json'

    def write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(data if isinstance(data, str) else json.dumps(data), encoding='utf8')

    def test_missing_file_gives_defaults(self):
        s = S.load(self.path)
        self.assertEqual((s.values, s.last_switches, s.read_only, s.notice), (S.DEFAULTS, [], False, ''))

    def test_round_trip(self):
        values = {'attack': '3.5', 'incoming': '0.5', 'movement': '2', 'cooldown': '0.3'}
        self.assertTrue(S.save(self.path, values, ['auto_shachi', 'floor_lock'], '0.13.1'))
        s = S.load(self.path)
        self.assertEqual(s.values, values)
        self.assertEqual(s.last_switches, ['floor_lock', 'auto_shachi'])

    def test_file_has_no_window_or_switch_state_fields(self):
        S.save(self.path, dict(S.DEFAULTS), ['floor_lock'])
        data = json.loads(self.path.read_text(encoding='utf8'))
        self.assertEqual(set(data), {'schema', 'app_version', 'values', 'last_switches'})
        self.assertEqual(set(data['values']), set(S.DEFAULTS))
        self.assertNotIn('window', data)
        self.assertNotIn('geometry', json.dumps(data))

    def test_corrupt_file_is_quarantined(self):
        for text in ('{oops', '[1, 2]', json.dumps({'values': {}}), json.dumps({'schema': '1'})):
            self.write(text)
            s = S.load(self.path)
            self.assertEqual(s.values, S.DEFAULTS, text)
            self.assertFalse(s.read_only)
            self.assertIn('损坏', s.notice)
            self.assertFalse(self.path.exists())
            backups = list(self.path.parent.glob('settings.json.bad-*'))
            self.assertTrue(backups)
            for b in backups:
                b.unlink()

    def test_newer_schema_is_read_only_defaults(self):
        self.write({'schema': S.SCHEMA + 1, 'values': {'attack': '5'}})
        s = S.load(self.path)
        self.assertTrue(s.read_only)
        self.assertEqual(s.values, S.DEFAULTS)
        self.assertTrue(self.path.exists())

    def test_invalid_single_values_fall_back_individually(self):
        for bad in ('999', 'abc', 'nan', 'inf', '-inf', '0', 5, None):
            self.write({'schema': 1, 'values': {'attack': bad, 'incoming': '3'}})
            s = S.load(self.path)
            self.assertEqual(s.values['attack'], '2.0', bad)
            self.assertEqual(s.values['incoming'], '3')

    def test_bounds_are_inclusive(self):
        for key, (low, high) in S.RANGES.items():
            self.assertTrue(S.valid_value(key, str(low)) and S.valid_value(key, str(high)), key)
            self.assertFalse(S.valid_value(key, str(high + 0.01)), key)

    def test_switch_keys_in_file_are_ignored(self):
        self.write({'schema': 1, 'floor_lock': True, 'teleport_unlocked': True,
                    'values': {'floor_lock': True, 'one_hit': '1'}, 'switches': {'auto_shachi': True},
                    'last_switches': ['floor_lock', 'unknown', 3, 'teleport_unlocked']})
        s = S.load(self.path)
        self.assertEqual(s.values, S.DEFAULTS)
        self.assertEqual(s.last_switches, ['floor_lock', 'teleport_unlocked'])
        self.assertFalse(hasattr(s, 'switches'))

    def test_hint_is_plain_text_only(self):
        self.assertEqual(S.hint_text([]), '')
        self.assertEqual(S.hint_text('floor_lock'), '')
        text = S.hint_text(['auto_shachi', 'floor_lock', 'bogus'])
        self.assertIsInstance(text, str)
        self.assertTrue(text.startswith('上次开过：强锁血、锁杀气'))
        self.assertIn('未开启', text)

    def test_atomic_write_keeps_old_file_on_failure(self):
        S.save(self.path, {'attack': '4'})
        before = self.path.read_bytes()
        with mock.patch('settings.os.replace', side_effect=OSError('disk full')):
            self.assertFalse(S.save(self.path, {'attack': '9'}))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.path.with_name('settings.json.tmp').exists())

    def test_save_never_writes_invalid_values(self):
        S.save(self.path, {'attack': '1e9', 'movement': 'x'})
        self.assertEqual(json.loads(self.path.read_text(encoding='utf8'))['values'], S.DEFAULTS)

    def test_unreadable_file_is_read_only(self):
        self.write({'schema': 1})
        with mock.patch.object(Path, 'read_text', side_effect=PermissionError('denied')):
            s = S.load(self.path)
        self.assertTrue(s.read_only and s.notice)

    def test_switch_labels_match_panel_switches(self):
        text = (Path(__file__).resolve().parent / 'app.py').read_text(encoding='utf-8')
        for name in S.SWITCH_LABELS:
            self.assertIn(f'self.{name} = tk.BooleanVar(value=False)', text, name)


class SettingsPath(unittest.TestCase):
    def test_source_frozen_and_override(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('PBZ_TRAINER_SETTINGS', None)
            self.assertEqual(runtime_paths.settings_path(), runtime_paths.RESOURCE_HOME / 'settings.json')
            with tempfile.TemporaryDirectory() as tmp, mock.patch.object(runtime_paths.sys, 'frozen', True, create=True):
                with mock.patch.dict(os.environ, {'LOCALAPPDATA': tmp}):
                    self.assertEqual(runtime_paths.settings_path(), Path(tmp) / 'PBZTrainer' / 'settings.json')
                with mock.patch.dict(os.environ, {'LOCALAPPDATA': 'relative'}):
                    self.assertEqual(runtime_paths.settings_path(),
                                     Path.home() / 'AppData' / 'Local' / 'PBZTrainer' / 'settings.json')
            with mock.patch.dict(os.environ, {'PBZ_TRAINER_SETTINGS': '/x/y.json'}):
                self.assertEqual(runtime_paths.settings_path(), Path('/x/y.json'))


if __name__ == '__main__':
    unittest.main()
