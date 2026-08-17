"""Benchmark 页面：ncmoe 区间自动测试 + 结果排名表。"""
from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QPlainTextEdit, QProgressBar, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from benchmark.runner import BenchJob, DEFAULT_TEST_PROMPT
from core.params import ServerParams
from ui.theme import GRAY, GREEN, MONO_STACK, RED


class BenchmarkPage(QWidget):
    run_requested = Signal(object)   # BenchJob
    stop_requested = Signal()

    COLUMNS = ["排名", "配置", "Gen tok/s", "Prompt tok/s",
               "VRAM 峰值 MB", "功耗峰值 W", "RAM 峰值 MB", "状态"]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._results: list[dict] = []
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

        row = QHBoxLayout()
        self.spin_start = self._spin(0, 512, 48)
        self.spin_end = self._spin(0, 512, 16)
        self.spin_step = self._spin(1, 128, 8)
        self.spin_tokens = self._spin(16, 4096, 200)
        for label, w in (("ncmoe 起始", self.spin_start),
                         ("ncmoe 结束", self.spin_end),
                         ("步进", self.spin_step),
                         ("生成 token 数", self.spin_tokens)):
            lab = QLabel(label)
            lab.setStyleSheet(f"color: {GRAY};")
            row.addWidget(lab)
            row.addWidget(w)
        row.addStretch(1)
        cl.addLayout(row)

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

        prompt_lab = QLabel("测试 Prompt（固定发送，用于公平对比）")
        prompt_lab.setStyleSheet(f"color: {GRAY};")
        cl.addWidget(prompt_lab)
        self.prompt_edit = QPlainTextEdit(DEFAULT_TEST_PROMPT)
        self.prompt_edit.setFixedHeight(80)
        self.prompt_edit.setStyleSheet(f"font-family: {MONO_STACK}; font-size: 12px;")
        cl.addWidget(self.prompt_edit)
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
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        root.addWidget(self.table, 1)

    @staticmethod
    def _spin(lo: int, hi: int, val: int) -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(val)
        return s

    # ------------------------------------------------------------------
    def _on_run(self) -> None:
        self._results.clear()
        self.table.setRowCount(0)
        job = BenchJob(
            base_params=ServerParams(),  # 由 MainWindow 填充
            ncmoe_start=self.spin_start.value(),
            ncmoe_end=self.spin_end.value(),
            ncmoe_step=self.spin_step.value(),
            gen_tokens=self.spin_tokens.value(),
            include_cpu_moe=self.chk_cpu_moe.isChecked(),
            prompt=self.prompt_edit.toPlainText().strip() or DEFAULT_TEST_PROMPT,
        )
        self.run_requested.emit(job)

    def set_running(self, running: bool) -> None:
        self.btn_run.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        for w in (self.spin_start, self.spin_end, self.spin_step,
                  self.spin_tokens, self.chk_cpu_moe, self.prompt_edit):
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
            best = max(ok, key=lambda r: r["gen_tps"])
            self.progress_label.setText(
                f"完成 ✓ 最快: {best['label']} @ {best['gen_tps']:.2f} tok/s")
        else:
            self.progress_label.setText("完成（无成功轮次）")
        self.progress.setValue(self.progress.maximum())

    # ------------------------------------------------------------------
    def _refresh_table(self) -> None:
        ok = sorted([r for r in self._results if r["ok"]],
                    key=lambda r: r["gen_tps"], reverse=True)
        bad = [r for r in self._results if not r["ok"]]
        rows = ok + bad
        best_gen = ok[0]["gen_tps"] if ok else 0.0
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            rank = ""
            if r["ok"]:
                rank = f"#{ok.index(r) + 1}"
                if r["gen_tps"] == best_gen:
                    rank = "★ 最快"
            values = [rank, r["label"],
                      f"{r['gen_tps']:.2f}" if r["ok"] else "--",
                      f"{r['prompt_tps']:.2f}" if r["ok"] else "--",
                      f"{r['vram_peak_mb']:.0f}" if r["vram_peak_mb"] else "--",
                      f"{r['power_peak_w']:.0f}" if r["power_peak_w"] else "--",
                      f"{r['ram_peak_mb']:.0f}" if r["ram_peak_mb"] else "--",
                      r["status"]]
            for j, v in enumerate(values):
                item = QTableWidgetItem(str(v))
                if r["ok"] and r["gen_tps"] == best_gen:
                    item.setForeground(QColor(GREEN))
                elif not r["ok"]:
                    item.setForeground(QColor(RED if r["status"] == "OOM" else GRAY))
                    if r["status"] == "OOM":
                        item.setToolTip(r.get("error", ""))
                self.table.setItem(i, j, item)

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
            writer.writerow(["label", "ncmoe", "cpu_moe", "gen_tps",
                             "prompt_tps", "vram_peak_mb", "power_peak_w",
                             "ram_peak_mb", "status", "error"])
            for r in self._results:
                writer.writerow([r["label"], r["ncmoe"], r["cpu_moe"],
                                 f"{r['gen_tps']:.2f}", f"{r['prompt_tps']:.2f}",
                                 f"{r['vram_peak_mb']:.0f}",
                                 f"{r['power_peak_w']:.0f}",
                                 f"{r['ram_peak_mb']:.0f}",
                                 r["status"], r["error"]])
