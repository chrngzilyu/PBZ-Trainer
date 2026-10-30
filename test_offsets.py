import unittest

import offsets as o


class OffsetTable(unittest.TestCase):
    def test_attribute_current_identities(self):
        self.assertEqual(o.HEALTH.current, 0x64)
        self.assertEqual(o.HEALTH_MAX.current, 0x74)
        self.assertEqual(o.SPIRIT_SHADOW_MAX.current, 0x914)
        self.assertEqual(o.SPIRIT_SHADOW.current + 4, 0x908)
        self.assertEqual(o.HEALTH.base, 0x60)
        # refill_ultimate pulse derives *_Max as offset+16 / +28
        self.assertEqual(o.ULTIMATE_CHARGE.offset + 16, o.ULTIMATE_CHARGE_MAX.offset)
        self.assertEqual(o.ULTIMATE_COUNT.offset + 16, o.ULTIMATE_COUNT_MAX.offset)
        self.assertEqual(o.ULTIMATE_CHARGE.offset + 28, o.ULTIMATE_CHARGE_MAX.current)

    def test_attributes_unique_and_not_overlapping(self):
        names = [f.name for f in o.ATTRIBUTES]
        self.assertEqual(len(names), len(set(names)))
        spans = sorted((f.offset, f.offset + f.size, f.name) for f in o.ATTRIBUTES)
        for (_, end, a), (start, _, b) in zip(spans, spans[1:]):
            self.assertLessEqual(end, start, f'{a} overlaps {b}')
        for f in o.ATTRIBUTES:
            self.assertEqual((f.kind, f.size, f.owner), (o.T_FLOAT_ATTR, 16, 'BasicAttributeSet'))

    def test_other_fields_not_overlapping_per_owner(self):
        fields = [v for v in vars(o).values() if isinstance(v, o.Field) and not isinstance(v, o.AttrField)]
        by_owner = {}
        for f in fields:
            by_owner.setdefault(f.owner, []).append(f)
        for owner, items in by_owner.items():
            spans = sorted((f.offset, f.offset + f.size, f.name) for f in items)
            for (_, end, a), (start, _, b) in zip(spans, spans[1:]):
                self.assertLessEqual(end, start, f'{owner}: {a} overlaps {b}')

    def test_lookup_tables(self):
        self.assertEqual(set(o.RESOURCE_MAX), {'health', 'shachi', 'spirit_shadow'})
        self.assertEqual(list(o.MOVEMENT), ['WalkSpeed', 'JogSpeed', 'SprintSpeed', 'CrouchSpeed'])
        self.assertIs(o.BY_NAME['Health'], o.HEALTH)
        self.assertEqual(o.PAWN_WEAPONS.args, ('Character Weapons Component', 0xec0, 0x10000, 8))


    def test_migrated_literals_absent(self):
        import re
        from pathlib import Path
        root = Path(__file__).resolve().parent
        migrated = [f.offset for f in o.ATTRIBUTES] + [f.current for f in o.ATTRIBUTES] + [
            0x1040, 0xec0, 0xf8, 0xe8, 0x9e8, 0x9f0, 0xe0, 0x620, 0x670, 0x50, 0x54, 0x58,
            0x228, 0x38, 0x30, 0x2f0, 0x358, 0x758, 0x9e0]
        for name in ('core.py', 'session_effects.py'):
            text = (root / name).read_text(encoding='utf-8')
            found = {int(h, 16) for h in re.findall(r'\b0x[0-9a-fA-F]+\b', text)} & set(migrated)
            self.assertFalse(found, f'{name} still hard-codes {sorted(map(hex, found))}; use offsets.py')


if __name__ == '__main__':
    unittest.main()
