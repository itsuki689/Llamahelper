# llama-server 生命周期

## 复核起点

本轮起点是 `67efd9e`，工作区干净；此前审查基于
`61fbe78022070783df2d0aa6545dc24505fc15af`。仓库未提供 AGENTS.md 或
CONTRIBUTING。现有 `70d4600` 已让重启的 `process_started` 触发健康检查，
但健康检查超时仍只写日志，界面一直处于 starting；健康成功日志和地址展示
仍读取之后可能修改的参数表单。Windows 下核心集成测试仍依赖 shebang 而跳过。

## 职责与状态

- `ServerManager` 独占 QProcess，每轮新建进程和停止计时器，保存参数副本及
  单调增长的 generation。进程日志、启动错误和退出信号只接受当前 QProcess
  的 sender；强杀计时器同样核对身份。重启参数在请求时复制，旧进程 finished
  并释放后才启动下一轮。显式 stop 或 shutdown 撤销待执行重启。
- `ServerLifecycle` 统一处理每轮 process_started，创建对应健康检查。只有
  checker 身份、generation、starting 状态全部匹配，且未关闭，才接受结果。
  `mark_running(generation)` 再检查代次和 QProcess 是否存活。迟到成功不能复活
  已取消的启动，迟到失败不能停止新服务。
- `ApiHealthChecker` 通过线程内 Qt 网络事件循环请求 `/health`，有总期限和
  单次请求期限。取消会唤醒重试等待，并在网络请求期间协作 abort，无强杀线程。
  结果检查、异常和无结果提前结束均有明确处理。
- `MainWindow` 的首次启动和重启共用参数校验、保存和启动入口，地址和就绪日志
  使用当轮快照。按钮由生命周期状态更新。stopping 时仍可点击停止来撤销重启。

正常路径为 `stopped → starting → running → stopping → stopped`。执行失败或
进程提前退出回到 stopped 并报告原因；健康超时/失败先回收该轮进程，再回到
stopped，用户可重新启动。应用退出禁止新启动、撤销重启、取消健康检查，并通过
事件循环异步等待服务、Benchmark、翻译和监控线程完成；仅完成的线程才允许删除。
进程停止保留 4 秒 terminate 宽限期，随后异步 kill；重复停止不会重置期限。

## 本地验证

依赖：Python 3.12、requirements.txt、pytest、pytest-timeout。无需模型或 GPU。

```powershell
python -m pytest tests/ -v -ra --timeout=120 --junitxml=build/test-results.xml
```

受限环境可将临时目录和缓存放在工作区内，`--basetemp` 应使用不存在的新目录：

```powershell
python -m pytest tests/ -v -ra --timeout=120 --basetemp=build/pytest-new-run -o cache_dir=build/pytest-cache --junitxml=build/test-results.xml
```

专门回归涵盖 checker 请求卡住时的取消/期限、旧成功和失败、旧 QProcess 和停止
计时器、取消待执行重启、表单修改后的快照、超时后重试，以及启动/运行期间关闭时
进程和全部窗口子线程的回收。

## 跨平台假服务与 CI

`tests/conftest.py` 的 fake_exe fixture 只把 Python 解释器、`-u` 和假服务脚本
插入程序参数列表，保留 `ServerParams.build_command()` 生成的模型、端口和其余
参数，不使用 shell 或 shebang。脚本位于带空格路径；模型路径也有对应验收。
Windows 使用 base interpreter，避免 venv redirector 产生超出 QProcess/Popen
所有权的额外子进程。ASR 既有进程测试也复用此 fixture，不改动其结果/排名语义。

假服务控制参数：

| 行为 | 参数或入口 |
| --- | --- |
| 延迟监听、延迟健康就绪 | `--startup-delay`、`--ready-delay`（秒） |
| 持续加载、健康请求卡住 | `--never-ready`、`--health-delay`（秒） |
| 确认健康请求已进入服务 | `--health-marker`（文件路径） |
| 监听前或就绪前提前退出 | `--exit-after`（秒）、`--exit-code` |
| 校验新一轮实际模型/端口/命令和 PID | `--record-launch`（JSON 路径） |
| 正常退出 | `GET /shutdown`，或 POSIX SIGTERM |

