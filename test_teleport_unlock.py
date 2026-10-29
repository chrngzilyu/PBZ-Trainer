"""Own-process x64 adapter and fake-memory lifecycle tests; never open the game."""
import ctypes as C
import ctypes.wintypes as W
import struct
import threading
import unittest
from unittest.mock import patch

import teleport_unlock as travel

KEY = 15124446
CALL = C.WINFUNCTYPE(None, C.c_void_p, C.c_void_p, C.c_void_p)
allocate = travel.api('VirtualAlloc', [C.c_void_p, C.c_size_t, W.DWORD, W.DWORD], C.c_void_p)
protect = travel.api('VirtualProtect', [C.c_void_p, C.c_size_t, W.DWORD, C.POINTER(W.DWORD)], W.BOOL)
release = travel.api('VirtualFree', [C.c_void_p, C.c_size_t, W.DWORD], W.BOOL)
this_process = travel.api('GetCurrentProcess', [], W.HANDLE)


class LocalAdapter:
    def __init__(self, kind):
        self.owner, self.frame, self.params = (C.create_string_buffer(n) for n in (40, 256, 16))
        self.guard, self.refs, self.outputs = (C.create_string_buffer(n) for n in (128, 48, 8))
        self.original_calls = []
        self.original = CALL(lambda obj, frame, result: self.original_calls.append((obj, frame, result)))
        self.clock_iat = C.c_void_p(C.cast(C.windll.kernel32.QueryPerformanceCounter, C.c_void_p).value)
        owner = C.addressof(self.owner)
        struct.pack_into('<i', self.owner, 12, 7)
        struct.pack_into('<Q', self.owner, 16, 0x12345000)
        struct.pack_into('<Q', self.owner, 32, 0x98765000)
        struct.pack_into('<I', self.guard, 8, 1)
        struct.pack_into('<IQQQ', self.guard, 12, 7, owner, 0x12345000, 0x98765000)
        struct.pack_into('<Q', self.guard, 0x50, travel.counter() + 3 * travel.frequency())
        struct.pack_into('<ii', self.guard, 0x58, 6, 10)
        struct.pack_into('<QQ', self.guard, 0x60, 0x1110, 0x2220)
        struct.pack_into('<Q', self.frame, 0x28, C.addressof(self.params))
        struct.pack_into('<Q', self.frame, 0x80, C.addressof(self.refs))
        struct.pack_into('<QQQ', self.refs, 0, 0x1110, C.addressof(self.outputs), C.addressof(self.refs) + 24)
        struct.pack_into('<QQQ', self.refs, 24, 0x2220, C.addressof(self.outputs) + 4, 0)
        if kind == 'query':
            struct.pack_into('<Q', self.params, 0, KEY)
        else:
            struct.pack_into('<iQ?', self.params, 0, 1, KEY, True)
        code = travel.build_adapter(C.addressof(self.guard), C.cast(self.original, C.c_void_p).value,
                                    C.addressof(self.clock_iat), KEY, kind)
        self.code = allocate(None, 4096, 0x3000, 4)
        if not self.code:
            raise OSError('local test allocation failed')
        C.memmove(self.code, code, len(code))
        old = W.DWORD()
        if not protect(self.code, 4096, 0x20, C.byref(old)):
            raise OSError('local test protection failed')
        travel.flush_cache(this_process(), self.code, len(code))
        self.function = CALL(self.code)

    def call(self):
        self.function(C.addressof(self.owner), C.addressof(self.frame), 0x1234)

    def close(self):
        release(self.code, 0, 0x8000)


