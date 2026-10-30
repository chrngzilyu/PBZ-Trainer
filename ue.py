"""Unreal Engine reflection: FName pool, UObject headers, FProperty checks, player context, TMap rows."""
from __future__ import annotations

import struct

import offsets as O


class UEMixin:
    def refresh_pool(self):
        self.pool = self.base + self.profile['name_pool_rva']
        block, cursor = struct.unpack('<II', self.read(self.pool + 8, 8))
        if not 0 <= block < 8192 or not 0 < cursor <= 131072:
            raise RuntimeError('名称池布局不匹配')
        self.blocks = struct.unpack('<' + 'Q' * (block + 1), self.read(self.pool + 16, 8 * (block + 1)))

    def name(self, index):
        if index in self.names:
            return self.names[index]
        block, offset = index >> 16, (index & 65535) * 2
        if block >= len(self.blocks):
            self.refresh_pool()
        if block >= len(self.blocks):
            raise RuntimeError('对象名称索引无效')
        header = struct.unpack('<H', self.read(self.blocks[block] + offset, 2))[0]
        length, wide = header >> 6, header & 1
        if not 0 < length <= 1023 or offset + 2 + length * (2 if wide else 1) > 131072:
            raise RuntimeError('对象名称布局无效')
        value = self.read(self.blocks[block] + offset + 2, length * (2 if wide else 1)).decode('utf-16-le' if wide else 'utf8')
        self.names[index] = value
        return value

    def obj(self, address):
        data = self.read(address, 40)
        vtable, flags, index, cls, name, number, outer = struct.unpack('<QIiQIIQ', data)
        if not self.base <= vtable < self.base + self.profile['image_size'] or not 0 <= index < 10000000:
            raise RuntimeError('对象身份校验失败')
        return {'address': address, 'index': index, 'class': cls, 'outer': outer, 'flags': flags, 'name': self.name(name)}

    def token(self, address):
        obj = self.obj(address)
        return (address, obj['index'], obj['class'], obj['outer'])

    def class_name(self, address):
        return self.obj(self.obj(address)['class'])['name']

    def property(self, cls, wanted, expected_offset, expected_type, expected_size):
        key = (cls, wanted)
        if key not in self.property_cache:
            seen = set()
            current = cls
            result = None
            while current and current not in seen and len(seen) < 32:
                seen.add(current)
                field = self.qword(current + 80)
                fields = set()
                while field and field not in fields and len(fields) < 1500:
                    fields.add(field)
                    data = self.read(field, 128)
                    name = self.name(struct.unpack_from('<I', data, 32)[0])
                    if name == wanted:
                        kind = self.qword(struct.unpack_from('<Q', data, 8)[0] + 24)
                        size = struct.unpack_from('<I', data, 52)[0]
                        offset = struct.unpack_from('<I', data, 68)[0]
                        result = (offset, kind, size)
                        break
                    field = struct.unpack_from('<Q', data, 16)[0]
                if result:
                    break
                current = self.qword(current + 64)
            if not result:
                raise RuntimeError('缺少已核对字段：' + wanted)
            self.property_cache[key] = result
        if self.property_cache[key] != (expected_offset, expected_type, expected_size):
            raise RuntimeError('字段布局变化，已停止写入：' + wanted)
        return expected_offset

    def context(self):
        if not self.alive():
            raise RuntimeError('游戏已退出')
        world = self.qword(self.base + self.profile['world_rva'])
        if self.class_name(world) != 'World':
            raise RuntimeError('当前世界尚未就绪')
        gi = self.qword(world + O.WORLD_GAME_INSTANCE)
        locals_array, count, capacity = struct.unpack('<Qii', self.read(gi + O.GI_LOCAL_PLAYERS, 16))
        if not 0 < count <= capacity <= 16:
            raise RuntimeError('本地玩家尚未就绪')
        local = self.qword(locals_array)
        controller = self.qword(local + O.PLAYER_CONTROLLER)
        pawn = self.qword(controller + O.CONTROLLER_PAWN)
        if not pawn or pawn != self.qword(controller + O.CONTROLLER_PAWN_CHECK):
            raise RuntimeError('玩家对象正在切换')
        player = self.obj(pawn)
        if player['flags'] & O.RF_CLASS_DEFAULT_OBJECT:
            raise RuntimeError('不能修改默认对象')
        attributes = self.qword(pawn + O.PAWN_ATTRIBUTE_SET)
        attr = self.obj(attributes)
        if attr['outer'] != pawn or self.class_name(attributes) != 'BasicAttributeSet':
            raise RuntimeError('玩家属性归属校验失败')
        return {'world': world, 'controller': controller, 'pawn': pawn, 'attributes': attributes}

    def map_rows(self, address):
        data = self.read(address, 80)
        array, count, capacity = struct.unpack_from('<Qii', data)
        bits, max_bits = struct.unpack_from('<ii', data, 40)
        if not 0 <= count <= capacity <= 4096 or bits != count or not bits <= max_bits <= 8192:
            raise RuntimeError('映射布局不匹配')
        if not count:
            return []
        secondary = struct.unpack_from('<Q', data, 32)[0]
        flags = self.read(secondary, (bits + 7) // 8) if secondary else data[16:32]
        if len(flags) * 8 < bits:
            raise RuntimeError('映射分配标记无效')
        slots = self.read(array, count * 24)
        rows = []
        for i in range(count):
            if flags[i // 8] & (1 << (i % 8)):
                key, value = struct.unpack_from('<QQ', slots, i * 24)
                rows.append((key, value))
        return rows

