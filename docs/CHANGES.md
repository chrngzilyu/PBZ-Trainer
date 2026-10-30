# 改动记录（fork 开发）

本文件逐条记录 fork（chrngzilyu/PBZ-Trainer）相对上游 sdkdsd/PBZ-Trainer v0.13.1 的每一处改动：位置、改了什么、为什么、对行为与兼容性的影响。

## 第 4 步：异常处理与错误提示（分支 `step4-error-handling`）

方案依据：先盘点约 60 处 `except`，确认真正危险的是 4 处回滚失败被 `pass` 吞掉，以及 `session_effects` 把严重错误降级为“暂缓”。本步只做错误分级与提示，不改任何偏移、写入目标或功能逻辑。

### 1. 新增 `errors.py`
- **改了什么**：新增错误类别 `TargetGone`（目标失效）、`ReadError`（读失败）、`WriteError`（写失败）、`RollbackError`（回滚失败，带 `rollback_errors` 列表）、`VersionError`（版本校验失败），以及日志异常类别 `LOG`。新增线程安全、有界（默认 100 条）的 `IssueQueue`，提供 `report / drain / read_failed / read_ok`。新增纯函数 `classify(exc)`、`summarize_issues(issues) -> (颜色, 文本)`、`recent_lines()`、`report_issue(game, ...)`。
- **为什么**：给错误分级，让 core 只入队、不碰界面；界面再按等级显示颜色。纯函数可以在没有 Tk 的环境里单测。
- **影响/兼容**：所有新异常都继承 `RuntimeError`，原有 `except RuntimeError` 和测试照常接住。没有归类的普通 `RuntimeError`/`ValueError`（如“对象已切换”“数值无效”）按“目标失效”处理：底部消息行照旧显示，不进状态栏。`report_issue` 在对象没有 `issues` 属性时会自动创建（测试用的假对象不走 `__init__`）。

### 2. `core.py`
| 位置 | 改了什么 | 为什么 | 影响/兼容 |
|---|---|---|---|
| 顶部 import（约第 17 行） | 引入 `errors` 中的类别与 `report_issue` | 上报需要 | 无 |
| `DataWriteError`（第 48 行） | 父类从 `RuntimeError` 改为 `WriteError` | 纳入“写失败”类别 | `WriteError` 仍是 `RuntimeError`，原有捕获不受影响 |
| `Game.__init__`（第 93 行） | 新增 `self.issues = IssueQueue()` | 存放待界面取走的错误 | 无 |
| `Game.__init__` 版本校验（第 109–127 行） | 哈希、映像头、64 位、映像大小、名称池校验失败改抛 `VersionError` | 归入红色“版本校验失败” | 消息文本不变，仍拒绝连接 |
| `Game.read`（第 143 行） | 读失败改抛 `ReadError` | 归入“读失败”，界面连续 20 次才提示 | 消息文本不变 |
| `Game.patch` 写失败清理（约第 490–502 行，原 488 行 `except Exception: pass`） | 回滚本身失败时：`cleanup` 记为 `rollback_failed`，在原异常上附 `rollback_errors` 和 `issue_reported=True`，上报 `ROLLBACK`；`write_failed` 日志新增 `rollback_errors` 字段 | **原来回滚失败被静默吞掉，日志还误记为 `target_changed_or_unreadable`，内存可能停在“改了一半”的状态** | 仍然抛出**原来的**异常（类型和消息都不变）；只是不再隐瞒回滚失败 |
| `refill_ultimate` 回滚（第 610 行附近） | 回滚失败时除写日志外，再上报 `ROLLBACK` | 原来只进日志，界面不知道 | 行为不变，多一条提示 |
| `stop_floor_lock` 失败（第 770 行，原 761 行） | `RuntimeError` 改为 `RollbackError`，附 `[原因]` | 让 session_effects 和界面识别为严重错误 | 消息文本不变 |
| 6 处 `log()` 外的 `except OSError: pass`（第 632/671/766/796/834/872 行）和 patch 内一处 | 改为上报 `LOG` | 原来日志写不进去时审计记录丢了也没人知道 | 不抛异常，只计数，状态栏显示灰色“日志异常 N” |
| `incoming_modifier` / `movement_modifier` / `switch_cooldown_modifier` 回滚（第 977/1023/1068 行，原 972/1023/1073 行的 `except Exception: pass`） | 三处重复代码合并为新方法 `_rollback_batch(completed, exc)`（第 1073 行）：逐条逆序恢复，失败的条目**保留在 `self.undo`**，上报 `ROLLBACK` 并在原异常上附 `rollback_errors` | **原来回滚失败被吞掉，用户不知道还有残留修改** | 原异常照常抛出；成功恢复的条目照旧从 undo 删除，行为与原来一致；保留的条目可以通过“恢复残留修改”再次恢复，不会重复写（有测试覆盖） |

