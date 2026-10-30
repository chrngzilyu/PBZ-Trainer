"""Golden trace: byte-exact behaviour snapshot of the public Game API on a synthetic game image.

The fixture builds a complete fake process image (name pool, UObjects, reflected properties,
world -> player chain, weapons, backpack and medicine TMaps) and lets the real Game code walk it.
Every read, write, property check, log row, return value, exception and reported issue is
recorded in order and compared against tests_golden/trace.json.

Refactor commits must not change this file's output. Regenerate ONLY for an intentional,
documented behaviour change:  python test_golden_trace.py --update
Never touches a real game process.
"""
import json
import struct
import sys
import threading
import unittest
from pathlib import Path

from core import Game, DataWriteError
from errors import IssueQueue

GOLDEN = Path(__file__).resolve().parent / 'tests_golden' / 'trace.json'
BASE = 0x20000000
SIZE = 0x40000
T_INT, T_ATTR, T_OBJ, T_DOUBLE, T_STRUCT, T_MAP = 0x80, 0x100000, 0x10000, 0x100000000, 0x2000, 0x400000000000

ATTRS = {'Health': (0x58, 700), 'Health_Max': (0x68, 850), 'Health_Min': (0xd78, 0),
         'Attack': (0xc8, 120), 'TakenVulnerabilityMultiplier_Physical': (0x138, 1),
         'TakenVulnerabilityMultiplier_Elemental': (0x2c8, 1.25), 'Shachi': (0x638, 30),
         'Shachi_Max': (0x658, 100), 'UltimateCharge': (0x898, 25), 'UltimateCharge_Max': (0x8a8, 100),
         'UltimateChargeCount': (0x8b8, 0), 'UltimateChargeCount_Max': (0x8c8, 2),
         'SpiritShadow': (0x8f8, 3), 'SpiritShadow_Max': (0x908, 4), 'WalkSpeed': (0xd88, 130),
         'JogSpeed': (0xd98, 600), 'SprintSpeed': (0xda8, 750), 'CrouchSpeed': (0xdb8, 300)}


class Image:
    def __init__(self):
        self.mem = bytearray(SIZE)
        self.top = 0x2000
        self.names = {}
        self.name_cursor = 2
        self.index = 100
        # name pool: header at +0 (pool), block table at +16, block 0 at 0x1000
        self.pool = BASE + 0x100
        self.block = BASE + 0x1000
        for n in ('None', 'X1', 'X2', 'ByteProperty'):
            self.name(n)
        self.prop_kinds = {}
        self.meta = self.new_obj(0, 'Class')

    def alloc(self, size, align=16):
        self.top = (self.top + align - 1) // align * align
        address = BASE + self.top
        self.top += size
        assert self.top < SIZE
        return address

    def put(self, address, fmt, *values):
        struct.pack_into(fmt, self.mem, address - BASE, *values)

    def name(self, text):
        if text in self.names:
            return self.names[text]
        if text == 'None':
            index = 0
        elif text == 'ByteProperty':
            index = 3
        else:
            while self.name_cursor in (3,):
                self.name_cursor += 1
            index = self.name_cursor
        raw = text.encode('utf8')
        offset = index * 2
        self.put(self.block + offset, '<H', len(raw) << 6)
        self.mem[self.block - BASE + offset + 2:self.block - BASE + offset + 2 + len(raw)] = raw
        if text not in ('None', 'ByteProperty'):
            self.name_cursor += (2 + len(raw) + 1) // 2 + 1
        self.names[text] = index
        self.put(self.pool + 8, '<II', 0, self.name_cursor * 2)
        self.put(self.pool + 16, '<Q', self.block)
        return index

    def new_obj(self, cls, name, outer=0, flags=0, size=0x1100):
        address = self.alloc(size)
        self.index += 1
        self.put(address, '<QIiQIIQ', BASE + 0x10, flags, self.index, cls or address, self.name(name), 0, outer)
        return address

    def new_class(self, name, props, super_cls=0):
        cls = self.new_obj(self.meta, name, size=128)
        previous = None
        for prop_name, offset, kind, size in props:
            if kind not in self.prop_kinds:
                pc = self.alloc(32)
                self.put(pc + 24, '<Q', kind)
                self.prop_kinds[kind] = pc
            field = self.alloc(128)
            self.put(field + 8, '<Q', self.prop_kinds[kind])
            self.put(field + 32, '<I', self.name(prop_name))
            self.put(field + 52, '<I', size)
            self.put(field + 68, '<I', offset)
            if previous is None:
                self.put(cls + 80, '<Q', field)
            else:
                self.put(previous + 16, '<Q', field)
            previous = field
        self.put(cls + 64, '<Q', super_cls)
        return cls

    def sparse(self, at, rows, stride):
        array = self.alloc(max(1, len(rows)) * stride)
        for i, row in enumerate(rows):
            for fmt, off, *values in row:
                self.put(array + i * stride + off, fmt, *values)
        n = len(rows)
        self.put(at, '<Qii', array, n, n)
        self.put(at + 16, '<Q', (1 << n) - 1)
        self.put(at + 32, '<Q', 0)
        self.put(at + 40, '<ii', n, max(n, 1))
        return array


