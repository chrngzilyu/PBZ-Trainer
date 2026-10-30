"""Guards for the mixin split of core.Game (step 2)."""
import ast
import unittest
from pathlib import Path

import core
from core import Game
from memory import ProcessMixin
from ue import UEMixin
from writer import WriterMixin
from features.targets import TargetsMixin
from features.modifiers import ModifiersMixin
from features.resources import ResourcesMixin

ROOT = Path(__file__).resolve().parent
MIXINS = (ProcessMixin, UEMixin, WriterMixin, TargetsMixin, ModifiersMixin, ResourcesMixin)
# Allowed direction: win32 -> memory -> ue -> writer -> features -> core
LAYERS = ['win32', 'memory', 'ue', 'writer', 'features', 'core']
FILES = {'win32': ['win32.py'], 'memory': ['memory.py'], 'ue': ['ue.py'], 'writer': ['writer.py'],
         'features': ['features/targets.py', 'features/modifiers.py', 'features/resources.py'], 'core': ['core.py']}


def imported_layers(path):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    found = set()
    for node in ast.walk(tree):
        names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
            [node.module or ''] if isinstance(node, ast.ImportFrom) else []
        for name in names:
            top = name.split('.')[0]
            if top in LAYERS:
                found.add(top)
    return found


class MixinStructure(unittest.TestCase):
    def test_game_is_assembled_from_mixins(self):
        self.assertEqual(Game.__bases__, MIXINS)

    def test_mixin_methods_do_not_overlap(self):
        seen = {}
        for mixin in MIXINS + (Game,):
            names = {n for n, v in vars(mixin).items() if callable(v) and not n.startswith('__')}
            for name in names:
                self.assertNotIn(name, seen, f'{name} defined in both {seen.get(name)} and {mixin.__name__}')
                seen[name] = mixin.__name__

    def test_layers_only_import_downwards(self):
        for layer, files in FILES.items():
            rank = LAYERS.index(layer)
            for path in files:
                for other in imported_layers(path) - {layer}:
                    self.assertLess(LAYERS.index(other), rank, f'{path} imports higher layer {other}')

    def test_core_reexports(self):
        for name in ('api', 'open_process', 'close_handle', 'image_path', 'wpm', 'rpm', 'snapshot',
                     'discover', 'ProcessEntry', 'DataWriteError', 'HOME', 'K', 'Game'):
            self.assertTrue(hasattr(core, name), name)

    def test_public_methods_still_on_game(self):
        for name in ('close', 'dust_state', 'set_dust', 'modifier', 'refill', 'refill_ultimate', 'restore_modifiers',
                     'restore_movement', 'restore_record', 'status', 'start_one_hit', 'stop_one_hit',
                     'start_floor_lock', 'stop_floor_lock', 'check_floor_lock', '_apply_attack', 'patch', 'log',
                     'context', 'read', 'token', 'targets', 'alive', 'backpack', 'class_name', 'map_rows', 'name',
                     'obj', 'property', 'qword', 'refresh_pool', 'raw_write',
                     'start_teleport_unlock', 'pulse_teleport_unlock', 'stop_teleport_unlock') + tuple(
                f'{op}_auto_{kind}' for op in ('start', 'stop', 'pulse')
                for kind in ('health', 'shachi', 'spirit_shadow', 'ultimate', 'medicine')):
            self.assertTrue(callable(getattr(Game, name, None)), name)

    def test_build_inputs_list_new_modules(self):
        text = (ROOT / 'build_exe.py').read_text(encoding='utf-8')
        for path in ['offsets.py'] + [p for files in FILES.values() for p in files] + ['features/__init__.py']:
            self.assertIn(f"'{path}'", text, path)


if __name__ == '__main__':
    unittest.main()