### 3. `session_effects.py`
| 位置 | 改了什么 | 为什么 | 影响/兼容 |
|---|---|---|---|
| import、`__init__` | 新增 `floor_disabled` 标志 | 严重错误后停止重试 | 无 |
| 新方法 `_serious()`（第 61 行） | 只有 `RollbackError`、`WriteError` 或带 `rollback_errors` 的异常算严重错误：上报（已上报的不重复报）并返回类别 | 原来所有异常都显示为“暂缓写入，等待角色就绪”，连“原下限未能自动恢复”也被说成暂缓 | 目标失效、角色未就绪等仍走原来的“暂缓”文案 |
| `floor_step` 异常分支（第 96 行附近） | 严重错误时 `floor_disabled=True`，状态显示“强锁血：已停用，回滚失败，可能有残留修改（…）”或“…写入失败（…）”；之后不再重试 | 严重错误不能被掩盖，也不应反复写 | 需要用户重新勾选强锁血才会重新开始（界面会自动取消勾选） |
| `attack_step` 异常分支（第 144 行附近） | 严重错误时清空 `attack_request`，状态显示“攻击倍率：已停用，…” | 同上 | 需要重新应用攻击倍率 |

### 4. `teleport_unlock.py`
| 位置 | 改了什么 | 为什么 | 影响/兼容 |
|---|---|---|---|
| 第 15 行 import；`heartbeat` 异常分支（第 386 行，原 383 行 `pass`） | 心跳失败后 `disarm()` 也失败时，上报黄色 `WRITE`（功能名 `teleport`） | 原来直接忽略 | 仍依赖适配器自身的三秒失效兜底，行为不变，只是多一条提示 |

### 5. `app.py`
| 位置 | 改了什么 | 为什么 | 影响/兼容 |
|---|---|---|---|
| 顶部 | 引入 `errors`；新增 `ISSUE_COLORS`（灰 `#9aa0ab`、黄 `#e0b84f`、红 `#e0565b`） | 状态栏配色 | 无 |
| 界面底部（第 252 行起） | 新增错误状态栏：彩色文字 +“恢复残留修改”“详情”“已知晓”三个按钮 | 原来只有一行消息，下一条就把上一条覆盖 | 原消息行保留，显示内容不变 |
| 新方法 `fail(exc)`（第 270 行） | 18 处 `self.message.set(str(exc))` 统一改为 `self.fail(exc)`：先照旧显示消息，再分级——目标失效不进状态栏；读失败计连续次数；其余入队；core 已上报的不重复 | 统一分级 | 消息行显示的文字与原来完全相同 |
| 新方法 `drain_issues()`（第 281 行） | 在现有 1 秒 `tick()` 末尾取出界面和 core 两个队列的错误，按最高等级上色，显示各类计数和最近一条；回滚失败首次出现时弹窗 | 不引入线程，契合现有 `after` 轮询 | 无额外线程 |
| `show_issues()` / `acknowledge_issues()`（第 299/303 行） | “详情”弹出最近 5 条；“已知晓”清空状态栏、重置弹窗标记 | 红色常驻，必须由用户确认 | — |
| `toggle_floor`、`health_tick`（第 474/529 行） | 重新勾选强锁血时清除 `floor_disabled`；强锁血因严重错误停用时自动取消勾选 | 界面与实际状态一致 | — |
| `tick`（第 654/661 行） | 刷新成功时把连续读失败计数清零；攻击倍率“已停用”也显示到消息行；末尾调用 `drain_issues()` | 读失败只有连续 20 次才提示 | — |

