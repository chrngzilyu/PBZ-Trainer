"""Verified PBZ data fields; optional teleport dispatch adapter is isolated in its own module."""
from __future__ import annotations

import hashlib
import json
import math
import struct
import threading
import time

from runtime_paths import RESOURCE_HOME
import offsets as O
from errors import LOG, ROLLBACK, IssueQueue, RollbackError, VersionError, report_issue

from win32 import (K, ProcessEntry, api, close_handle, discover, exit_code, image_path, open_process,  # noqa: F401  re-exported
                   process_first, process_next, query_path, rpm, snapshot, wpm)
from memory import DataWriteError, ProcessMixin  # noqa: F401  re-exported
from ue import UEMixin
from writer import WriterMixin

HOME = RESOURCE_HOME


class Game(ProcessMixin, UEMixin, WriterMixin):
    def __init__(self):
        self.lock = threading.RLock()
        self.handle = None
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
        self.profile = json.loads((HOME / 'profile.json').read_text(encoding='utf8'))
        self.pid, self.path = discover()
        with self.path.open('rb') as f:
            digest = hashlib.file_digest(f, 'sha256').hexdigest()
        if digest != self.profile['sha256']:
            raise VersionError('游戏版本不匹配，已停止写入；此版本只支持已核对版本')
        self.handle = open_process(0x410, False, self.pid)
        if not self.handle:
            raise RuntimeError('无法读取游戏进程')
        try:
            if image_path(self.handle) != self.path:
                raise RuntimeError('进程身份变化')
            self.base = self.profile['base']
            header = self.read(self.base, 4096)
            nt = struct.unpack_from('<I', header, 60)[0]
            if header[:2] != b'MZ' or header[nt:nt + 4] != b'PE\0\0':
                raise VersionError('游戏映像校验失败')
            if struct.unpack_from('<H', header, nt + 4)[0] != 0x8664:
                raise VersionError('需要已核对的 64 位游戏')
            if struct.unpack_from('<I', header, nt + 80)[0] != self.profile['image_size']:
                raise VersionError('游戏映像大小变化')
            self.refresh_pool()
            if self.name(0) != 'None' or self.name(3) != 'ByteProperty':
                raise VersionError('引擎名称池校验失败')
            self.context()
        except BaseException:
            self.close()
            raise

    def weapon_cooldown_configs(self, context=None):
        """Resolve equipped main/sub weapon assets, excluding medicines and interactions."""
        context = context or self.context()
        pawn = context['pawn']
        self.property(self.obj(pawn)['class'], *O.PAWN_WEAPONS.args)
        weapons = self.qword(pawn + O.PAWN_WEAPONS.offset)
        if self.obj(weapons)['outer'] != pawn or self.class_name(weapons) != 'CharacterWeaponsComponent':
            raise RuntimeError('武器组件归属不匹配')
        self.property(self.obj(weapons)['class'], *O.WEAPONS_RUNTIME_DATA.args)
        raw = self.read(weapons + O.WEAPONS_RUNTIME_DATA.offset, 80)
        array, count, capacity = struct.unpack_from('<Qii', raw)
        bits, max_bits = struct.unpack_from('<ii', raw, 40)
        if not 0 < count <= capacity <= 32 or bits != count or not bits <= max_bits <= 128:
            raise RuntimeError('武器槽映射布局变化')
        secondary = struct.unpack_from('<Q', raw, 32)[0]
        flags = self.read(secondary, (bits + 7) // 8) if secondary else raw[16:32]
        found = {}
        seen = set()
        for i in range(count):
            if not flags[i // 8] & (1 << (i % 8)):
                continue
            row = array + i * 48
            name, number = struct.unpack('<II', self.read(row, 8))
            slot = self.name(name)
            if number or slot not in ('Weapon.Slot.Main', 'Weapon.Slot.Shachi'):
                continue
            data, length, allocated = struct.unpack('<Qii', self.read(row + 8, 16))
            if not 0 <= length <= allocated <= 8:
                raise RuntimeError('武器槽数组布局变化')
            for index in range(length):
                config = self.qword(data + index * 288)
                if not config or config in seen:
                    continue
                expected = 'PBZ_MainWeaponConfig_C' if slot.endswith('.Main') else 'PBZ_SubWeaponConfig_C'
                if self.class_name(config) != expected or self.obj(config)['flags'] & O.RF_CLASS_DEFAULT_OBJECT:
                    raise RuntimeError('武器配置类型不匹配')
                self.property(self.obj(config)['class'], *O.CHANGE_WEAPON_COOLDOWN.args)
                feature = f"weapon_cooldown:{slot.rsplit('.', 1)[1]}:{index}"
                found[feature] = config
                seen.add(config)
        if not found:
            raise RuntimeError('未找到当前玩家的主武器／影之武装配置')
        return found

    def weapon_cooldown_features(self, context=None):
        return list(self.weapon_cooldown_configs(context))

    def backpack(self, context):
        matches = []
        for _, pointer in self.map_rows(context['world'] + O.WORLD_SUBSYSTEMS):
            try:
                obj = self.obj(pointer)
                if obj['outer'] == context['world'] and self.class_name(pointer) == 'PBZBackPackSubsystem':
                    matches.append(pointer)
            except (RuntimeError, UnicodeError):
                continue
        if len(matches) != 1:
            raise RuntimeError('背包子系统尚未就绪')
        return matches[0]

    def medicine(self, context, kind='Hulu'):
        if kind not in ('Hulu', 'FeiJiu'):
            raise ValueError('仅支持已核对的葫芦和酒')
        backpack = self.backpack(context)
        self.property(self.obj(backpack)['class'], *O.BACKPACK_MEDICINES.args)
        matches = []
        for key, pointer in self.map_rows(backpack + O.BACKPACK_MEDICINES.offset):
            if self.name(key & 0xffffffff) != kind or key >> 32:
                continue
            obj = self.obj(pointer)
            if obj['outer'] == backpack and not obj['flags'] & O.RF_CLASS_DEFAULT_OBJECT and self.class_name(pointer) == 'PBZMedicineInfo':
                matches.append(pointer)
        if len(matches) != 1:
            raise RuntimeError('未找到当前药品：' + ('葫芦' if kind == 'Hulu' else '酒'))
        return backpack, matches[0]

    def medicine_state(self, kind, context=None):
        context = context or self.context()
        backpack, medicine = self.medicine(context, kind)
        for field in O.MEDICINE_FIELDS:
            self.property(self.obj(medicine)['class'], *field.args)
        self.property(self.obj(backpack)['class'], *O.BACKPACK_MEDICINE_EXTRA.args)
        current, display, maximum = struct.unpack('<iii', self.read(medicine + O.MEDICINE_CURRENT.offset, 12))
        extra = struct.unpack('<i', self.read(backpack + O.BACKPACK_MEDICINE_EXTRA.offset, 4))[0]
        if not 0 < maximum <= 50 or not 0 <= extra <= 50 or not 0 <= current <= maximum + extra or not 0 <= display <= maximum + extra:
            raise RuntimeError('药品次数或上限无效，已停止补充')
        return medicine, current, display, maximum + extra

    def targets(self, feature):
        context = self.context()
        if feature == 'dust':
            pawn = context['pawn']
            self.property(self.obj(pawn)['class'], *O.PAWN_DUST.args)
            return [(pawn, pawn + O.PAWN_DUST.offset, '<i')]
        if feature.startswith('weapon_cooldown:'):
            config = self.weapon_cooldown_configs(context).get(feature)
            if config is None:
                raise RuntimeError('当前武器已变化，停止写入旧武器配置')
            return [(config, config + O.CHANGE_WEAPON_COOLDOWN.offset, '<d')]
        attributes = context['attributes']
        cls = self.obj(attributes)['class']
        if feature.startswith('movement:'):
            name = feature.split(':', 1)[1]
            if name not in O.MOVEMENT:
                raise ValueError('未知的移动速度项目')
            field = O.MOVEMENT[name]
            self.property(cls, *field.args)
            return [(attributes, attributes + field.base, '<ff')]
        if feature.startswith('incoming:'):
            if feature not in O.INCOMING:
                raise ValueError('未知的承伤类型')
            field = O.INCOMING[feature]
            self.property(cls, *field.args)
            return [(attributes, attributes + field.base, '<ff')]
        if feature in ('health', 'health_floor', 'shachi', 'attack', 'ultimate_charge', 'ultimate_count', 'spirit_shadow'):
            field = O.RESOURCE_FIELD[feature]
            self.property(cls, *field.args)
            return [(attributes, attributes + field.base, '<ff')]
        if feature == 'medicine' or feature.startswith('medicine:'):
            kind = 'Hulu' if feature == 'medicine' else feature.split(':', 1)[1]
            backpack, medicine = self.medicine(context, kind)
            cls = self.obj(medicine)['class']
            self.property(cls, *O.MEDICINE_CURRENT.args)
            self.property(cls, *O.MEDICINE_DISPLAY.args)
            return [(medicine, medicine + O.MEDICINE_CURRENT.offset, '<ii')]
        if feature == 'cooldown':
            pawn = context['pawn']
            self.property(self.obj(pawn)['class'], *O.PAWN_WEAPONS.args)
            weapons = self.qword(pawn + O.PAWN_WEAPONS.offset)
            if self.obj(weapons)['outer'] != pawn or self.class_name(weapons) != 'CharacterWeaponsComponent':
                raise RuntimeError('武器组件归属不匹配')
            config = self.qword(weapons + O.UNVERIFIED_WEAPON_CONFIG_PTR)
            cls = self.obj(config)['class']
            self.property(cls, *O.SWITCH_MAIN_COOLDOWN.args)
            self.property(cls, *O.SWITCH_SHACHI_COOLDOWN.args)
            return [(config, config + O.SWITCH_MAIN_COOLDOWN.offset, '<dd')]
        raise ValueError('不支持的功能')

    def dust_state(self):
        with self.lock:
            owner, address, fmt = self.targets('dust')[0]
            token = self.token(owner)
            amount = struct.unpack(fmt, self.read(address, 4))[0]
            if not 0 <= amount <= 2000000000:
                raise RuntimeError('意识尘埃数量超出已核对范围')
            if self.context()['pawn'] != owner or self.token(owner) != token:
                raise RuntimeError('意识尘埃所属角色已变化')
            return {'amount': amount, 'token': token}

    def set_dust(self, amount, expected_token=None):
        with self.lock:
            # The game's ChangeDustNumInternal Blueprint clamps to 0..2,000,000,000.
            if isinstance(amount, bool) or not isinstance(amount, int) or not 0 <= amount <= 2000000000:
                raise ValueError('意识尘埃数量须为整数 0–2,000,000,000')
            state = self.dust_state()
            if expected_token is not None and tuple(expected_token) != state['token']:
                raise RuntimeError('意识尘埃所属角色已变化，未写入')
            return self.patch('dust', (amount,), expected_token=state['token'])

    def refill(self, feature, expected_token=None, expected_player_token=None):
        with self.lock:
            context = self.context()
            attr = context['attributes']
            if feature in ('health', 'shachi', 'spirit_shadow'):
                field = O.RESOURCE_MAX[feature]
                self.property(self.obj(attr)['class'], *field.args)
                maximum = struct.unpack('<f', self.read(attr + field.current, 4))[0]
                if not 0 < maximum <= 1000000:
                    raise RuntimeError('资源上限无效')
                return self.patch(feature, (maximum, maximum), expected_token=expected_token)
            if feature == 'medicine' or feature.startswith('medicine:'):
                kind = 'Hulu' if feature == 'medicine' else feature.split(':', 1)[1]
                _, _, _, total = self.medicine_state(kind, context)
                return self.patch(feature, (total, total), expected_token=expected_token,
                                  expected_player_token=expected_player_token)
            raise ValueError('未知补充项目')

    def refill_ultimate(self, expected_token=None):
        """Fill the player's two reflected ultimate attributes, preserving capacities."""
        with self.lock:
            context = self.context()
            attr = context['attributes']
            cls = self.obj(attr)['class']
            self.property(cls, *O.ULTIMATE_CHARGE_MAX.args)
            self.property(cls, *O.ULTIMATE_COUNT_MAX.args)
            charge = struct.unpack('<f', self.read(attr + O.ULTIMATE_CHARGE_MAX.current, 4))[0]
            count = struct.unpack('<f', self.read(attr + O.ULTIMATE_COUNT_MAX.current, 4))[0]
            if not 0 < charge <= 1000000 or not 0 < count <= 100:
                raise RuntimeError('精纯杀气上限无效')
            identity = self.token(attr)
            if expected_token is not None and identity != tuple(expected_token):
                raise RuntimeError('精纯杀气所属玩家已变化，未补充')
            records = []
            try:
                records.append(self.patch('ultimate_charge', (charge, charge), expected_token=identity))
                if self.context()['attributes'] != attr or self.token(attr) != identity:
                    raise RuntimeError('补充过程中玩家已切换')
                records.append(self.patch('ultimate_count', (count, count), expected_token=identity))
                self.log({'action': 'ultimate_refill_completed', 'records': records, 'hud_observation_required': True})
            except Exception:
                for record in reversed(records):
                    try:
                        self.restore_record(record)
                    except Exception as cleanup:
                        report_issue(self, ROLLBACK, record['feature'], '精纯杀气补充回滚失败：' + str(cleanup))
                        self.log({'action': 'ultimate_cleanup_failed', 'error': str(cleanup), 'record': record})
                raise
            return records

    def start_auto_medicine(self, kind):
        with self.lock:
            context = self.context()
            medicine, _, _, _ = self.medicine_state(kind, context)
            binding = {'medicine': self.token(medicine), 'player': self.token(context['attributes'])}
            self.log({'action': 'auto_medicine_started', 'kind': kind, 'binding': binding})
            self.auto_medicines[kind] = binding

    def stop_auto_medicine(self, kind=None):
        with self.lock:
            active = self.auto_medicines.pop(kind, None) if kind else dict(self.auto_medicines)
            if kind is None:
                self.auto_medicines.clear()
            if active:
                try:
                    self.log({'action': 'auto_medicine_stopped', 'kind': kind})
                except OSError as log_error:
                    report_issue(self, LOG, '', log_error)

    def pulse_auto_medicine(self, kind):
        with self.lock:
            binding = self.auto_medicines.get(kind)
            if binding is None:
                return False
            try:
                context = self.context()
                if self.token(context['attributes']) != binding['player']:
                    raise RuntimeError('玩家已切换，药品锁定已停止')
                medicine, current, display, total = self.medicine_state(kind, context)
                if self.token(medicine) != binding['medicine']:
                    raise RuntimeError('药品对象已变化，锁定已停止')
                if current < total or display < total:
                    self.refill('medicine:' + kind, expected_token=binding['medicine'], expected_player_token=binding['player'])
                    return True
                return False
            except Exception:
                self.auto_medicines.pop(kind, None)
                raise

    def start_auto_ultimate(self):
        with self.lock:
            attr = self.context()['attributes']
            self.targets('ultimate_charge')
            self.targets('ultimate_count')
            identity = self.token(attr)
            self.log({'action': 'auto_ultimate_started', 'token': identity})
            self.auto_ultimate = identity

    def stop_auto_ultimate(self):
        with self.lock:
            active = self.auto_ultimate is not None
            self.auto_ultimate = None
            if active:
                try:
                    self.log({'action': 'auto_ultimate_stopped'})
                except OSError as log_error:
                    report_issue(self, LOG, '', log_error)

    def pulse_auto_ultimate(self):
        with self.lock:
            if self.auto_ultimate is None:
                return False
            try:
                attr = self.context()['attributes']
                if self.token(attr) != self.auto_ultimate:
                    raise RuntimeError('玩家已切换，精纯杀气锁定已停止')
                cls = self.obj(attr)['class']
                values = []
                for field, field_max, cap in [(O.ULTIMATE_CHARGE, O.ULTIMATE_CHARGE_MAX, 1000000),
                                              (O.ULTIMATE_COUNT, O.ULTIMATE_COUNT_MAX, 100)]:
                    self.property(cls, *field.args)
                    self.property(cls, *field_max.args)
                    current = struct.unpack('<f', self.read(attr + field.current, 4))[0]
                    maximum = struct.unpack('<f', self.read(attr + field_max.current, 4))[0]
                    if not math.isfinite(current) or not 0 <= current <= maximum <= cap or maximum <= 0:
                        raise RuntimeError('精纯杀气状态无效，锁定已停止')
                    values.append((current, maximum))
                if any(current < maximum - 0.001 for current, maximum in values):
                    self.refill_ultimate(expected_token=self.auto_ultimate)
                    return True
                return False
            except Exception:
                self.auto_ultimate = None
                raise

    def start_floor_lock(self):
        with self.lock:
            self.stop_auto_health()
            attr = self.context()['attributes']
            identity = self.token(attr)
            self.property(self.obj(attr)['class'], *O.HEALTH_MAX.args)
            current = struct.unpack('<f', self.read(attr + O.HEALTH.current, 4))[0]
            maximum = struct.unpack('<f', self.read(attr + O.HEALTH_MAX.current, 4))[0]
            if not math.isfinite(current) or not 0 < current <= maximum <= 1000000:
                raise RuntimeError('当前生命状态无效或已死亡，未开启生命下限锁定')
            record = self.modifier('health_floor', maximum)
            self.floor_repair_count = 0
            try:
                self.refill('health', expected_token=identity)
            except Exception:
                self.stop_floor_lock()
                raise
            return record

    def stop_floor_lock(self):
        with self.lock:
            record = self.undo.get('health_floor')
            if record is not None:
                result = self.restore_record(record)
                del self.undo['health_floor']
                return result

    def check_floor_lock(self):
        """Maintain Health_Min after normal recalculation, without repeatedly writing Health."""
        with self.lock:
            record = self.undo.get('health_floor')
            if record is None:
                raise RuntimeError('生命下限锁定当前未开启')
            try:
                attr = self.context()['attributes']
                if self.token(attr) != tuple(record['token']):
                    raise RuntimeError('玩家已切换，生命下限锁定已停止')
                current = struct.unpack('<f', self.read(attr + O.HEALTH.current, 4))[0]
                maximum = struct.unpack('<f', self.read(attr + O.HEALTH_MAX.current, 4))[0]
                if not math.isfinite(current) or not 0 < current <= maximum <= 1000000:
                    raise RuntimeError('生命状态无效或已死亡，生命下限保护已停止')
                observed = self.read(record['address'], 8)
                observed_values = struct.unpack('<ff', observed)
                if not all(math.isfinite(v) and 0 <= v <= 1000000 for v in observed_values):
                    raise RuntimeError('游戏生命下限数值无效，停止保护')
                target = struct.pack('<ff', maximum, maximum)
                if observed == bytes.fromhex(record['after']) and observed == target:
                    return False
                old_expected = bytes.fromhex(record['after'])
                original = bytes.fromhex(record['before'])
                # Preserve game-origin changes as the new exit baseline, only for changed components.
                self.log({'action': 'floor_repair_intent', 'observed': observed.hex(),
                          'target': target.hex(), 'record': record})
                updated = self.patch('health_floor', (maximum, maximum), expected_token=record['token'])
                actual_before = bytes.fromhex(updated['before'])
                baseline = b''.join(actual_before[i:i + 4] if actual_before[i:i + 4] != old_expected[i:i + 4]
                                    else original[i:i + 4] for i in (0, 4))
                updated['before'] = baseline.hex()
                self.undo['health_floor'] = updated
                self.floor_repair_count = getattr(self, 'floor_repair_count', 0) + 1
                self.log({'action': 'floor_reapplied', 'count': self.floor_repair_count, 'record': updated,
                          'periodic_health_write': False})
                return True
            except Exception as exc:
                try:
                    self.log({'action': 'floor_lock_stopped', 'reason': str(exc), 'record': record})
                except OSError as log_error:
                    report_issue(self, LOG, '', log_error)
                try:
                    self.stop_floor_lock()
                except Exception as cleanup:
                    raise RollbackError(str(exc) + '；原下限未能自动恢复：' + str(cleanup), [str(cleanup)]) from exc
                raise

    def start_auto_health(self):
        with self.lock:
            self.stop_floor_lock()
            context = self.context()
            attr = context['attributes']
            self.targets('health')
            self.property(self.obj(attr)['class'], *O.HEALTH_MAX.args)
            current = struct.unpack('<f', self.read(attr + O.HEALTH.current, 4))[0]
            maximum = struct.unpack('<f', self.read(attr + O.HEALTH_MAX.current, 4))[0]
            if not math.isfinite(current) or not 0 < current <= maximum <= 1000000:
                raise RuntimeError('当前生命状态无效或已死亡，未开启自动回血')
            token = self.token(attr)
            self.log({'action': 'auto_health_started', 'token': token})
            self.auto_health = token

    def stop_auto_health(self):
        with self.lock:
            was_active = self.auto_health is not None
            self.auto_health = None
            if was_active:
                try:
                    self.log({'action': 'auto_health_stopped'})
                except OSError as log_error:
                    report_issue(self, LOG, '', log_error)

    def pulse_auto_health(self):
        with self.lock:
            if self.auto_health is None:
                return False
            try:
                attr = self.context()['attributes']
                if self.token(attr) != self.auto_health:
                    raise RuntimeError('玩家已切换，自动回血已停止')
                current = struct.unpack('<f', self.read(attr + O.HEALTH.current, 4))[0]
                maximum = struct.unpack('<f', self.read(attr + O.HEALTH_MAX.current, 4))[0]
                if not math.isfinite(current) or not 0 < current <= maximum <= 1000000:
                    raise RuntimeError('生命状态异常或已死亡，自动回血已停止')
                if current < maximum - 0.001:
                    self.refill('health', expected_token=self.auto_health)
                    return True
                return False
            except Exception:
                self.auto_health = None
                raise

    def start_auto_shachi(self):
        with self.lock:
            context = self.context()
            self.targets('shachi')
            token = self.token(context['attributes'])
            self.log({'action': 'auto_shachi_started', 'token': token})
            self.auto_shachi = token

    def stop_auto_shachi(self):
        with self.lock:
            was_active = self.auto_shachi is not None
            self.auto_shachi = None
            if was_active:
                try:
                    self.log({'action': 'auto_shachi_stopped'})
                except OSError as log_error:
                    report_issue(self, LOG, '', log_error)

    def pulse_auto_shachi(self):
        with self.lock:
            if self.auto_shachi is None:
                return False
            try:
                context = self.context()
                attr = context['attributes']
                if self.token(attr) != self.auto_shachi:
                    raise RuntimeError('玩家已切换，杀气自动补充已停止')
                current = struct.unpack('<f', self.read(attr + O.SHACHI.current, 4))[0]
                maximum = struct.unpack('<f', self.read(attr + O.SHACHI_MAX.current, 4))[0]
                if not math.isfinite(current) or not 0 <= current <= maximum <= 1000000 or maximum <= 0:
                    raise RuntimeError('杀气状态异常，自动补充已停止')
                if current < maximum - 0.001:
                    self.refill('shachi', expected_token=self.auto_shachi)
                    return True
                return False
            except Exception:
                self.auto_shachi = None
                raise

    def start_auto_spirit_shadow(self):
        with self.lock:
            owner, _, _ = self.targets('spirit_shadow')[0]
            token = self.token(owner)
            self.log({'action': 'auto_spirit_shadow_started', 'token': token})
            self.auto_spirit_shadow = token

    def stop_auto_spirit_shadow(self):
        with self.lock:
            active = self.auto_spirit_shadow is not None
            self.auto_spirit_shadow = None
            if active:
                try:
                    self.log({'action': 'auto_spirit_shadow_stopped'})
                except OSError as log_error:
                    report_issue(self, LOG, '', log_error)

    def pulse_auto_spirit_shadow(self):
        with self.lock:
            if self.auto_spirit_shadow is None:
                return False
            try:
                owner, address, _ = self.targets('spirit_shadow')[0]
                if self.token(owner) != self.auto_spirit_shadow:
                    raise RuntimeError('玩家已切换，锁暗源已停止')
                self.property(self.obj(owner)['class'], *O.SPIRIT_SHADOW_MAX.args)
                # address is SpiritShadow.base (+8); +4 lands on its CurrentValue
                current = struct.unpack('<f', self.read(address + (O.SPIRIT_SHADOW.current - O.SPIRIT_SHADOW.base), 4))[0]
                maximum = struct.unpack('<f', self.read(owner + O.SPIRIT_SHADOW_MAX.current, 4))[0]
                if not math.isfinite(current) or not 0 <= current <= maximum <= 1000000 or maximum <= 0:
                    raise RuntimeError('暗源状态异常，锁暗源已停止')
                if current < maximum - 0.001:
                    self.refill('spirit_shadow', expected_token=self.auto_spirit_shadow)
                    return True
                return False
            except Exception:
                self.auto_spirit_shadow = None
                raise

    def modifier(self, feature, amount):
        with self.lock:
            if feature == 'movement':
                return self.movement_modifier(amount)
            if feature == 'attack':
                if not math.isfinite(amount) or not 0.1 <= amount <= 20:
                    raise ValueError('攻击倍率范围为 0.1–20')
                return self._apply_attack(amount, 'multiplier')
            if feature == 'incoming':
                return self.incoming_modifier(amount)
            if feature == 'cooldown':
                return self.switch_cooldown_modifier(amount)
            if feature != 'health_floor':
                raise ValueError('不支持持续配置修改')
            floor_identity = None
            if feature == 'health_floor':
                attr = self.context()['attributes']
                self.property(self.obj(attr)['class'], *O.HEALTH_MAX.args)
                maximum = struct.unpack('<f', self.read(attr + O.HEALTH_MAX.current, 4))[0]
                if not math.isfinite(amount) or not 0 < amount <= maximum <= 1000000:
                    raise ValueError('生命下限必须大于0且不超过当前最大生命')
                floor_identity = self.token(attr)
            if feature in self.undo:
                self.restore_record(self.undo[feature])
                del self.undo[feature]
            owner, address, fmt = self.targets(feature)[0]
            original = struct.unpack(fmt, self.read(address, struct.calcsize(fmt)))
            if not all(math.isfinite(v) and 0 <= v <= 1000000 for v in original):
                raise RuntimeError('生命下限原值无效')
            values = (amount, amount)
            record = self.patch(feature, values, expected_token=floor_identity)
            self.undo[feature] = record
            return record

    def _apply_attack(self, multiplier, mode):
        """Both attack modes share one undo slot, so they never multiply each other."""
        with self.lock:
            if 'attack' in self.undo:
                self.restore_record(self.undo['attack'])
                del self.undo['attack']
            owner, address, fmt = self.targets('attack')[0]
            identity = self.token(owner)
            original = struct.unpack(fmt, self.read(address, struct.calcsize(fmt)))
            if not all(math.isfinite(v) and 0 < v <= 1000000 for v in original):
                raise RuntimeError('攻击原值无效')
            record = self.patch('attack', tuple(v * multiplier for v in original), expected_token=identity)
            record['attack_mode'] = mode
            record['multiplier'] = multiplier
            self.undo['attack'] = record
            return record

    def start_one_hit(self):
        return self._apply_attack(10000, 'one_hit')

    def incoming_modifier(self, amount):
        """Scale the local player's physical/elemental taken factors, not shared enemy assets."""
        with self.lock:
            if not math.isfinite(amount) or not 0.1 <= amount <= 20:
                raise ValueError('怪物伤害倍率范围为 0.1–20')
            features = ('incoming:physical', 'incoming:elemental')
            for feature in features:
                if feature in self.undo:
                    self.restore_record(self.undo[feature])
                    del self.undo[feature]
            identity = self.token(self.context()['attributes'])
            plan = []
            for feature in features:
                owner, address, fmt = self.targets(feature)[0]
                if self.token(owner) != identity:
                    raise RuntimeError('承伤属性对象已变化')
                original = struct.unpack(fmt, self.read(address, struct.calcsize(fmt)))
                if not all(math.isfinite(v) and 0 <= v <= 1000 for v in original):
                    raise RuntimeError('承伤原值无效')
                plan.append((feature, tuple(v * amount for v in original)))
            completed = []
            try:
                for feature, values in plan:
                    record = self.patch(feature, values, expected_token=identity)
                    record['multiplier'] = amount
                    self.undo[feature] = record
                    completed.append(record)
            except Exception as exc:
                self._rollback_batch(completed, exc)
                raise
            return completed

    def restore_movement(self):
        with self.lock:
            results = {}
            for feature, record in list(self.undo.items()):
                if not feature.startswith('movement:'):
                    continue
                try:
                    result = self.restore_record(record)
                    results[feature] = result or '已恢复'
                    del self.undo[feature]
                except Exception as exc:
                    results[feature] = str(exc)
            return results

    def movement_modifier(self, amount):
        with self.lock:
            if not math.isfinite(amount) or not 1 <= amount <= 5:
                raise ValueError('移动加速倍率范围为 1–5')
            self.restore_movement()
            if any(key.startswith('movement:') for key in self.undo):
                raise RuntimeError('原移动速度记录尚未恢复，未叠加新的倍率')
            if amount == 1:
                return []
            identity = self.token(self.context()['attributes'])
            plan = []
            for name in O.MOVEMENT:
                feature = 'movement:' + name
                owner, address, fmt = self.targets(feature)[0]
                if self.token(owner) != identity:
                    raise RuntimeError('移动属性对象已变化')
                original = struct.unpack(fmt, self.read(address, struct.calcsize(fmt)))
                if not all(math.isfinite(value) and 0 < value <= 10000 for value in original):
                    raise RuntimeError('移动速度原值无效')
                plan.append((feature, tuple(value * amount for value in original)))
            completed = []
            try:
                for feature, values in plan:
                    record = self.patch(feature, values, expected_token=identity)
                    record['multiplier'] = amount
                    self.undo[feature] = record
                    completed.append(record)
            except Exception as exc:
                self._rollback_batch(completed, exc)
                raise
            return completed

    def stop_one_hit(self):
        with self.lock:
            record = self.undo.get('attack')
            if record and record.get('attack_mode') == 'one_hit':
                result = self.restore_record(record)
                del self.undo['attack']
                return result

    def switch_cooldown_modifier(self, amount):
        """Change both slot intervals and individual weapon cooldowns as one restorable group."""
        with self.lock:
            if not math.isfinite(amount) or not 0.1 <= amount <= 2:
                raise ValueError('切武器间隔范围为 0.1–2 秒')
            previous = [key for key in self.undo if key == 'cooldown' or key.startswith('weapon_cooldown:')]
            for feature in previous:
                self.restore_record(self.undo[feature])
                del self.undo[feature]
            context = self.context()
            identity = self.token(context['pawn'])
            features = ['cooldown'] + self.weapon_cooldown_features(context)
            plan = []
            for feature in features:
                owner, address, fmt = self.targets(feature)[0]
                original = struct.unpack(fmt, self.read(address, struct.calcsize(fmt)))
                if not all(math.isfinite(v) and 0 <= v <= 120 for v in original):
                    raise RuntimeError('冷却原值无效')
                values = ((amount, amount) if feature == 'cooldown'
                          else tuple(min(v, amount) for v in original))
                if values != original:
                    plan.append((feature, owner, address, fmt, self.token(owner), values))
            completed = []
            try:
                for feature, owner, address, fmt, token, values in plan:
                    if self.token(self.context()['pawn']) != identity:
                        raise RuntimeError('修改过程中玩家已切换')
                    if self.targets(feature) != [(owner, address, fmt)]:
                        raise RuntimeError('修改过程中武器已切换')
                    record = self.patch(feature, values, expected_token=token)
                    self.undo[feature] = record
                    completed.append(record)
            except Exception as exc:
                self._rollback_batch(completed, exc)
                raise
            # Existing bounded trial callers use this primary record; all changes remain in undo.
            return next((r for r in completed if r['feature'] == 'cooldown'), None)

    def status(self):
        with self.lock:
            context = self.context()
            attr = context['attributes']
            result = {'pid': self.pid, 'pawn': hex(context['pawn'])}
            for name, field in O.STATUS_ATTRIBUTES:
                result[name] = struct.unpack('<f', self.read(attr + field.current, 4))[0]
            for name, field in O.STATUS_ULTIMATE:
                result[name] = struct.unpack('<f', self.read(attr + field.current, 4))[0]
            try:
                backpack, medicine = self.medicine(context)
                result['medicine'] = struct.unpack('<i', self.read(medicine + O.MEDICINE_CURRENT.offset, 4))[0]
                result['medicine_base_max'] = struct.unpack('<i', self.read(medicine + O.MEDICINE_MAX.offset, 4))[0]
                result['medicine_extra'] = struct.unpack('<i', self.read(backpack + O.BACKPACK_MEDICINE_EXTRA.offset, 4))[0]
            except (RuntimeError, UnicodeError):
                result['medicine'] = None
            owner, address, fmt = self.targets('cooldown')[0]
            result['weapon_cooldowns'] = struct.unpack(fmt, self.read(address, struct.calcsize(fmt)))
            result['individual_weapon_cooldowns'] = [
                {'feature': feature, 'name': self.obj(config)['name'],
                 'seconds': struct.unpack('<d', self.read(config + O.CHANGE_WEAPON_COOLDOWN.offset, 8))[0]}
                for feature, config in self.weapon_cooldown_configs(context).items()]
            return result

    def start_teleport_unlock(self):
        if self.teleport_unlock is None:
            from teleport_unlock import TeleportUnlock
            self.teleport_unlock = TeleportUnlock(self)
        self.teleport_unlock.start()
        return self.teleport_unlock.status

    def pulse_teleport_unlock(self):
        if self.teleport_unlock:
            self.teleport_unlock.pulse()
            return self.teleport_unlock.status
        return ''

    def stop_teleport_unlock(self):
        if getattr(self, 'teleport_unlock', None):
            self.teleport_unlock.stop()

    def close(self):
        if getattr(self, 'teleport_unlock', None):
            self.teleport_unlock.close()
        self.auto_shachi = None
        self.auto_spirit_shadow = None
        self.auto_health = None
        self.auto_medicines.clear()
        self.auto_ultimate = None
        if self.handle:
            close_handle(self.handle)
            self.handle = None
