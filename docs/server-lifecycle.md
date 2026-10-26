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
进程和全部窗口子线程的回收。跨平台假服务与 CI 的验证记录在后续提交补齐。