### 6. 测试
- `test_core.py` 新增 `RollbackFailureReporting`（6 个）：patch 写入和回滚都失败；incoming / movement / cooldown 三个批量写入回滚失败时 undo 保留并上报；回滚失败后再执行 restore 不会重复写；日志 `OSError` 被计数。
- 新增 `test_errors.py`（12 个）：分类映射、消息文本不变、队列有界、8 线程并发、读失败阈值与清零、懒创建队列、状态栏颜色与文本、最近 5 条、session_effects 中“角色未就绪仍显示暂缓”“回滚失败不再降级且不重复上报”“攻击维持写入失败后停用”。
- 本机（Linux + `/tmp/winshim` 替身）：`test_core` + `test_errors` 共 78 个全部通过；`test_teleport_unlock` 改动前后都是同样 17 个错误（需要真实 Windows API）。
- **只能在 Windows 上验证**：`app.py` 的状态栏、按钮、弹窗（本机没有 tkinter）；`test_session_persistence.py`；`test_teleport_unlock.py`；实机游戏中的回滚失败场景。

## 第 8 步：GitHub Actions Windows 自动测试（分支 `step8-ci`）

方案依据：`/workspace/plan-step8-ci.md`。本步只加 CI 与补齐构建输入清单，不改任何业务逻辑、偏移或功能。

### 1. 新增 `.github/workflows/ci.yml`
- **改了什么**：新增 workflow `CI`。触发：`push`（分支 `main`/`master`/`step*` 与标签 `v*`）、`pull_request`、`workflow_dispatch`。权限仅 `contents: read`。两个 job，均在 `windows-latest` + Python 3.13：
  - **test**（每次必跑）：`python -m unittest discover -s . -p "test_*.py" -v`；`python app.py --self-check --report ui-check.json`；无论成败上传 `ui-check.json` artifact（保留 14 天）。
  - **build**（可选）：仅在 `workflow_dispatch` 或推送 `v*` 标签时运行；`pip install -r requirements-build.txt` 后执行 `python build_exe.py --output dist`；上传 `dist/*.exe` 与 `.build/onefile/build-report.json`（保留 14 天）。**不**自动创建 Release。
- **为什么**：本机是 Linux/Mac，完整测试集（含 teleport、session、Tk self-check）只能在 Windows 上跑；后续重构需要这层安全网。打包与测试分离，避免每次小改都打 10MB+ EXE。
- **影响/兼容**：不改运行时代码。CI 绿只说明单元测试与自检通过，**不等于**实机游戏验收。公开 fork 上 Actions 分钟一般够用；不注入密钥、不连游戏。

### 2. `build_exe.py` 的 `inputs` 列表
- **改了什么**：在 `inputs` 中补上第 4 步新增的 `errors.py`（插在 `core.py` 与 `ui_theme.py` 之间）。
- **为什么**：PyInstaller 会因 import 把 `errors.py` 打进包，但构建报告的 `input_sha256` 原先漏记该文件；首次跑 build job 时若漏记也不利于审计。
- **影响/兼容**：打包产物内容不变（本来就会被 import 进来）；仅报告多一条哈希。本地构建命令不变。