def build():
    im = Image()
    world_cls = im.new_class('World', [])
    world = im.new_obj(world_cls, 'World_0')
    world_rva = 0x800
    im.put(BASE + world_rva, '<Q', world)
    gi = im.new_obj(im.new_class('GameInstance', []), 'GI', world)
    im.put(world + 0x228, '<Q', gi)
    locals_array = im.alloc(16)
    im.put(gi + 0x38, '<Qii', locals_array, 1, 1)
    local = im.new_obj(im.new_class('LocalPlayer', []), 'LP', gi)
    im.put(locals_array, '<Q', local)
    controller = im.new_obj(im.new_class('PlayerController', []), 'PC', world)
    im.put(local + 0x30, '<Q', controller)
    pawn_cls = im.new_class('PBZ_Player_C', [('ConsciousnessDust', 0x1040, T_INT, 4),
                                             ('Character Weapons Component', 0xec0, T_OBJ, 8)])
    pawn = im.new_obj(pawn_cls, 'Player', world, size=0x1100)
    im.put(controller + 0x2f0, '<Q', pawn)
    im.put(controller + 0x358, '<Q', pawn)
    im.put(pawn + 0x1040, '<i', 40460)
    attr_super = im.new_class('AttributeSet', [('Unrelated', 0x30, T_INT, 4)])
    attr_cls = im.new_class('BasicAttributeSet', [(n, o, T_ATTR, 16) for n, (o, _) in ATTRS.items()], attr_super)
    attributes = im.new_obj(attr_cls, 'Attributes', pawn, size=0xe00)
    im.put(pawn + 0x758, '<Q', attributes)
    for n, (o, v) in ATTRS.items():
        im.put(attributes + o + 8, '<ff', v, v)
    # weapons
    weapons = im.new_obj(im.new_class('CharacterWeaponsComponent', [('RuntimeWeaponData', 0xf8, T_MAP, 80)]),
                         'Weapons', pawn, size=0x200)
    im.put(pawn + 0xec0, '<Q', weapons)
    switch = im.new_obj(im.new_class('PBZ_WeaponSwitchConfig', [('Switch Main Weapon Cooldown', 0x9e8, T_DOUBLE, 8),
                                                                ('Switch Shachi Weapon Cooldown', 0x9f0, T_DOUBLE, 8)]),
                        'SwitchCfg', size=0xa00)
    im.put(weapons + 0xe8, '<Q', switch)
    im.put(switch + 0x9e8, '<dd', 1.0, 0.8)
    main_cls = im.new_class('PBZ_MainWeaponConfig_C', [('Change Weapon Cooldown', 0xe0, T_DOUBLE, 8)])
    sub_cls = im.new_class('PBZ_SubWeaponConfig_C', [('Change Weapon Cooldown', 0xe0, T_DOUBLE, 8)])
    main = im.new_obj(main_cls, 'MainSword', size=0x100)
    sub = im.new_obj(sub_cls, 'ShachiBlade', size=0x100)
    im.put(main + 0xe0, '<d', 0.6)
    im.put(sub + 0xe0, '<d', 0.3)
    arr_main, arr_sub, arr_other = im.alloc(288), im.alloc(288), im.alloc(288)
    im.put(arr_main, '<Q', main)
    im.put(arr_sub, '<Q', sub)
    im.sparse(weapons + 0xf8, [
        [('<II', 0, im.name('Weapon.Slot.Main'), 0), ('<Qii', 8, arr_main, 1, 1)],
        [('<II', 0, im.name('Weapon.Slot.Other'), 0), ('<Qii', 8, arr_other, 0, 0)],
        [('<II', 0, im.name('Weapon.Slot.Shachi'), 0), ('<Qii', 8, arr_sub, 1, 1)]], 48)
    # backpack + medicine
    backpack = im.new_obj(im.new_class('PBZBackPackSubsystem', [('MedicineInfos', 0x620, T_MAP, 80),
                                                               ('MedicineExtraUsage', 0x670, T_INT, 4)]),
                          'BackPack', world, size=0x700)
    other_sub = im.new_obj(im.new_class('OtherSubsystem', []), 'Other', world)
    im.sparse(world + 0x9e0, [[('<QQ', 0, 1, other_sub)], [('<QQ', 0, 2, backpack)]], 24)
    med_cls = im.new_class('PBZMedicineInfo', [('CurrentTime', 0x50, T_INT, 4), ('DisplayTime', 0x54, T_INT, 4),
                                               ('MaxTime', 0x58, T_INT, 4)])
    hulu = im.new_obj(med_cls, 'HuluInfo', backpack, size=0x80)
    feijiu = im.new_obj(med_cls, 'FeiJiuInfo', backpack, size=0x80)
    im.put(hulu + 0x50, '<iii', 1, 1, 3)
    im.put(feijiu + 0x50, '<iii', 2, 2, 2)
    im.put(backpack + 0x670, '<i', 1)
    im.sparse(backpack + 0x620, [[('<QQ', 0, im.name('Hulu'), hulu)], [('<QQ', 0, im.name('FeiJiu'), feijiu)]], 24)
    im.refs = {'world': world, 'pawn': pawn, 'attributes': attributes, 'weapons': weapons, 'switch': switch,
               'main': main, 'sub': sub, 'backpack': backpack, 'hulu': hulu, 'feijiu': feijiu}
    im.profile = {'base': BASE, 'image_size': SIZE, 'name_pool_rva': im.pool - BASE, 'world_rva': world_rva,
                  'sha256': 'fixture'}
    return im


