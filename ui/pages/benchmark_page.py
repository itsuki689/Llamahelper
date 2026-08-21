"""Benchmark 页面：参数区间自动测试 + 结果排名表。

两种模式：
- gen：生成模型，指标为 generation tok/s（越高越快）
- asr：语音识别模型（如 Qwen3-ASR），指标为 RTF = 音频时长 / 识别耗时
  （越高越快），参考音频由用户放入指定目录（wav/flac/mp3）
"""
from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit,
    QProgressBar, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from benchmark.runner import SWEEP_FIELDS, BenchJob, DEFAULT_TEST_PROMPT
from config.app_config import app_dir
from core.params import ServerParams
from ui.theme import GRAY, GREEN, MONO_STACK, RED
from ui.widgets import guard_wheel_children

GEN_COLUMNS = ["排名", "配置", "Gen tok/s", "Prompt tok/s",
               "VRAM 峰值 MB", "功耗峰值 W", "RAM 峰值 MB", "状态"]
ASR_COLUMNS = ["排名", "配置", "平均 RTF", "音频总时长 s", "识别总耗时 s",
               "VRAM 峰值 MB", "功耗峰值 W", "RAM 峰值 MB", "状态"]


class BenchmarkPage(QWidget):
    run_requested = Signal(object)   # BenchJob
    stop_requested = Signal()

    MODES = {"生成模型（tok/s）": "gen", "ASR 语音识别（RTF）": "asr"}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._results: list[dict] = []
        self._mode = "gen"
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        title = QLabel("Benchmark")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        # ---- 设置卡片 ----
        cfg = QFrame()
        cfg.setObjectName("card")
        cl = QVBoxLayout(cfg)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)

        # 拆成多行，避免把窗口最小宽度撑大
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(list(self.MODES.keys()))
        self.combo_sweep = QComboBox()
        self.combo_sweep.addItems(list(SWEEP_FIELDS.keys()))
        self.spin_start = self._spin(0, 1048576, 48)
        self.spin_end = self._spin(0, 1048576, 16)
        self.spin_step = self._spin(1, 4096, 8)
        row = QHBoxLayout()
        for label, w in (("模式", self.combo_mode),
                         ("扫描参数", self.combo_sweep),
                         ("起始", self.spin_start),
                         ("结束", self.spin_end),
                         ("步进", self.spin_step)):
            lab = QLabel(label)
            lab.setStyleSheet(f"color: {GRAY};")
            row.addWidget(lab)
            row.addWidget(w)
        row.addStretch(1)
        cl.addLayout(row)
        self.combo_sweep.currentTextChanged.connect(self._on_sweep_changed)
        self.combo_mode.currentTextChanged.connect(self._on_mode_changed)

        # gen 模式：token 设置
        self.tokens_box = QFrame()
        row_tokens = QHBoxLayout(self.tokens_box)
        row_tokens.setContentsMargins(0, 0, 0, 0)
        self.spin_tokens = self._spin(16, 4096, 200)
        self.spin_warmup = self._spin(0, 1024, 32)
        for label, w in (("生成 token 数", self.spin_tokens),
                         ("预热 token 数", self.spin_warmup)):
            lab = QLabel(label)
            lab.setStyleSheet(f"color: {GRAY};")
            row_tokens.addWidget(lab)
            row_tokens.addWidget(w)
        row_tokens.addStretch(1)
        cl.addWidget(self.tokens_box)

        # asr 模式：参考音频目录
        self.audio_box = QFrame()
        row_audio = QHBoxLayout(self.audio_box)
        row_audio.setContentsMargins(0, 0, 0, 0)
        audio_lab = QLabel("参考音频目录")
        audio_lab.setStyleSheet(f"color: {GRAY};")
        self.audio_edit = QLineEdit(str(app_dir() / "benchmark_audio"))
        self.audio_edit.setPlaceholderText("把 wav/flac/mp3 参考音频放入该目录")
        btn_audio = QPushButton("浏览…")
        btn_audio.clicked.connect(self._browse_audio_dir)
        row_audio.addWidget(audio_lab)
        row_audio.addWidget(self.audio_edit, 1)
        row_audio.addWidget(btn_audio)
        cl.addWidget(self.audio_box)
        audio_hint = QLabel("RTF = 音频时长 / 识别耗时（越大越快）；"
                            "每轮先预热一次再逐文件测量；支持 wav / flac / mp3")
        audio_hint.setStyleSheet(f"color: {GRAY}; font-size: 11px;")
        cl.addWidget(audio_hint)
        self._audio_hint = audio_hint

        row2 = QHBoxLayout()
        self.chk_cpu_moe = QCheckBox("包含 --cpu-moe（全部 MoE 在 CPU）基准轮")
        self.chk_cpu_moe.setChecked(True)
        self.btn_run = QPushButton("开始测试")
        self.btn_run.setObjectName("primaryBtn")
        self.btn_stop = QPushButton("中止")
        self.btn_stop.setObjectName("dangerBtn")
        self.btn_stop.setEnabled(False)
        self.btn_export = QPushButton("导出 CSV")
        self.btn_run.clicked.connect(self._on_run)
        self.btn_stop.clicked.connect(self.stop_requested)
        self.btn_export.clicked.connect(self._export_csv)
        row2.addWidget(self.chk_cpu_moe)
        row2.addStretch(1)
        row2.addWidget(self.btn_export)
        row2.addWidget(self.btn_stop)
        row2.addWidget(self.btn_run)
        cl.addLayout(row2)

        # gen 模式：固定 prompt
        self.prompt_box = QFrame()
        pv = QVBoxLayout(self.prompt_box)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(6)
        prompt_lab = QLabel("测试 Prompt（固定发送，用于公平对比）")
        prompt_lab.setStyleSheet(f"color: {GRAY};")
        pv.addWidget(prompt_lab)
        self.prompt_edit = QPlainTextEdit(DEFAULT_TEST_PROMPT)
        self.prompt_edit.setFixedHeight(80)
        self.prompt_edit.setStyleSheet(f"font-family: {MONO_STACK}; font-size: 12px;")
        pv.addWidget(self.prompt_edit)
        cl.addWidget(self.prompt_box)
        root.addWidget(cfg)

        # ---- 进度 ----
        prow = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setFixedHeight(10)
        self.progress.setTextVisible(False)
        self.progress_label = QLabel("空闲")
        self.progress_label.setStyleSheet(f"color: {GRAY};")
        prow.addWidget(self.progress, 1)
        prow.addWidget(self.progress_label)
        root.addLayout(prow)

        # ---- 结果表 ----
        self.table = QTableWidget(0, len(GEN_COLUMNS))
        self.table.setHorizontalHeaderLabels(GEN_COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        root.addWidget(self.table, 1)
        guard_wheel_children(self)  # 滚轮需先聚焦控件才生效
        self._on_mode_changed(self.combo_mode.currentText())

    @staticmethod
    def _spin(lo: int, hi: int, val: int) -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(val)
        return s

    # ------------------------------------------------------------------
    def _current_sweep_field(self) -> str:
        return SWEEP_FIELDS[self.combo_sweep.currentText()][0]

    def _current_mode(self) -> str:
        return self.MODES[self.combo_mode.currentText()]

    def _on_sweep_changed(self, _text: str) -> None:
        self.chk_cpu_moe.setVisible(self._current_sweep_field() == "n_cpu_moe")

    def _on_mode_changed(self, _text: str) -> None:
        asr = self._current_mode() == "asr"
        self.tokens_box.setVisible(not asr)
        self.prompt_box.setVisible(not asr)
        self.audio_box.setVisible(asr)
        self._audio_hint.setVisible(asr)

    def _on_run(self) -> None:
        self._mode = self._current_mode()
        self._results.clear()
        self._setup_table()
        job = BenchJob(
            base_params=ServerParams(),  # 由 MainWindow 填充
            mode=self._mode,
            sweep_field=self._current_sweep_field(),
            sweep_start=self.spin_start.value(),
            sweep_end=self.spin_end.value(),
            sweep_step=self.spin_step.value(),
            gen_tokens=self.spin_tokens.value(),
            warmup_tokens=self.spin_warmup.value(),
            include_cpu_moe=self.chk_cpu_moe.isChecked(),
            prompt=self.prompt_edit.toPlainText().strip() or DEFAULT_TEST_PROMPT,
            audio_dir=self.audio_edit.text().strip(),
        )
        self.run_requested.emit(job)

    def _setup_table(self) -> None:
        cols = ASR_COLUMNS if self._mode == "asr" else GEN_COLUMNS
        self.table.setRowCount(0)
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)

    @staticmethod
    def _metric_of(result: dict, mode: str) -> float:
        return result["rtf"] if mode == "asr" else result["gen_tps"]

    def set_running(self, running: bool) -> None:
        self.btn_run.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        for w in (self.combo_mode, self.combo_sweep, self.spin_start,
                  self.spin_end, self.spin_step, self.spin_tokens,
                  self.spin_warmup, self.chk_cpu_moe, self.prompt_edit,
                  self.audio_edit):
            w.setEnabled(not running)
        if running:
            self.progress_label.setText("准备中…")
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
        else:
            self.progress_label.setText("空闲")

    def on_run_started(self, info: dict) -> None:
        idx, total = info["index"], info["total"]
        self.progress.setRange(0, total)
        self.progress.setValue(idx)
        self.progress_label.setText(f"[{idx + 1}/{total}] 测试 {info['label']} …")

    def on_run_result(self, result: dict) -> None:
        self._results.append(result)
        self._refresh_table()

    def on_finished_all(self) -> None:
        self.set_running(False)
        ok = [r for r in self._results if r["ok"]]
        if ok:
            best = max(ok, key=lambda r: self._metric_of(r, self._mode))
            if self._mode == "asr":
                text = f"完成 ✓ 最快: {best['label']} @ RTF {best['rtf']:.2f}"
            else:
                text = f"完成 ✓ 最快: {best['label']} @ {best['gen_tps']:.2f} tok/s"
            self.progress_label.setText(text)
        else:
            self.progress_label.setText("完成（无成功轮次）")
        self.progress.setValue(self.progress.maximum())

    # ------------------------------------------------------------------
    def _row_values(self, r: dict) -> list[str]:
        tail = [f"{r['vram_peak_mb']:.0f}" if r["vram_peak_mb"] else "--",
                f"{r['power_peak_w']:.0f}" if r["power_peak_w"] else "--",
                f"{r['ram_peak_mb']:.0f}" if r["ram_peak_mb"] else "--",
                r["status"]]
        if self._mode == "asr":
            perf = [f"{r['rtf']:.2f}" if r["ok"] else "--",
                    f"{r['audio_s']:.1f}" if r["ok"] else "--",
                    f"{r['infer_s']:.1f}" if r["ok"] else "--"]
        else:
            perf = [f"{r['gen_tps']:.2f}" if r["ok"] else "--",
                    f"{r['prompt_tps']:.2f}" if r["ok"] else "--"]
        return ["", r["label"], *perf, *tail]  # 排名列在 _refresh_table 填

    def _refresh_table(self) -> None:
        ok = sorted([r for r in self._results if r["ok"]],
                    key=lambda r: self._metric_of(r, self._mode), reverse=True)
        bad = [r for r in self._results if not r["ok"]]
        rows = ok + bad
        best = self._metric_of(ok[0], self._mode) if ok else 0.0
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            values = self._row_values(r)
            if r["ok"]:
                values[0] = (f"#{ok.index(r) + 1}" if self._metric_of(r, self._mode) != best
                             else "★ 最快")
            for j, v in enumerate(values):
                item = QTableWidgetItem(str(v))
                if r["ok"] and self._metric_of(r, self._mode) == best:
                    item.setForeground(QColor(GREEN))
                elif not r["ok"]:
                    item.setForeground(QColor(RED if r["status"] == "OOM" else GRAY))
                    if r["status"] == "OOM":
                        item.setToolTip(r.get("error", ""))
                self.table.setItem(i, j, item)

    def _browse_audio_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择参考音频目录")
        if path:
            self.audio_edit.setText(str(Path(path)))

    def _export_csv(self) -> None:
        if not self._results:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 Benchmark 结果", "benchmark_results.csv",
            "CSV 文件 (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["label", "field", "value", "gen_tps",
                             "prompt_tps", "rtf", "audio_s", "infer_s",
                             "asr_files", "vram_peak_mb", "power_peak_w",
                             "ram_peak_mb", "status", "error"])
            for r in self._results:
                writer.writerow([r["label"], r["field"], r["value"],
                                 f"{r['gen_tps']:.2f}", f"{r['prompt_tps']:.2f}",
                                 f"{r.get('rtf', 0.0):.2f}",
                                 f"{r.get('audio_s', 0.0):.1f}",
                                 f"{r.get('infer_s', 0.0):.1f}",
                                 r.get("asr_files", 0),
                                 f"{r['vram_peak_mb']:.0f}",
                                 f"{r['power_peak_w']:.0f}",
                                 f"{r['ram_peak_mb']:.0f}",
                                 r["status"], r["error"]])