### 3. CI 首次跑红后的最小修复：`test_session_persistence.py`
- **改了什么**：假 Panel 夹具（`Panel.__new__`，不走 `__init__`）补上第 4 步新增的 `issues` / `issue_log` / `rollback_warned` / `issue_text` / `issue_label`。
- **为什么**：`tick()` 末尾会调用 `drain_issues()` 与 `issues.read_ok()`；夹具原先没这些字段，Windows CI 上 6 个 session 用例报 `AttributeError`。本机 Linux 从未跑过该文件，第 4 步漏测。
- **影响/兼容**：只改测试夹具，不改运行时代码；真实 `Panel.__init__` 本来就会初始化这些字段。

### 4. CI 打包：控制台编码
- **改了什么**：build job 的 `Build one-file EXE` 步骤增加环境变量 `PYTHONIOENCODING=utf-8`。
- **为什么**：PyInstaller 已成功产出 EXE 与 `build-report.json`，但 `build_exe.py` 最后 `print` 含中文路径时，Actions 托管 runner（cp1252）报 `UnicodeEncodeError`，步骤以非零退出，artifact 未上传。
- **影响/兼容**：只影响 CI 环境；本机中文 Windows 控制台（GBK）本来就能打印。不改 `build_exe.py` 业务逻辑。

## 第 1–3 步：重构（分支 `step1-3-refactor`）

方案依据：`/workspace/plan-step1-3-refactor.md`。目标是“对玩家无可见变化”：每个提交都必须让 golden trace 与 C0 基线逐字节一致，原有 110 个用例不改。

### C0：行为快照基线（golden trace）
- **改了什么**：新增 `test_golden_trace.py` 与 `tests_golden/trace.json`。夹具在内存里构造一套完整的假游戏映像（名称池、UObject 头、反射字段链、World→玩家指针链、武器 TMap、背包子系统 TMap、药品 TMap），让**真实的** `Game` 代码（`obj/token/property/context/map_rows/patch/restore_record/...`）在上面运行；只替换最底层的 `read/raw_write/log/alive`，并给 `property` 套记录器。按顺序记录每次读（地址、长度）、写（地址、字节）、写失败注入、`property` 参数、日志行（不含时间和 pid）、每一步的返回值或异常类型与中文文案、`rollback_errors`、上报的 issue、最终 undo。共 30 个场景：status、全部 targets、TMap/武器/背包/药品解析、refill ×5 与 refill_ultimate、五种锁的 start/pulse（补/不补/异常/玩家切换）/stop、attack/one_hit、incoming、movement（含部分分量恢复）、cooldown、生命下限锁（修复、部分恢复、死亡、切换、恢复失败）、dust、patch 守卫、写入中途失败与回滚失败。
- **为什么**：重构是“纯搬家 + 等价替换”，需要一个比现有单测更细的安全网，能发现读写顺序、地址、调用次数、文案的任何漂移。
- **影响**：只加测试，不改运行时代码。用例数 110 → 112。基线在旧代码（`step8-ci` 7758d9c）上生成。只有有意且在本文档写明原因的行为变化才允许 `python test_golden_trace.py --update` 重录。

### C1：新增偏移表 `offsets.py`（暂无引用）
- **改了什么**：新增 `offsets.py`。`Field(owner, name, offset, kind, size)` 记录一个反射字段，`.args` 直接给 `property(cls, *field.args)` 用；`AttrField` 专门表示 FGameplayAttributeData，`.base`（offset+8，写 Base+Current 的 `'<ff'` 起点）和 `.current`（offset+12，读 Current）把原代码里隐式的 +8/+12 约定变成有名字的属性。表内有 BasicAttributeSet 的 18 个字段、Pawn/武器/背包/药品字段、固定指针链（0x228/0x38/0x30/0x2f0/0x358/0x758/0x9e0）、业务上限 `LIMITS`，以及 `MOVEMENT`/`INCOMING`/`RESOURCE_FIELD`/`RESOURCE_MAX`/`STATUS_*` 查找表。未经反射核对的武器 config 指针 0xe8 单独记为 `UNVERIFIED_WEAPON_CONFIG_PTR`，本次不加核对。新增 `test_offsets.py`（4 个）：`0x58+12==0x64`、`0x908+12==0x914`、精纯杀气 `+16/+28` 推算等恒等式；属性字段不重名、不重叠；其它类字段按所属类不重叠；查找表内容。
- **为什么**：同一偏移最多重复 9 处，还有 0x64/0x74/0x914/+16/+28 这类推算地址，游戏更新时最容易漏改。
- **影响**：本提交还没有任何代码引用它，运行时行为不变；golden trace 一致。用例 112 → 116。