class NativeAdapters(unittest.TestCase):
    def make(self, kind='query'):
        f = LocalAdapter(kind)
        self.addCleanup(f.close)
        return f

    def test_query_returns_virtual_availability_without_changing_input_or_owner(self):
        f = self.make()
        before = bytes(f.owner), bytes(f.params)
        f.call()
        self.assertEqual(struct.unpack('<ii', f.outputs), (6, 10))
        self.assertEqual((bytes(f.owner), bytes(f.params)), before)
        self.assertEqual(f.original_calls, [])
        self.assertEqual(struct.unpack_from('<Q', f.guard, 0x28)[0], 1)

    def test_no_record_virtual_one_satisfies_both_checks_without_consumption(self):
        f = self.make()
        struct.pack_into('<ii', f.guard, 0x58, 1, 1)
        f.call(); f.call()
        self.assertEqual(struct.unpack('<ii', f.outputs), (1, 1))
        self.assertEqual(struct.unpack_from('<Q', f.guard, 0x28)[0], 2)
        r = self.make('remove')
        before = bytes(r.params), bytes(r.owner)
        r.call()
        self.assertEqual(r.original_calls, [])
        self.assertEqual((bytes(r.params), bytes(r.owner)), before)
        self.assertEqual(struct.unpack_from('<Q', r.guard, 0x30)[0], 1)

    def test_other_item_and_numbered_fname_forward_unchanged_arguments(self):
        for key in (KEY + 1, KEY | (1 << 32)):
            f = self.make()
            struct.pack_into('<Q', f.params, 0, key)
            f.call()
            self.assertEqual(f.original_calls, [(C.addressof(f.owner), C.addressof(f.frame), 0x1234)])
            self.assertEqual(bytes(f.outputs), bytes(8))

    def test_remove_only_skips_normal_single_charm_consumption(self):
        for amount, key, broadcast in ((2, KEY, True), (1, KEY + 1, True), (1, KEY, False), (-1, KEY, True)):
            f = self.make('remove')
            struct.pack_into('<iQ?', f.params, 0, amount, key, broadcast)
            f.call()
            self.assertEqual(len(f.original_calls), 1)
            self.assertEqual(struct.unpack_from('<Q', f.guard, 0x30)[0], 0)

    def test_disabled_or_expired_adapter_forwards_original(self):
        for kind in ('query', 'remove'):
            for offset, fmt, value in ((8, '<I', 0), (0x50, '<Q', 0)):
                f = self.make(kind)
                struct.pack_into(fmt, f.guard, offset, value)
                f.call()
                self.assertEqual(len(f.original_calls), 1)

    def test_changed_owner_identity_does_not_intercept(self):
        for offset, fmt, value in ((12, '<i', 8), (16, '<Q', 1), (32, '<Q', 1)):
            f = self.make()
            struct.pack_into(fmt, f.owner, offset, value)
            f.call()
            self.assertEqual(len(f.original_calls), 1)

    def test_vm_frames_and_null_locals_forward(self):
        for offset, value in ((0x20, 1), (0x28, 0)):
            f = self.make()
            struct.pack_into('<Q', f.frame, offset, value)
            f.call()
            self.assertEqual(len(f.original_calls), 1)

    def test_missing_or_partial_out_parameters_do_not_write_any_output(self):
        for mode in ('none', 'missing_max', 'cycle'):
            f = self.make()
            if mode == 'none': struct.pack_into('<Q', f.frame, 0x80, 0)
            elif mode == 'missing_max': struct.pack_into('<Q', f.refs, 24, 0x3330)
            else: struct.pack_into('<Q', f.refs, 16, C.addressof(f.refs))
            f.call()
            self.assertEqual(bytes(f.outputs), bytes(8))
            self.assertEqual(len(f.original_calls), 1)
            self.assertEqual(f.original_calls[0][2], 0x1234)

    def test_out_parameter_order_does_not_matter(self):
        f = self.make()
        struct.pack_into('<QQ', f.refs, 0, 0x2220, C.addressof(f.outputs) + 4)
        struct.pack_into('<QQ', f.refs, 24, 0x1110, C.addressof(f.outputs))
        f.call()
        self.assertEqual(struct.unpack('<ii', f.outputs), (6, 10))


