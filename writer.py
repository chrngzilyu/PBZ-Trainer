"""Guarded writes: action log, token-checked patch, conditional restore and batch rollback."""
from __future__ import annotations

import datetime as dt
import json
import math
import struct

from runtime_paths import log_directory
from errors import LOG, ROLLBACK, report_issue


class WriterMixin:
    def log(self, row):
        folder = log_directory()
        folder.mkdir(parents=True, exist_ok=True)
        row = {'time_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'pid': self.pid, **row}
        with (folder / 'actions.jsonl').open('a', encoding='utf8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
            f.flush()

    def patch(self, feature, values, expected_token=None, expected_player_token=None):
        with self.lock:
            targets = self.targets(feature)
            if len(targets) != 1:
                raise RuntimeError('写入目标数量不匹配')
            owner, address, fmt = targets[0]
            if len(values) != len(struct.unpack(fmt, bytes(struct.calcsize(fmt)))) or not all(math.isfinite(v) for v in values):
                raise ValueError('数值无效')
            token = self.token(owner)
            if expected_token is not None and token != tuple(expected_token):
                raise RuntimeError('写入对象已切换，未写入')
            if expected_player_token is not None and self.token(self.context()['attributes']) != tuple(expected_player_token):
                raise RuntimeError('玩家已切换，未写入')
            before = self.read(address, struct.calcsize(fmt))
            after = struct.pack(fmt, *values)
            record = {'feature': feature, 'owner': owner, 'token': token, 'address': address, 'fmt': fmt,
                      'before': before.hex(), 'after': after.hex()}
            self.log({'action': 'write_intent', **record})
            if self.targets(feature) != targets or self.token(owner) != token:
                raise RuntimeError('玩家或目标已变化，未写入')
            if expected_player_token is not None and self.token(self.context()['attributes']) != tuple(expected_player_token):
                raise RuntimeError('记录写入前玩家已切换，未写入')
            try:
                self.raw_write(address, after)
                observed = self.read(address, len(after))
                if observed != after:
                    raise RuntimeError('游戏立即改写了设置值，请停止并检查')
                self.log({'action': 'write_verified', **record})
            except Exception as exc:
                cleanup = 'target_changed_or_unreadable'
                try:
                    current = self.read(address, len(after))
                    count = getattr(exc, 'bytes_written', 0)
                    partial = after[:count] + before[count:] if 0 < count < len(after) else None
                    if self.token(owner) == token and (current == after or current == partial):
                        self.raw_write(address, before)
                        cleanup = 'restored_after_error' if self.read(address, len(before)) == before else 'restore_readback_mismatch'
                    elif self.token(owner) == token and (feature == 'health_floor' or feature.startswith('movement:')):
                        self.restore_record(record)
                        cleanup = 'attribute_components_restored_after_error'
                except Exception as rollback_error:
                    # Step 4: the rollback itself failed; memory may be half-written. Report, don't swallow.
                    cleanup = 'rollback_failed'
                    exc.rollback_errors = getattr(exc, 'rollback_errors', []) + [str(rollback_error)]
                    exc.issue_reported = True
                    report_issue(self, ROLLBACK, feature, '写入失败后回滚也失败：' + str(rollback_error))
                try:
                    self.log({'action': 'write_failed', 'feature': feature, 'cleanup': cleanup, 'error': str(exc),
                              'rollback_errors': getattr(exc, 'rollback_errors', [])})
                except OSError as log_error:
                    report_issue(self, LOG, feature, log_error)
                raise
            return record

    def restore_record(self, record):
        with self.lock:
            owner, address = record['owner'], record['address']
            if not self.alive() or self.token(owner) != tuple(record['token']):
                raise RuntimeError('原对象已变化，未向旧地址写入')
            if record['feature'] == 'dust' and self.targets('dust') != [(owner, address, '<i')]:
                raise RuntimeError('意识尘埃所属角色已切换，未撤销到旧角色')
            if record['feature'].startswith('movement:') and self.targets(record['feature']) != [(owner, address, record['fmt'])]:
                raise RuntimeError('移动属性所属角色已变化，未向旧角色恢复')
            expected = bytes.fromhex(record['after'])
            original = bytes.fromhex(record['before'])
            current_block = self.read(address, len(expected))
            if current_block != expected:
                movement_partial = record['feature'].startswith('movement:') and any(
                    current_block[offset:offset + 4] == expected[offset:offset + 4]
                    and current_block[offset:offset + 4] != original[offset:offset + 4] for offset in (0, 4))
                if (record['feature'] == 'health_floor' or movement_partial) and record['fmt'] == '<ff' and len(expected) == len(original) == 8:
                    restored, preserved = [], []
                    for offset in (0, 4):
                        wanted = expected[offset:offset + 4]
                        prior = original[offset:offset + 4]
                        current = self.read(address + offset, 4)
                        if current == wanted and current != prior:
                            action = 'restore_movement_component_intent' if movement_partial else 'restore_floor_component_intent'
                            self.log({'action': action, 'component_offset': offset, **record})
                            if self.token(owner) != tuple(record['token']):
                                raise RuntimeError('恢复下限时对象已变化，停止写入')
                            if movement_partial and self.targets(record['feature']) != [(owner, address, record['fmt'])]:
                                raise RuntimeError('恢复移速时玩家已变化，停止写入')
                            if self.read(address + offset, 4) != wanted:
                                preserved.append(offset)
                                continue
                            self.raw_write(address + offset, prior)
                            if self.read(address + offset, 4) != prior:
                                raise RuntimeError('生命下限部分恢复读回不一致')
                            restored.append(offset)
                        elif current != prior:
                            preserved.append(offset)
                    action = 'restore_movement_components_completed' if movement_partial else 'restore_floor_components_completed'
                    self.log({'action': action, 'restored_offsets': restored,
                              'preserved_game_offsets': preserved, **record})
                    return '已撤销移速修改，保留游戏后续变化' if movement_partial else '已撤销下限修改，保留游戏后续变化'
                raise RuntimeError('游戏已改变该数值，保留游戏当前状态，未覆盖')
            self.log({'action': 'restore_intent', **record})
            if record['feature'].startswith('movement:'):
                if self.targets(record['feature']) != [(owner, address, record['fmt'])] or self.token(owner) != tuple(record['token']):
                    raise RuntimeError('恢复移速前角色已变化，未写入')
                if self.read(address, len(expected)) != expected:
                    raise RuntimeError('游戏已改变移动速度，保留游戏当前数值')
            if record['feature'] == 'dust':
                if self.targets('dust') != [(owner, address, '<i')] or self.token(owner) != tuple(record['token']):
                    raise RuntimeError('撤销前角色已切换，未写入')
                if self.read(address, len(expected)) != expected:
                    raise RuntimeError('游戏已改变意识尘埃，保留游戏当前数量')
            self.raw_write(address, original)
            if self.read(address, len(original)) != original:
                raise RuntimeError('恢复读回不一致')
            self.log({'action': 'restore_verified', **record})

    def _rollback_batch(self, completed, exc):
        """Undo a partially applied group in reverse; failed entries stay in self.undo for manual recovery."""
        failures = []
        for record in reversed(completed):
            try:
                self.restore_record(record)
                del self.undo[record['feature']]
            except Exception as rollback_error:
                failures.append(record['feature'] + '：' + str(rollback_error))
                report_issue(self, ROLLBACK, record['feature'], '批量修改回滚失败，修改仍残留：' + str(rollback_error))
        if failures:
            exc.rollback_errors = getattr(exc, 'rollback_errors', []) + failures
            exc.issue_reported = True
        return failures

    def restore_modifiers(self):
        results = {}
        for feature, record in list(self.undo.items()):
            try:
                result = self.restore_record(record)
                results[feature] = result or '已恢复'
                del self.undo[feature]
            except Exception as exc:
                results[feature] = str(exc)
        return results

