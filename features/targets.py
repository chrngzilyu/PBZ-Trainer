"""Feature targets: weapons, backpack and medicines, reflected write targets, dust and status reads."""
from __future__ import annotations

import struct

import offsets as O


class TargetsMixin:
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