class FakeGame:
    base = 0x140000000
    def __init__(self):
        self.memory, self.logs = {}, []
        self.lock = threading.RLock()
        self.running = True
    def read(self, address, size):
        if address == self.base + 0x1632fea: return bytes.fromhex('4c8b412849634144')
        return bytes(self.memory.get(address + i, 0) for i in range(size))
    def put(self, address, payload):
        self.memory.update({address + i: v for i, v in enumerate(payload)})
    def qword(self, address): return struct.unpack('<Q', self.read(address, 8))[0]
    def token(self, address): return (address, 7, 0x4444, 0x5555)
    def alive(self): return self.running
    def log(self, row): self.logs.append(row)


class FakePages:
    def __init__(self, game):
        self.game, self.address = game, 0x10000
        self.fail_name = None
        self.exchanges = []
    def allocate(self): return self.address
    def write(self, address, payload): self.game.put(address, payload)
    def seal(self): pass
    def close(self): pass
    def exchange(self, entry, before, after):
        if self.fail_name == entry['name'] and after != entry['original']:
            raise RuntimeError('simulated installation failure')
        if self.game.qword(entry['slot']) != before:
            raise RuntimeError('changed by another tool')
        self.exchanges.append((entry['slot'], before, after))
        self.game.put(entry['slot'], struct.pack('<Q', after))


class Lifecycle(unittest.TestCase):
    def fixture(self):
        g = FakeGame()
        entries = [{'name': name, 'function': 0x1000 + i * 512, 'slot': 0x1128 + i * 512,
                    'token': g.token(0x1000 + i * 512), 'original': 0x5000 + i * 512,
                    'fields': {'CurrentNum': 0x1110, 'MaxNum': 0x2220}} for i, name in enumerate(('query', 'remove'))]
        for e in entries: g.put(e['slot'], struct.pack('<Q', e['original']))
        g.put(g.base + travel.QPC_IAT_RVA, struct.pack('<Q', 0x77770000))
        state = {'token': (0x3000, 3, 0x4000, 0x5000), 'amount': 0, 'maximum': 1, 'record_present': False}
        for target, replacement in (('descriptors', lambda game: entries), ('item_key', lambda game: KEY),
                                    ('snapshot', lambda game, key: dict(state))):
            p = patch.object(travel, target, replacement); p.start(); self.addCleanup(p.stop)
        pages = FakePages(g)
        controller = travel.TeleportUnlock(g, lambda game: pages)
        return g, controller, pages, entries, state

    def test_start_requires_two_valid_samples_and_no_inventory_record(self):
        g, c, pages, entries, state = self.fixture()
        c.start()
        self.assertEqual(g.read(pages.address + 8, 4), bytes(4))
        c.pulse()
        self.assertEqual(g.read(pages.address + 8, 4), struct.pack('<I', 1))
        self.assertEqual(struct.unpack('<ii', g.read(pages.address + 0x58, 8)), (1, 1))
        self.assertTrue(c.requested)

    def test_stop_restores_both_dispatch_pointers_and_disarms_first(self):
        g, c, pages, entries, state = self.fixture()
        c.start(); c.pulse(); c.stop()
        self.assertEqual(g.read(pages.address + 8, 4), bytes(4))
        self.assertFalse(c.requested)
        self.assertTrue(all(g.qword(e['slot']) == e['original'] for e in entries))

    def test_partial_install_failure_restores_the_first_pointer(self):
        g, c, pages, entries, state = self.fixture()
        pages.fail_name = 'remove'
        with self.assertRaises(RuntimeError): c.start()
        self.assertFalse(c.requested)
        self.assertTrue(all(g.qword(e['slot']) == e['original'] for e in entries))
        self.assertEqual(g.read(pages.address + 8, 4), bytes(4))

    def test_does_not_overwrite_another_tools_dispatch_on_stop(self):
        g, c, pages, entries, state = self.fixture()
        c.start(); c.pulse()
        g.put(entries[0]['slot'], struct.pack('<Q', 0x12345678))
        with self.assertRaises(RuntimeError): c.stop()
        self.assertEqual(g.qword(entries[0]['slot']), 0x12345678)
        self.assertEqual(g.qword(entries[1]['slot']), entries[1]['original'])
        self.assertEqual(g.read(pages.address + 8, 4), bytes(4))

    def test_player_replacement_pauses_then_rebinds_without_old_owner_writes(self):
        g, c, pages, entries, state = self.fixture()
        c.start(); c.pulse()
        state['token'] = (0x6000, 6, 0x7000, 0x8000)
        c.pulse()
        self.assertEqual(g.read(pages.address + 8, 4), bytes(4))
        c.pulse()
        self.assertEqual(g.qword(pages.address + 0x10), 0x6000)
        self.assertEqual(g.read(pages.address + 8, 4), struct.pack('<I', 1))
        self.assertTrue(all(not 0x3000 <= a < 0x8000 for a in g.memory if g.memory[a]))

    def test_transient_read_error_pauses_without_losing_request(self):
        g, c, pages, entries, state = self.fixture()
        c.start(); c.pulse()
        with patch.object(travel, 'snapshot', side_effect=RuntimeError('loading')):
            c.pulse()
        self.assertTrue(c.requested)
        self.assertEqual(g.read(pages.address + 8, 4), bytes(4))
        c.pulse(); c.pulse()
        self.assertEqual(g.read(pages.address + 8, 4), struct.pack('<I', 1))

    def test_an_existing_foreign_hook_is_rejected_before_allocation(self):
        g, c, pages, entries, state = self.fixture()
        g.put(entries[0]['slot'], struct.pack('<Q', 0x98765432))
        with self.assertRaises(RuntimeError): c.start()
        self.assertIsNone(c.pages)
        self.assertEqual(pages.exchanges, [])