### C2：core / session_effects / teleport 改为按字段名引用偏移
- **改了什么**：
  - `core.py`：`context` 的指针链、`weapon_cooldown_configs`、`backpack`、`medicine`、`medicine_state`、`targets`、`refill`、`refill_ultimate`、四种资源锁 pulse、生命下限锁、`modifier('health_floor')`、`movement_modifier`、`status` 中的偏移字面值全部改成 `offsets.py` 的字段：`property(cls, *F.args)`、写入用 `F.base`、读 Current 用 `F.current`。推算地址也改成有名字的写法：`0x64/0x74 → HEALTH.current/HEALTH_MAX.current`、`owner+0x914 → SPIRIT_SHADOW_MAX.current`、精纯杀气 `offset+16/+28 → *_MAX.args / *_MAX.current`、暗源 `address+4 →（SPIRIT_SHADOW.current − .base）`。CDO 标志 0x10 → `RF_CLASS_DEFAULT_OBJECT`。
  - `session_effects.py`：`_attributes` 里的 0x64/0x74 → `HEALTH.current/HEALTH_MAX.current`。
  - `teleport_unlock.py`：**只改第 172 行**这一处 `+0x64 → HEALTH.current`，并新增 `from offsets import HEALTH`；其余逻辑和私有布局（RVA、机器码、背包库存偏移）不动，默认关闭、版本校验、token 校验、写前日志、条件恢复都原样保留。
  - `test_offsets.py` 新增守卫测试：core.py 和 session_effects.py 里不得再出现已迁移的十六进制偏移。
- **未迁移（有意保留）**：Win32 访问掩码 0x1000/0x410/0x438、PE 头 0x8664、地址范围、FName 的 `0xffffffff` 掩码、UObject/FProperty 结构内部布局、业务范围上限（1000000/100/50/2000000000，已登记在 `LIMITS` 但本次不替换，避免改动面过大）。
- **为什么**：游戏更新时只需改 `offsets.py` 一处。
- **影响**：生成的地址、读取尺寸、`property()` 参数和调用顺序完全不变；golden trace 逐字节一致。用例 116 → 117。

### C3：拆出 `win32.py` 与 `memory.py`
- **改了什么**：
  - 新增 `win32.py`：从 core 原样搬来 `K`、`api()`、`open_process/close_handle/rpm/wpm/snapshot/query_path/exit_code`、`ProcessEntry`、`process_first/next`、`image_path()`、`discover()`。
  - 新增 `memory.py`：`DataWriteError` 与 `ProcessMixin`（`alive/read/qword/raw_write`，代码一字未改）。
  - `core.py`：`class Game(ProcessMixin)`，并**再导出** `api, open_process, close_handle, image_path, wpm, rpm, snapshot, discover, ProcessEntry, K, DataWriteError` 等名字，所以 `teleport_unlock.py`、`app.py`、测试的 import 都不用改。去掉了已不再使用的 `ctypes/Path/ReadError/WriteError` 导入。
- **为什么**：Win32 绑定和进程内存读写是最底层，单独成文件后可以单独做替身测试，core 也更短。
- **影响**：方法仍挂在同一个 `Game` 类上（mixin），测试对 `g.read/g.raw_write` 的补丁照常生效；golden trace 一致。
