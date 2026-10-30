"""Resource refills and auto locks (health, shachi, spirit shadow, ultimate, medicines)."""
from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from typing import Callable

import offsets as O
from errors import LOG, ROLLBACK, report_issue


@dataclass(frozen=True)
class ResourceLock:
    """One "keep this resource full" lock.  Every flag below preserves an original difference
    between the locks; do not unify them without a separate plan (golden trace guards this)."""
    key: str                 # log actions auto_<key>_started / auto_<key>_stopped
    state_attr: str          # existing Game attribute holding the owner token
    owner: str               # 'attributes': context()['attributes']; 'target': targets(target)[0][0]
    target: str              # feature resolved by targets() for the 'target' owner
    start_targets: tuple     # targets() validated in start_* (after context() for 'attributes')
    fields: tuple            # ((current AttrField, max AttrField, cap), ...) read in pulse, in order
    positive: bool           # True: 0 < current (health, dead = invalid); False: 0 <= current and max > 0
    pulse_properties: str    # 'none' | 'max' | 'both': property() checks done in pulse before each read
    refill: Callable         # (game, token) -> refill write
    switched: str            # pulse error when the owner token changed
    invalid: str             # pulse error when values are invalid
    start_invalid: str = ''  # if set, start also checks Max by reflection and validates values
    before_start: str = ''   # Game method called first in start (health: stop_floor_lock)


LOCKS = {
    'health': ResourceLock(
        'health', 'auto_health', 'attributes', '', ('health',), ((O.HEALTH, O.HEALTH_MAX, 1000000),),
        True, 'none', lambda g, token: g.refill('health', expected_token=token),
        '玩家已切换，自动回血已停止', '生命状态异常或已死亡，自动回血已停止',
        start_invalid='当前生命状态无效或已死亡，未开启自动回血', before_start='stop_floor_lock'),
    'shachi': ResourceLock(
        'shachi', 'auto_shachi', 'attributes', '', ('shachi',), ((O.SHACHI, O.SHACHI_MAX, 1000000),),
        False, 'none', lambda g, token: g.refill('shachi', expected_token=token),
        '玩家已切换，杀气自动补充已停止', '杀气状态异常，自动补充已停止'),
    'spirit_shadow': ResourceLock(
        'spirit_shadow', 'auto_spirit_shadow', 'target', 'spirit_shadow', (),
        ((O.SPIRIT_SHADOW, O.SPIRIT_SHADOW_MAX, 1000000),),
        False, 'max', lambda g, token: g.refill('spirit_shadow', expected_token=token),
        '玩家已切换，锁暗源已停止', '暗源状态异常，锁暗源已停止'),
    'ultimate': ResourceLock(
        'ultimate', 'auto_ultimate', 'attributes', '', ('ultimate_charge', 'ultimate_count'),
        ((O.ULTIMATE_CHARGE, O.ULTIMATE_CHARGE_MAX, 1000000), (O.ULTIMATE_COUNT, O.ULTIMATE_COUNT_MAX, 100)),
        False, 'both', lambda g, token: g.refill_ultimate(expected_token=token),
        '玩家已切换，精纯杀气锁定已停止', '精纯杀气状态无效，锁定已停止'),
}


def _valid(spec, current, maximum, cap):
    if not math.isfinite(current):
        return False
    if spec.positive:
        return 0 < current <= maximum <= cap
    return 0 <= current <= maximum <= cap and not maximum <= 0


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

    def _lock_owner(self, spec):
        if spec.owner == 'target':
            return self.targets(spec.target)[0][0]
        return self.context()['attributes']

    def _lock_start(self, spec):
        with self.lock:
            if spec.before_start:
                getattr(self, spec.before_start)()
            owner = self._lock_owner(spec)
            for feature in spec.start_targets:
                self.targets(feature)
            if spec.start_invalid:
                field, field_max, cap = spec.fields[0]
                self.property(self.obj(owner)['class'], *field_max.args)
                current = struct.unpack('<f', self.read(owner + field.current, 4))[0]
                maximum = struct.unpack('<f', self.read(owner + field_max.current, 4))[0]
                if not _valid(spec, current, maximum, cap):
                    raise RuntimeError(spec.start_invalid)
            token = self.token(owner)
            self.log({'action': f'auto_{spec.key}_started', 'token': token})
            setattr(self, spec.state_attr, token)

    def _lock_stop(self, spec):
        with self.lock:
            active = getattr(self, spec.state_attr) is not None
            setattr(self, spec.state_attr, None)
            if active:
                try:
                    self.log({'action': f'auto_{spec.key}_stopped'})
                except OSError as log_error:
                    report_issue(self, LOG, '', log_error)

    def _lock_pulse(self, spec):
        with self.lock:
            token = getattr(self, spec.state_attr)
            if token is None:
                return False
            try:
                owner = self._lock_owner(spec)
                if self.token(owner) != token:
                    raise RuntimeError(spec.switched)
                if spec.pulse_properties != 'none':
                    cls = self.obj(owner)['class']
                values = []
                for field, field_max, cap in spec.fields:
                    if spec.pulse_properties == 'both':
                        self.property(cls, *field.args)
                    if spec.pulse_properties != 'none':
                        self.property(cls, *field_max.args)
                    current = struct.unpack('<f', self.read(owner + field.current, 4))[0]
                    maximum = struct.unpack('<f', self.read(owner + field_max.current, 4))[0]
                    if not _valid(spec, current, maximum, cap):
                        raise RuntimeError(spec.invalid)
                    values.append((current, maximum))
                if any(current < maximum - 0.001 for current, maximum in values):
                    spec.refill(self, token)
                    return True
                return False
            except Exception:
                setattr(self, spec.state_attr, None)
                raise

    def start_auto_ultimate(self):
        return self._lock_start(LOCKS['ultimate'])

    def stop_auto_ultimate(self):
        return self._lock_stop(LOCKS['ultimate'])

    def pulse_auto_ultimate(self):
        return self._lock_pulse(LOCKS['ultimate'])

    def start_auto_health(self):
        return self._lock_start(LOCKS['health'])

    def stop_auto_health(self):
        return self._lock_stop(LOCKS['health'])

    def pulse_auto_health(self):
        return self._lock_pulse(LOCKS['health'])

    def start_auto_shachi(self):
        return self._lock_start(LOCKS['shachi'])

    def stop_auto_shachi(self):
        return self._lock_stop(LOCKS['shachi'])

    def pulse_auto_shachi(self):
        return self._lock_pulse(LOCKS['shachi'])

    def start_auto_spirit_shadow(self):
        return self._lock_start(LOCKS['spirit_shadow'])

    def stop_auto_spirit_shadow(self):
        return self._lock_stop(LOCKS['spirit_shadow'])

    def pulse_auto_spirit_shadow(self):
        return self._lock_pulse(LOCKS['spirit_shadow'])