class StateValidation(unittest.TestCase):
    def fixture(self, recorded=False):
        g = FakeGame()
        context = {'world': 0x2000, 'pawn': 0x2800, 'attributes': 0x3000}
        g.context = lambda: dict(context)
        g.backpack = lambda ctx: 0x4000
        g.property = lambda *args: None
        g.map_rows = lambda address: [(KEY, 0x5000)] if recorded else []
        g.obj = lambda p: {'address': p, 'class': 0x6000, 'outer': 0x4000, 'flags': 0}
        g.class_name = lambda p: 'PBZInventoryInfo'
        g.put(0x3064, struct.pack('<f', 600))
        g.put(0x5380, struct.pack('<iiQ', 6, 10, KEY))
        return g

    def test_snapshot_accepts_missing_and_empty_records_without_writes(self):
        for recorded in (False, True):
            g = self.fixture(recorded)
            if recorded: g.put(0x5380, struct.pack('<i', 0))
            before = dict(g.memory)
            result = travel.snapshot(g, KEY)
            self.assertEqual(result['amount'], 0)
            self.assertEqual(result['record_present'], recorded)
            self.assertEqual(g.memory, before)

    def test_snapshot_rejects_bad_quantity_key_or_ownership(self):
        for mode in ('negative', 'overflow', 'wrong_key', 'owner', 'duplicate'):
            g = self.fixture(True)
            if mode == 'negative': g.put(0x5380, struct.pack('<i', -1))
            if mode == 'overflow': g.put(0x5380, struct.pack('<i', 11))
            if mode == 'wrong_key': g.put(0x5388, struct.pack('<Q', KEY + 1))
            if mode == 'owner': g.obj = lambda p: {'address': p, 'class': 0x6000, 'outer': 1, 'flags': 0}
            if mode == 'duplicate': g.map_rows = lambda address: [(KEY, 0x5000)] * 2
            with self.assertRaises(RuntimeError): travel.snapshot(g, KEY)

    def test_snapshot_rejects_dead_or_changing_player(self):
        for health in (0, -1, float('nan'), float('inf')):
            g = self.fixture()
            g.put(0x3064, struct.pack('<f', health))
            with self.assertRaises(RuntimeError): travel.snapshot(g, KEY)
        g = self.fixture()
        g.context = unittest.mock.Mock(side_effect=[{'attributes': 0x3000}, {'attributes': 0x8000}])
        with self.assertRaises(RuntimeError): travel.snapshot(g, KEY)

    def test_fname_resolution_does_not_require_inventory_and_rejects_ambiguity(self):
        g = FakeGame()
        g.blocks = [0x10000]
        g.refresh_pool = lambda: None
        g.name = lambda index: 'Item_Traversing'
        g.put(0x10020, struct.pack('<H', len(b'Item_Traversing') << 6) + b'Item_Traversing')
        self.assertEqual(travel.item_key(g), 0x10)
        g.put(0x10060, struct.pack('<H', len(b'Item_Traversing') << 6) + b'Item_Traversing')
        with self.assertRaises(RuntimeError): travel.item_key(g)


