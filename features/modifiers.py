"""Persistent modifiers: attack / one-hit, incoming damage, movement, weapon switch cooldown, health floor lock."""
from __future__ import annotations

import math
import struct

import offsets as O
from errors import LOG, RollbackError, report_issue


class ModifiersMixin:
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

