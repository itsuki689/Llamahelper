# LlamaHelper

Windows 11 上的 **llama.cpp GUI 调参与性能测试工具**：llama-server.exe 的图形化启动器、
参数配置器和自动化 Benchmark 工具，面向 GGUF / Qwen MoE 模型与 NVIDIA CUDA GPU。

深色 Apple 风格桌面界面（PySide6），可打包为单个 exe。

## 功能

- **Dashboard**：GPU 利用率 / 显存 / 温度 / 功耗 / 频率，CPU / RAM，llama-server 进程
  CPU / RAM，Generation 与 Prompt tok/s 大数字实时展示；启动 / 停止 / 重启、
  打开 WebUI、复制 API URL、PID 显示、防重复启动、端口占用检测。
- **Parameters**：Context Size、Threads、Batch Size、GPU Layers（Auto 使用
  llama.cpp 原生 `-ngl auto`）、Flash Attention 开关、KV Cache K/V 类型、
  `--cpu-moe` 与 `ncmoe`、MTP 投机解码（`--spec-type draft-mtp` +
  `--spec-draft-n-max`）、Host / Port / Alias、额外自定义参数；高级模式
  （折叠面板）提供 ubatch-size、threads-batch、parallel、continuous
  batching、no-mmap、mlock、no-warmup、cache-reuse、RoPE scaling /
  freq-scale、temp / top-k / top-p / min-p 默认采样参数；下方实时显示
  最终生成的完整命令；GGUF 文件支持拖放；路径与参数自动记忆
  （程序目录 `llama_helper_config.json`）。
- **Benchmark**：自定义扫描参数（n-cpu-moe / GPU Layers / Batch / ubatch /
  Threads / Context / MTP draft n-max）+ 起始 / 结束 / 步进；扫描 ncmoe
  时可选 `--cpu-moe` 基准轮。每轮自动：启动服务 → 等待 API 就绪 →
  **预热请求（不计入结果）** → 发送固定 prompt 生成指定 token → 记录
  generation/prompt tok/s、显存峰值、功耗峰值、RAM 峰值、OOM / 启动失败 →
  关闭服务。结果表格按 generation tok/s 排名并标记最快配置，可导出 CSV。
- **Logs**：QProcess 实时输出 stdout/stderr，按级别着色（错误红 / 警告黄 /
  性能绿 / 加载蓝），自动滚动可暂停，可保存。
- **Presets**：多组 JSON 预设（默认含 Qwen35B Balanced / Max Speed /
  Long Context / Low VRAM），保存于程序目录 `presets.json`。

## 目标环境

- Windows 11 + Python 3.12
- llama.cpp CUDA 构建：`llama-b10453-bin-win-cuda-13.3-x64`（运行时需配套
  `cudart-llama-bin-win-cuda-13.3-x64`，把两者解压到同一目录后选择其中的
  `llama-server.exe`）。GPU Layers 的 Auto 依赖较新版本 llama.cpp 的
  `-ngl auto`；如旧构建不支持，取消 Auto 勾选后填固定层数即可
- 默认参数按 5070 Ti Laptop 12GB + 9955HX + 64GB DDR5 调优（threads=16）

## 开发运行

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## 打包 exe

```bat
build.bat
```

产物为 `dist\LlamaHelper.exe`（单文件、无控制台窗口）。
`presets.json` 与 `llama_helper_config.json` 保存在 exe 同目录。

## 项目结构

```
main.py                入口
ui/                    主题、自定义控件、五个页面、主窗口
core/                  参数/命令构建、QProcess 进程管理、性能日志解析
monitoring/            nvidia-smi GPU 监控、psutil 系统监控（线程，可降级）
benchmark/             ncmoe 自动扫描 Benchmark 执行器
config/                配置持久化与预设管理
tests/                 单元测试 + 假 llama-server 集成测试
build.bat              PyInstaller 一键打包
```

## 说明

- 不使用 `shell=True`，路径全部走 `pathlib`，带空格路径安全。
- 所有耗时操作（进程 I/O、健康检查、nvidia-smi、psutil、Benchmark）均在
  后台线程执行，UI 线程不阻塞。
- 无 `nvidia-smi` 的环境中 GPU 卡片显示 N/A，其余功能不受影响。
- Benchmark 的 tok/s 取自 llama-server `/completion` 响应中的 `timings`
  字段（比解析日志更可靠）；Dashboard 的 tok/s 实时解析服务日志中的
  `prompt eval time` / `eval time` 行。