class PointerFailure(unittest.TestCase):
    def test_short_write_is_rolled_back_but_foreign_value_is_preserved(self):
        for foreign in (False, True):
            g = FakeGame()
            entry = {'name': 'query', 'function': 0x1000, 'slot': 0x1128, 'token': g.token(0x1000)}
            before, after = 0x1111222233334444, 0x777788889999aaaa
            g.put(entry['slot'], struct.pack('<Q', before))
            pages = travel.RemotePages.__new__(travel.RemotePages)
            pages.game = g
            pages.check_process = lambda: None
            writes = []
            def write(address, payload):
                writes.append(payload)
                if len(writes) == 1:
                    g.put(address, payload[:3])
                    if foreign: g.put(address, struct.pack('<Q', 0xabcdef))
                    raise travel.DataWriteError('short write', 3)
                g.put(address, payload)
            pages._write = write
            with self.assertRaises(travel.DataWriteError): pages.exchange(entry, before, after)
            self.assertEqual(g.qword(entry['slot']), 0xabcdef if foreign else before)
            self.assertEqual(len(writes), 1 if foreign else 2)

    def test_identity_change_during_intent_log_prevents_pointer_write(self):
        g = FakeGame()
        entry = {'name': 'query', 'function': 0x1000, 'slot': 0x1128, 'token': g.token(0x1000)}
        g.put(entry['slot'], struct.pack('<Q', 0x5000))
        pages = travel.RemotePages.__new__(travel.RemotePages)
        pages.game = g
        pages.check_process = lambda: None
        pages._write = unittest.mock.Mock()
        g.log = lambda row: setattr(g, 'token', lambda ptr: (ptr, 8, 0, 0))
        with self.assertRaises(RuntimeError): pages.exchange(entry, 0x5000, 0x6000)
        pages._write.assert_not_called()


class PanelIsolation(unittest.TestCase):
    def test_teleport_toggle_never_changes_health_or_other_modifiers(self):
        from app import Panel
        from test_session_persistence import Variable
        panel = Panel.__new__(Panel)
        panel.game = unittest.mock.Mock()
        panel.game.start_teleport_unlock.return_value = 'waiting'
        panel.teleport_unlocked = Variable(True)
        panel.teleport_status, panel.message = Variable(''), Variable('')
        panel.floor_lock, panel.auto_health = Variable(True), Variable(False)
        panel.refresh = lambda: None
        panel.toggle_teleport()
        panel.teleport_unlocked.set(False)
        panel.toggle_teleport()
        self.assertTrue(panel.floor_lock.get())
        self.assertFalse(panel.auto_health.get())
        self.assertEqual([c[0] for c in panel.game.method_calls], ['start_teleport_unlock', 'stop_teleport_unlock'])


if __name__ == '__main__': unittest.main()
