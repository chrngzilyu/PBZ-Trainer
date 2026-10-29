# 项目地图

Windows x64 外部实验修改器，当前适配版本与指纹在 profile.json。

- app.py：中文 Tk 面板及只读自检。
- ui_theme.py：原创深色主题、开关图像和提示。
- core.py：版本校验、玩家定位、数据写入日志、条件恢复。
- teleport_unlock.py：独立传送分派适配、三秒心跳及条件恢复；显式开启才分配和写入。
- test_teleport_unlock.py：测试进程自有 x64 代码、假内存和恢复边界；不打开游戏。
- session_effects.py：加载暂态暂停、当前玩家重绑定和攻击固定目标维护。
- test_session_persistence.py：场景切换与恢复请求回归。
- runtime_paths.py：内嵌资源路径与持久日志路径。
- test_core.py：假内存测试，不连接实际游戏。
- build_exe.py / build_exe.ps1：隔离环境构建单文件 EXE。

命令：python -m unittest discover -s . -p 'test_*.py'；python app.py --self-check；python app.py --preview；./build_exe.ps1。

保留默认关闭、版本及对象身份校验、写前日志和条件恢复；不得写入旧对象地址或覆盖游戏后续改变的值。研究结果不能冒充实机验收。材料编辑、攻击倍速与异常防护不在当前范围；不要关闭用户正在运行的其他修改器。

0.13.0 传送为未经实机验证的实验功能；仅当前背包／Item_Traversing，其他请求转交原实现。分派指针恢复须核对原 token 和当前值，发布过的适配页不得在游戏运行时释放。不得将第三方模组或完整游戏脚本打包。