Windows 控制台假服务可能不响应 QProcess.terminate；此时测试实际覆盖 4 秒宽限期
后的 kill 回收。健康线程在网络请求中仍会主动取消，不等待该宽限期。

验收场景对应 `tests/test_e2e.py` 的正常首启、带新模型/端口的重启、进程创建/健康
请求中停止、503/请求卡住后的超时恢复、监听前/监听后提前退出、不可执行程序和
请求中关闭；`tests/test_shutdown.py` 另外注入旧成功/失败、旧 QProcess/计时器信号，
覆盖同端口重启、取消待执行重启和就绪地址快照。

工作流在 main、codex/** 推送及面向 main 的 PR 上运行：完整 pytest →
`tools/check_lifecycle_results.py` → PyInstaller。测试失败或门禁失败都会阻止打包；
JUnit 报告即使失败也上传为 `windows-test-results`。门禁要求关键测试名称存在，
并拒绝生命周期、健康检查、主窗口和既有 ASR 进程模块中的任何跳过/失败/错误。
门禁自身有缺测、空报告、跳过、失败、错误的回归验证。

```powershell
python tools/check_lifecycle_results.py build/test-results.xml
```

### 实际验证记录

- 旧 GitHub Windows 构建 [32559581102](https://github.com/itsuki689/Llamahelper/actions/runs/32559581102)
  对应审查基线 61fbe780：构建成功，日志为 **72 passed, 7 skipped**，其中包含
  主窗口和核心进程测试；成功不能证明这些场景已经执行。
- 本地 Windows / Python 3.12.13 / PySide6 6.11.2：原始重点模块为
  **12 passed, 5 skipped**；修复后跨平台重点模块 **48 passed, 0 skipped**，
  JUnit 门禁通过。门禁专门回归 **6 passed**；全量回归 **153 passed, 0 skipped**
  （182.71 秒），挂件启动适配另行复测 **1 passed**。
- 非 Windows 环境未在本机实际执行；在安装上述依赖后运行同一 pytest 和门禁
  命令复现。真实 llama-server、模型和 GPU 性能不在本轮假服务验证范围内。

### CI 实际暴露的 Qt 6.12 退出异常

第一次推送的 [40277492728](https://github.com/itsuki689/Llamahelper/actions/runs/40277492728)
使用 PySide6 6.12.0：全部 **153 passed, 0 skipped**，但 pytest 进程汇总后返回
非零退出码，打包正确地被阻止。在独立的 6.12 环境复现了汇总后的原生访问冲突；
仅运行既有 `tests/test_widget.py` 也可复现，健康检查模块单独运行正常。

测试会话现在持有唯一 QApplication，直到各模块 fixture 完成；关闭仍保留的测试
窗口，协作等待其进程和线程结束，断言没有活动资源，再处理 DeferredDelete、回收
Python 引用并显式销毁 QApplication。这样不再让关闭后的测试窗口及其信号引用
拖到 Python 解释器退出时销毁，也不忽略 pytest 的进程失败。

复现及检查退出码（Windows PowerShell；依赖安装到独立虚拟环境）：

```powershell
python -m pip install PySide6==6.12.0 psutil pytest pytest-timeout
python -m pytest tests/test_widget.py -q --timeout=120
$LASTEXITCODE
python -m pytest tests/ -v -ra --timeout=120 --junitxml=build/qt612-results.xml
$LASTEXITCODE
python tools/check_lifecycle_results.py build/qt612-results.xml
```

显式清理后，6.12 挂件模块 **25 passed，进程退出码 0**；6.11.2 的挂件、健康检查
和门禁兼容回归 **34 passed**。匹配 CI 依赖的 6.12 全量回归 **153 passed,
0 skipped（183.00 秒），进程退出码 0**，JUnit 门禁通过。
