# LlamaHelper

Windows 11 上的 **llama.cpp GUI 调参与性能测试工具**：llama-server.exe 的图形化启动器、
参数配置器和自动化 Benchmark 工具，面向 GGUF / Qwen MoE 模型与 NVIDIA CUDA GPU。

深色 Apple 风格桌面界面（PySide6），可打包为单个 exe。

## 功能

- **Dashboard**：GPU 利用率 / 显存 / 温度 / 功耗 / 频率，CPU / RAM，llama-server 进程
  CPU / RAM，Generation 与 Prompt tok/s 大数字实时展示；启动 / 停止 / 重启、
  打开 WebUI、复制 API URL、PID 显示、防重复启动、端口占用检测。
- **Parameters**：llama-server / GGUF 模型 / mmproj 投影文件（`-mm`，可选，
  多模态模型用）路径选择，GGUF 支持拖放；Context Size、Threads、
  Threads Batch（-tb）、Batch Size、ubatch-size（-ub）、
  GPU Layers（Auto 使用 llama.cpp 原生 `-ngl auto`）、Flash Attention 开关、
  KV Cache K/V 类型、`--cpu-moe` 与 `ncmoe`、MTP 投机解码
  （`--spec-type draft-mtp` + `--spec-draft-n-max`）、Host / Port / Alias、
  额外自定义参数；高级模式（折叠面板）提供 parallel、
  continuous batching、no-mmap、mlock、no-warmup、cache-reuse、
  RoPE scaling / freq-scale、temp / top-k / top-p / min-p 默认采样参数；
  所有数值与下拉控件需点击聚焦后滚轮才生效，避免滚动页面时误改；
  下方实时显示最终生成的完整命令；路径与参数自动记忆
  （程序目录 `llama_helper_config.json`）。
- **Benchmark**：自定义扫描参数（n-cpu-moe / GPU Layers / Batch / ubatch /
  Threads / Threads Batch / Context / MTP draft n-max）+ 起始 / 结束 / 步进；扫描 ncmoe
  时可选 `--cpu-moe` 基准轮。每轮自动：启动服务 → 等待 API 就绪 →
  **预热请求（不计入结果）** → 发送固定 prompt 生成指定 token → 记录
  generation/prompt tok/s、显存峰值、功耗峰值、RAM 峰值、OOM / 启动失败 →
  关闭服务。结果表格按 generation tok/s 排名并标记最快配置，可导出 CSV。
  另支持 **ASR 语音识别模型模式**（如 Qwen3-ASR-1.7B）：不以生成速度排名，
  改用 **RTF（Real-Time Factor）= 音频时长 ÷ 识别耗时**（越大越好）；
  参考音频（wav / flac / mp3）由用户放入指定目录（默认程序目录 `benchmark_audio/`），
  音频经 `/v1/chat/completions` 的 `input_audio`（base64）发送，逐文件识别并
  计时，先用首个文件预热一轮，表格按平均 RTF 排名。
- **Logs**：QProcess 实时输出 stdout/stderr，按级别着色（错误红 / 警告黄 /
  性能绿 / 加载蓝），自动滚动可暂停，可保存。
- **Presets**：多组 JSON 预设（默认含 Qwen35B Balanced / Max Speed /
  Long Context / Low VRAM），保存于程序目录 `presets.json`。
- **翻译挂件**（侧边栏「翻译挂件」启动）：无边框置顶的 Apple 风格桌面圆形，
  连接当前配置的本地 llama-server。右键直接粘贴剪贴板文本或图片并处理；
  黑色圆形外沿保留独立的发丝白边，内部白圈在悬停时扩张、处理时收缩并呼吸。
  结果按当前显示器左右可用空间
  向较宽一侧展开：短内容显示为双半圆胶囊，长内容向下生长为圆角矩形。
  左键拖到屏幕左右边缘可吸附为带白色竖纹的窄条，悬停时竖纹延长，左键点击
  解除吸附并恢复圆形。请求走后台线程，停靠边、显示器、沿边位置和提示词保存于
  `widget_config.json`。

## 目标环境

- Windows 11 + Python 3.12
- llama.cpp CUDA 构建：`llama-b10453-bin-win-cuda-13.3-x64`（运行时需配套
  `cudart-llama-bin-win-cuda-13.3-x64`，把两者解压到同一目录后选择其中的
  `llama-server.exe`）。GPU Layers 的 Auto 依赖较新版本 llama.cpp 的
  `-ngl auto`；如旧构建不支持，取消 Auto 勾选后填固定层数即可
- 默认参数按 5070 Ti Laptop 12GB + 9955HX + 64GB DDR5 调优（threads=16）

## 开发运行

双击 `start.bat` 可一键创建项目虚拟环境、安装缺失依赖并启动程序。

也可以手动运行：

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
benchmark/             自定义参数扫描 Benchmark 执行器、ASR 音频时长解析（wav/flac/mp3）
config/                配置持久化与预设管理
widget/                翻译挂件（API 客户端 / 挂件窗体 / 挂件配置）
tools/                 图标生成脚本
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
