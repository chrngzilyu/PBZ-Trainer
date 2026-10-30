"""Resource refills and auto locks (health, shachi, spirit shadow, ultimate, medicines)."""
from __future__ import annotations

import math
import struct

import offsets as O
from errors import LOG, ROLLBACK, report_issue


class ResourcesMixin:
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