class TraceGame(Game):
    def __init__(self, image):
        self.image = image
        self.events = []
        self.fail = []  # [address, successes_before_fail, times_to_fail, optional 'applied']
        self.lock = threading.RLock()
        self.handle = 1
        self.issues = IssueQueue()
        self.undo = {}
        self.auto_shachi = None
        self.auto_spirit_shadow = None
        self.auto_health = None
        self.auto_medicines = {}
        self.auto_ultimate = None
        self.teleport_unlock = None
        self.floor_repair_count = 0
        self.property_cache = {}
        self.names = {}
        self.profile = image.profile
        self.pid, self.path = 4242, Path('PBZ.exe')
        self.base = self.profile['base']
        self.refresh_pool()

    def alive(self):
        return True

    def read(self, address, size):
        self.events.append(['R', hex(address), size])
        offset = address - BASE
        if not 0 <= offset <= SIZE - size or size <= 0:
            raise RuntimeError('fixture read out of image')
        return bytes(self.image.mem[offset:offset + size])

    def raw_write(self, address, payload):
        self.events.append(['W', hex(address), bytes(payload).hex()])
        offset = address - BASE
        for rule in self.fail:
            if rule[0] == address:
                if rule[1] > 0:
                    rule[1] -= 1
                elif rule[2] > 0:
                    rule[2] -= 1
                    applied = len(rule) > 3 and rule[3] == 'applied'
                    if applied:  # bytes reached memory but the API reported failure
                        self.image.mem[offset:offset + len(payload)] = payload
                    self.events.append(['W!', hex(address), applied])
                    raise DataWriteError('fixture write failed', len(payload) if applied else 0)
        self.image.mem[offset:offset + len(payload)] = payload

    def log(self, row):
        self.events.append(['L', normalise(row)])

    def property(self, cls, wanted, expected_offset, expected_type, expected_size):
        self.events.append(['P', hex(cls), wanted, hex(expected_offset), hex(expected_type), expected_size])
        return Game.property(self, cls, wanted, expected_offset, expected_type, expected_size)


def normalise(value):
    if isinstance(value, dict):
        return {str(k): normalise(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalise(v) for v in value]
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, float):
        return repr(value)
    return value


def f32(g, address, value):
    struct.pack_into('<ff', g.image.mem, address - BASE, value, value)


def switch_player(g):
    """Simulate the game replacing the attribute object (new internal index)."""
    a = g.image.refs['attributes']
    index = struct.unpack_from('<i', g.image.mem, a - BASE + 12)[0]
    struct.pack_into('<i', g.image.mem, a - BASE + 12, index + 5000)


