# 影之刃零修改器

Windows 64 位实验修改器，支持深色中文界面与单文件 EXE。连接时校验游戏版本，所有修改功能默认关闭。

**[下载 v0.12.0 单文件 EXE](https://github.com/sdkdsd/PBZ-Trainer/releases/tag/v0.12.0)** · [验证范围](VALIDATION.md) · [更新记录](CHANGELOG.md)

## 下载与使用

1. 打开上方 Release 页面，在 Assets 中下载 `PBZ-Trainer-v0.12.0.exe`。
2. 进入游戏中可以操作角色的场景，双击 EXE；程序会尝试只读连接。
3. 按需开启功能。无需安装 Python，也不需要额外的配置文件或 runtime 文件夹。

当前仅支持 **Steam build 26266538** 的已核对 Windows x64 主程序。其他版本会拒绝连接，需重新核对后适配。

## 功能

| 功能 | 范围与说明 |
| --- | --- |
| 强锁血 | 维持生命下限；普通受击不掉血已有反馈 |
| 自动回血 | 备用方案，与强锁血互斥，不能保证阻止一击致死 |
| 锁杀气、暗源、精纯杀气 | 消耗后约每 0.2 秒检查并补满，不改变容量 |
| 锁葫芦／酒次数 | 按需补满已有药品次数；图标可能需切换快捷药品刷新 |
| 单次补充 | 回满生命、补充葫芦、补满精纯杀气 |
| 玩家基础攻击倍率 | 0.1–20 倍，实际伤害范围仍待实战验证 |
| 一键秒杀 | 原始基础攻击 ×10000，与普通攻击倍率相互替代、不叠乘；并非所有规则都保证秒杀 |
| 怪物伤害倍率 | 0.1–20 倍，实际调整玩家物理／元素承伤；特殊真实伤害不包含 |
| 移动加速 | 1–5 倍，行走／跑步／冲刺／蹲行；1.5 倍已有实机反馈，冲刺重算后可能需重新应用 |
| 武器切换冷却 | 0.1–2 秒，同时处理角色间隔与当前装备武器冷却；换装备后需重新应用 |
| 意识尘埃数量 | 0–2,000,000,000，单次设置，可撤销本窗口上次设置；游戏显示可能在获得／消耗后刷新 |

资源锁定及部分倍率仍属实验功能，具体已验证范围见 [VALIDATION.md](VALIDATION.md)。未加入材料数量编辑、霸体、异常免疫或攻击倍速。

## 恢复与日志

“恢复移速”仅撤销移动加速。底部“恢复倍率、移速、冷却与锁血”会同时恢复本窗口的生命下限。正常关闭窗口会停止资源锁，并尝试恢复本窗口记录的修改；遇到角色变化或游戏已改值时，优先保留游戏后续变化。

意识尘埃可能随自动存档保留，不随关闭回退。新窗口不会接管其他修改器已开启的锁定，请勿同时使用多个面板控制同一项目。

EXE 的动作日志保存到 `%LOCALAPPDATA%/PBZTrainer/logs/actions.jsonl`；源码模式保存在源码目录的 `logs/`。日志不会随单文件临时解包目录消失。

## 从源码运行

使用带 Tk 的 Windows x64 Python 3.13：

```powershell
python app.py
```

仅预览界面、不连接游戏：

```powershell
python app.py --preview
```

## 构建单文件 EXE

```powershell
python -m venv .build-venv
& ./.build-venv/Scripts/python.exe -m pip install -r requirements-build.txt
./build_exe.ps1
```

输出为 `dist/影之刃零修改器.exe`。构建使用隔离环境和 PyInstaller onefile/windowed，包含 Tk、版本配置、原创图标与运行环境许可；构建工具不会调用游戏修改功能。

## 检查

```powershell
python test_core.py
python app.py --self-check --report ui-check.json
```

自检不连接游戏。加 `--check-game` 可额外校验当前游戏版本并只读读取状态；该检查的写入入口会被阻断。EXE 也支持这些参数。

运行环境许可证可在界面“使用说明”中查看，原文见 [THIRD_PARTY_NOTICES.txt](assets/THIRD_PARTY_NOTICES.txt)。