def scenarios():
    r = lambda g, k: g.image.refs[k]
    A = lambda g, n: r(g, 'attributes') + ATTRS[n][0] + 8
    return {
        'status': [lambda g: g.status()],
        'targets_all': [lambda g, f=f: g.targets(f) for f in (
            'dust', 'weapon_cooldown:Main:0', 'weapon_cooldown:Shachi:0', 'movement:WalkSpeed', 'movement:CrouchSpeed',
            'incoming:physical', 'incoming:elemental', 'health', 'health_floor', 'shachi', 'attack', 'ultimate_charge',
            'ultimate_count', 'spirit_shadow', 'medicine', 'medicine:FeiJiu', 'cooldown', 'bogus', 'movement:Fly',
            'incoming:fire', 'weapon_cooldown:Main:9')],
        'map_rows_and_weapons': [lambda g: g.map_rows(r(g, 'backpack') + 0x620),
                                 lambda g: g.map_rows(r(g, 'world') + 0x9e0),
                                 lambda g: g.weapon_cooldown_configs(), lambda g: g.weapon_cooldown_features(),
                                 lambda g: g.backpack(g.context()), lambda g: g.medicine(g.context(), 'FeiJiu'),
                                 lambda g: g.medicine(g.context(), 'Wine'), lambda g: g.medicine_state('Hulu')],
        'refill_each': [lambda g: g.refill('health'), lambda g: g.refill('shachi'), lambda g: g.refill('spirit_shadow'),
                        lambda g: g.refill('medicine'), lambda g: g.refill('medicine:FeiJiu'), lambda g: g.refill('x'),
                        lambda g: g.refill_ultimate(), lambda g: g.refill_ultimate(expected_token=(1, 2, 3, 4))],
        'auto_health': [lambda g: g.start_auto_health(), lambda g: g.pulse_auto_health(), lambda g: g.pulse_auto_health(),
                        lambda g: f32(g, A(g, 'Health'), 10), lambda g: g.pulse_auto_health(),
                        lambda g: g.stop_auto_health(), lambda g: g.pulse_auto_health(), lambda g: g.stop_auto_health()],
        'auto_health_dead_and_switch': [lambda g: f32(g, A(g, 'Health'), 0), lambda g: g.start_auto_health(),
                                        lambda g: f32(g, A(g, 'Health'), 5), lambda g: g.start_auto_health(),
                                        lambda g: switch_player(g), lambda g: g.pulse_auto_health(),
                                        lambda g: g.auto_health],
        'auto_shachi': [lambda g: g.start_auto_shachi(), lambda g: g.pulse_auto_shachi(), lambda g: g.pulse_auto_shachi(),
                        lambda g: f32(g, A(g, 'Shachi'), 1), lambda g: g.pulse_auto_shachi(),
                        lambda g: f32(g, A(g, 'Shachi_Max'), 0), lambda g: g.pulse_auto_shachi(),
                        lambda g: g.auto_shachi, lambda g: g.stop_auto_shachi()],
        'auto_shachi_switch': [lambda g: g.start_auto_shachi(), lambda g: switch_player(g),
                               lambda g: g.pulse_auto_shachi(), lambda g: g.stop_auto_shachi()],
        'auto_spirit_shadow': [lambda g: g.start_auto_spirit_shadow(), lambda g: g.pulse_auto_spirit_shadow(),
                               lambda g: g.pulse_auto_spirit_shadow(), lambda g: f32(g, A(g, 'SpiritShadow'), 2),
                               lambda g: g.pulse_auto_spirit_shadow(), lambda g: g.stop_auto_spirit_shadow(),
                               lambda g: g.pulse_auto_spirit_shadow(), lambda g: g.start_auto_spirit_shadow(),
                               lambda g: f32(g, A(g, 'SpiritShadow'), float('inf')),
                               lambda g: g.pulse_auto_spirit_shadow(), lambda g: g.auto_spirit_shadow,
                               lambda g: g.start_auto_spirit_shadow(), lambda g: switch_player(g),
                               lambda g: g.pulse_auto_spirit_shadow()],
        'auto_ultimate': [lambda g: g.start_auto_ultimate(), lambda g: g.pulse_auto_ultimate(),
                          lambda g: g.pulse_auto_ultimate(), lambda g: f32(g, A(g, 'UltimateChargeCount'), 1),
                          lambda g: g.pulse_auto_ultimate(), lambda g: f32(g, A(g, 'UltimateChargeCount_Max'), 500),
                          lambda g: g.pulse_auto_ultimate(), lambda g: g.auto_ultimate, lambda g: g.stop_auto_ultimate(),
                          lambda g: f32(g, A(g, 'UltimateChargeCount_Max'), 2), lambda g: g.start_auto_ultimate(),
                          lambda g: switch_player(g), lambda g: g.pulse_auto_ultimate()],
        'auto_ultimate_rollback': [lambda g: g.fail.append([A(g, 'UltimateChargeCount'), 0, 1]),
                                   lambda g: g.start_auto_ultimate(), lambda g: g.pulse_auto_ultimate()],
        'auto_ultimate_rollback_fails': [lambda g: g.fail.append([A(g, 'UltimateChargeCount'), 0, 9]),
                                         lambda g: g.fail.append([A(g, 'UltimateCharge'), 1, 9]),
                                         lambda g: g.refill_ultimate()],
        'auto_medicine': [lambda g: g.start_auto_medicine('Hulu'), lambda g: g.pulse_auto_medicine('Hulu'),
                          lambda g: g.pulse_auto_medicine('Hulu'), lambda g: g.pulse_auto_medicine('FeiJiu'),
                          lambda g: g.start_auto_medicine('FeiJiu'), lambda g: g.stop_auto_medicine('Hulu'),
                          lambda g: g.stop_auto_medicine(), lambda g: g.start_auto_medicine('Hulu'),
                          lambda g: switch_player(g), lambda g: g.pulse_auto_medicine('Hulu'),
                          lambda g: dict(g.auto_medicines)],
        'attack_and_one_hit': [lambda g: g.modifier('attack', 2), lambda g: g.modifier('attack', 3),
                               lambda g: g.modifier('attack', 50), lambda g: g.start_one_hit(),
                               lambda g: g.stop_one_hit(), lambda g: g.stop_one_hit(), lambda g: g.modifier('attack', 1.5),
                               lambda g: g.restore_modifiers()],
        'incoming': [lambda g: g.modifier('incoming', 0.5), lambda g: g.modifier('incoming', 0.25),
                     lambda g: g.modifier('incoming', 0), lambda g: g.restore_modifiers()],
        'incoming_rollback_fails': [lambda g: g.fail.append([A(g, 'TakenVulnerabilityMultiplier_Elemental'), 0, 1]),
                                    lambda g: g.fail.append([A(g, 'TakenVulnerabilityMultiplier_Physical'), 1, 1, 'applied']),
                                    lambda g: g.modifier('incoming', 0.5), lambda g: sorted(g.undo)],
        'movement': [lambda g: g.modifier('movement', 1.5), lambda g: g.modifier('movement', 2),
                     lambda g: f32(g, A(g, 'JogSpeed'), 630), lambda g: g.restore_movement(),
                     lambda g: g.modifier('movement', 2), lambda g: g.modifier('movement', 1),
                     lambda g: g.modifier('movement', 9)],
        'movement_partial_component': [lambda g: g.modifier('movement', 1.5),
                                       lambda g: struct.pack_into('<f', g.image.mem, A(g, 'SprintSpeed') + 4 - BASE, 950),
                                       lambda g: g.restore_movement()],
        'movement_write_fail': [lambda g: g.fail.append([A(g, 'SprintSpeed'), 0, 1]),
                                lambda g: g.modifier('movement', 1.5), lambda g: sorted(g.undo)],
        'cooldown': [lambda g: g.modifier('cooldown', 0.5), lambda g: g.modifier('cooldown', 0.2),
                     lambda g: g.modifier('cooldown', 5), lambda g: g.status(), lambda g: g.restore_modifiers()],
        'cooldown_fail': [lambda g: g.fail.append([r(g, 'sub') + 0xe0, 0, 1]), lambda g: g.modifier('cooldown', 0.2),
                          lambda g: sorted(g.undo)],
        'floor_lock': [lambda g: g.start_floor_lock(), lambda g: g.check_floor_lock(),
                       lambda g: struct.pack_into('<f', g.image.mem, A(g, 'Health_Min') - BASE, 20),
                       lambda g: g.check_floor_lock(), lambda g: g.floor_repair_count,
                       lambda g: f32(g, A(g, 'Health_Max'), 900), lambda g: g.check_floor_lock(),
                       lambda g: g.stop_floor_lock(), lambda g: g.check_floor_lock(), lambda g: g.stop_floor_lock()],
        'floor_lock_partial_restore': [lambda g: g.start_floor_lock(),
                                       lambda g: struct.pack_into('<f', g.image.mem, A(g, 'Health_Min') + 4 - BASE, 33),
                                       lambda g: g.stop_floor_lock()],
        'floor_lock_dead_and_switch': [lambda g: g.start_floor_lock(), lambda g: f32(g, A(g, 'Health'), 0),
                                       lambda g: g.check_floor_lock(), lambda g: f32(g, A(g, 'Health'), 50),
                                       lambda g: g.start_floor_lock(), lambda g: switch_player(g),
                                       lambda g: g.check_floor_lock(), lambda g: sorted(g.undo)],
        'floor_vs_auto_health': [lambda g: g.start_auto_health(), lambda g: g.start_floor_lock(),
                                 lambda g: g.auto_health, lambda g: g.start_auto_health(), lambda g: sorted(g.undo),
                                 lambda g: g.modifier('health_floor', 9999), lambda g: g.modifier('health_floor', 300),
                                 lambda g: g.modifier('bogus', 1), lambda g: g.restore_modifiers()],
        'floor_lock_restore_fails': [lambda g: g.start_floor_lock(), lambda g: f32(g, A(g, 'Health'), 0),
                                     lambda g: g.fail.append([A(g, 'Health_Min'), 0, 9]),
                                     lambda g: g.check_floor_lock()],
        'dust': [lambda g: g.dust_state(), lambda g: g.set_dust(2000000000), lambda g: g.set_dust(-1),
                 lambda g: g.set_dust(True), lambda g: g.set_dust(5, expected_token=(1, 2, 3, 4)),
                 lambda g: g.restore_record(g.last), lambda g: g.dust_state()],
        'patch_rollback_fails': [lambda g: g.fail.append([A(g, 'Health'), 0, 2, 'applied']), lambda g: g.refill('health')],
        'patch_rollback_ok': [lambda g: g.fail.append([A(g, 'Shachi'), 0, 1, 'applied']),
                          lambda g: g.refill('shachi'), lambda g: g.fail.append([A(g, 'Health_Min'), 0, 1, 'applied']),
                          lambda g: g.modifier('health_floor', 400), lambda g: g.refill('shachi')],
        'patch_guard': [lambda g: g.patch('attack', (1.0,)), lambda g: g.patch('attack', (float('nan'), 1.0)),
                        lambda g: g.patch('attack', (1.0, 1.0), expected_token=(0, 0, 0, 0)),
                        lambda g: g.patch('medicine', (3, 3), expected_player_token=(0, 0, 0, 0))],
    }


def run_all():
    out = {}
    for name, steps in scenarios().items():
        g = TraceGame(build())
        g.last = None
        g.events.append(['init_done'])
        for i, step in enumerate(steps):
            g.events.append(['step', i])
            try:
                result = step(g)
                if isinstance(result, dict) and 'feature' in result and 'before' in result:
                    g.last = result
                g.events.append(['ok', normalise(result)])
            except Exception as exc:
                g.events.append(['raise', type(exc).__name__, str(exc),
                                 normalise(getattr(exc, 'rollback_errors', None))])
        g.events.append(['issues', [[i['category'], i['feature'], i['message']] for i in g.issues.drain()]])
        g.events.append(['undo', normalise(g.undo)])
        out[name] = g.events
    return out


def render():
    return json.dumps(run_all(), ensure_ascii=False, indent=0, sort_keys=True) + '\n'


class GoldenTrace(unittest.TestCase):
    def test_trace_matches_golden_byte_for_byte(self):
        actual = render()
        expected = GOLDEN.read_text(encoding='utf8')
        if actual != expected:
            a, e = actual.splitlines(), expected.splitlines()
            line = next((i for i, (x, y) in enumerate(zip(a, e)) if x != y), min(len(a), len(e)))
            self.fail(f'golden trace differs at line {line + 1}:\n  expected: {e[line] if line < len(e) else "<eof>"}'
                      f'\n  actual:   {a[line] if line < len(a) else "<eof>"}')

    def test_fixture_exercises_writes_logs_and_failures(self):
        trace = run_all()
        kinds = {event[0] for events in trace.values() for event in events}
        self.assertTrue({'R', 'W', 'W!', 'L', 'P', 'raise', 'ok'} <= kinds)


if __name__ == '__main__':
    if '--update' in sys.argv:
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(render(), encoding='utf8', newline='\n')
        print('updated', GOLDEN)
    else:
        unittest.main()
