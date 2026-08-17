"""Parameters 页面：llama-server 参数表单 + 实时命令预览。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
    QVBoxLayout, QWidget,
)

from core.params import KV_CACHE_TYPES, ServerParams
from ui.theme import MONO_STACK
from ui.widgets import DragDropLineEdit


class ParametersPage(QWidget):
    """所有控件变更都会发出 params_changed，并实时刷新命令预览。"""

    params_changed = Signal(object)  # ServerParams

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._loading = False
        # 防抖：控件连续变化时延迟发出保存信号（须在 _build_ui 之前创建）
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(400)
        self._debounce.timeout.connect(self._emit_params)
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(14)

        title = QLabel("Parameters")
        title.setObjectName("pageTitle")
        outer.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        form = QVBoxLayout(body)
        form.setContentsMargins(0, 0, 8, 0)
        form.setSpacing(12)

        # ---- 路径卡片 ----
        path_card = QFrame()
        path_card.setObjectName("card")
        pc = QVBoxLayout(path_card)
        pc.setContentsMargins(16, 14, 16, 14)
        pc.setSpacing(10)

        self.exe_edit = QLineEdit()
        self.exe_edit.setPlaceholderText("llama-server.exe 路径")
        btn_exe = QPushButton("浏览…")
        btn_exe.clicked.connect(self._browse_exe)
        pc.addWidget(QLabel("llama-server 可执行文件"))
        row = QHBoxLayout()
        row.addWidget(self.exe_edit, 1)
        row.addWidget(btn_exe)
        pc.addLayout(row)

        self.model_edit = DragDropLineEdit(".gguf")
        self.model_edit.setPlaceholderText("选择或拖放 .gguf 模型文件")
        btn_model = QPushButton("浏览…")
        btn_model.clicked.connect(self._browse_model)
        pc.addWidget(QLabel("GGUF 模型文件（支持拖放）"))
        row2 = QHBoxLayout()
        row2.addWidget(self.model_edit, 1)
        row2.addWidget(btn_model)
        pc.addLayout(row2)
        form.addWidget(path_card)

        # ---- 推理参数卡片 ----
        tune = QFrame()
        tune.setObjectName("card")
        tc = QVBoxLayout(tune)
        tc.setContentsMargins(16, 14, 16, 14)
        tc.setSpacing(10)

        self.spin_ctx = self._spin(512, 1048576, 8192, 1024)
        self.spin_threads = self._spin(1, 64, 16, 1)
        self.spin_batch = self._spin(16, 32768, 1024, 16)
        tc.addLayout(self._row("Context Size (-c)", self.spin_ctx))
        tc.addLayout(self._row("Threads (-t)", self.spin_threads))
        tc.addLayout(self._row("Batch Size (-b)", self.spin_batch))

        ngl_row = QHBoxLayout()
        ngl_lab = QLabel("GPU Layers (-ngl)")
        ngl_lab.setFixedWidth(180)
        ngl_lab.setStyleSheet("color: #98989D;")
        self.chk_ngl_auto = QCheckBox("Auto")
        self.chk_ngl_auto.setChecked(True)
        self.spin_ngl = self._spin(0, 200, 99, 1)
        self.spin_ngl.setEnabled(False)
        self.chk_ngl_auto.toggled.connect(
            lambda on: self.spin_ngl.setEnabled(not on))
        ngl_row.addWidget(ngl_lab)
        ngl_row.addWidget(self.chk_ngl_auto)
        ngl_row.addWidget(self.spin_ngl, 1)
        tc.addLayout(ngl_row)

        self.chk_fa = QCheckBox("启用 Flash Attention (-fa on)")
        self.chk_fa.setChecked(True)
        tc.addWidget(self.chk_fa)

        self.combo_k = QComboBox()
        self.combo_k.addItems(KV_CACHE_TYPES)
        self.combo_v = QComboBox()
        self.combo_v.addItems(KV_CACHE_TYPES)
        tc.addLayout(self._row("KV Cache K 类型", self.combo_k))
        tc.addLayout(self._row("KV Cache V 类型", self.combo_v))

        moe_row = QHBoxLayout()
        moe_lab = QLabel("CPU MoE")
        moe_lab.setFixedWidth(180)
        moe_lab.setStyleSheet("color: #98989D;")
        self.chk_cpu_moe = QCheckBox("--cpu-moe（全部 MoE 在 CPU）")
        self.spin_ncmoe = self._spin(0, 512, 0, 1)
        self.spin_ncmoe.setPrefix("ncmoe ")
        self.chk_cpu_moe.toggled.connect(
            lambda on: self.spin_ncmoe.setEnabled(not on))
        moe_row.addWidget(moe_lab)
        moe_row.addWidget(self.chk_cpu_moe)
        moe_row.addWidget(self.spin_ncmoe, 1)
        tc.addLayout(moe_row)
        form.addWidget(tune)

        # ---- 服务参数卡片 ----
        srv = QFrame()
        srv.setObjectName("card")
        sc = QVBoxLayout(srv)
        sc.setContentsMargins(16, 14, 16, 14)
        sc.setSpacing(10)
        self.edit_host = QLineEdit("127.0.0.1")
        self.spin_port = self._spin(1, 65535, 8080, 1)
        self.edit_alias = QLineEdit()
        self.edit_alias.setPlaceholderText("可选，模型别名")
        self.edit_extra = QLineEdit()
        self.edit_extra.setPlaceholderText('例如: --ubatch-size 512 --no-warmup')
        sc.addLayout(self._row("Host", self.edit_host))
        sc.addLayout(self._row("Port", self.spin_port))
        sc.addLayout(self._row("Alias", self.edit_alias))
        sc.addLayout(self._row("额外参数", self.edit_extra))
        form.addWidget(srv)

        # ---- 命令预览 ----
        cmd_card = QFrame()
        cmd_card.setObjectName("card")
        cc = QVBoxLayout(cmd_card)
        cc.setContentsMargins(16, 14, 16, 14)
        cc.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(QLabel("最终命令"))
        head.addStretch(1)
        btn_copy_cmd = QPushButton("复制命令")
        btn_copy_cmd.clicked.connect(self._copy_command)
        head.addWidget(btn_copy_cmd)
        cc.addLayout(head)
        self.cmd_preview = QPlainTextEdit()
        self.cmd_preview.setReadOnly(True)
        self.cmd_preview.setStyleSheet(f"font-family: {MONO_STACK}; font-size: 12px;")
        self.cmd_preview.setFixedHeight(110)
        cc.addWidget(self.cmd_preview)
        form.addWidget(cmd_card)
        form.addStretch(1)

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # 所有控件变化 → 刷新预览（防抖发信号）
        for w in (self.exe_edit, self.model_edit, self.edit_host,
                  self.edit_alias, self.edit_extra):
            w.textChanged.connect(self._on_change)
        for w in (self.spin_ctx, self.spin_threads, self.spin_batch,
                  self.spin_ngl, self.spin_ncmoe, self.spin_port):
            w.valueChanged.connect(self._on_change)
        for w in (self.chk_ngl_auto, self.chk_fa, self.chk_cpu_moe):
            w.toggled.connect(self._on_change)
        for w in (self.combo_k, self.combo_v):
            w.currentTextChanged.connect(self._on_change)
        self._on_change()

    # ------------------------------------------------------------------
    @staticmethod
    def _spin(lo: int, hi: int, val: int, step: int) -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(val)
        s.setSingleStep(step)
        return s

    @staticmethod
    def _row(label: str, widget) -> QHBoxLayout:
        row = QHBoxLayout()
        lab = QLabel(label)
        lab.setFixedWidth(180)
        lab.setStyleSheet("color: #98989D;")
        row.addWidget(lab)
        row.addWidget(widget, 1)
        return row

    # ------------------------------------------------------------------
    def get_params(self) -> ServerParams:
        return ServerParams(
            llama_server_path=self.exe_edit.text().strip(),
            model_path=self.model_edit.text().strip(),
            context_size=self.spin_ctx.value(),
            threads=self.spin_threads.value(),
            batch_size=self.spin_batch.value(),
            gpu_layers_auto=self.chk_ngl_auto.isChecked(),
            gpu_layers=self.spin_ngl.value(),
            flash_attention=self.chk_fa.isChecked(),
            cache_type_k=self.combo_k.currentText(),
            cache_type_v=self.combo_v.currentText(),
            cpu_moe=self.chk_cpu_moe.isChecked(),
            n_cpu_moe=self.spin_ncmoe.value(),
            host=self.edit_host.text().strip() or "127.0.0.1",
            port=self.spin_port.value(),
            alias=self.edit_alias.text().strip(),
            extra_args=self.edit_extra.text(),
        )

    def set_params(self, p: ServerParams) -> None:
        self._loading = True
        try:
            self.exe_edit.setText(p.llama_server_path)
            self.model_edit.setText(p.model_path)
            self.spin_ctx.setValue(p.context_size)
            self.spin_threads.setValue(p.threads)
            self.spin_batch.setValue(p.batch_size)
            self.chk_ngl_auto.setChecked(p.gpu_layers_auto)
            self.spin_ngl.setValue(p.gpu_layers)
            self.spin_ngl.setEnabled(not p.gpu_layers_auto)
            self.chk_fa.setChecked(p.flash_attention)
            self.combo_k.setCurrentText(p.cache_type_k)
            self.combo_v.setCurrentText(p.cache_type_v)
            self.chk_cpu_moe.setChecked(p.cpu_moe)
            self.spin_ncmoe.setValue(p.n_cpu_moe)
            self.spin_ncmoe.setEnabled(not p.cpu_moe)
            self.edit_host.setText(p.host)
            self.spin_port.setValue(p.port)
            self.edit_alias.setText(p.alias)
            self.edit_extra.setText(p.extra_args)
        finally:
            self._loading = False
        self._on_change()

    # ------------------------------------------------------------------
    def _on_change(self, *_) -> None:
        self._refresh_preview()
        if not self._loading:
            self._debounce.start()

    def _refresh_preview(self) -> None:
        p = self.get_params()
        if p.llama_server_path and p.model_path:
            self.cmd_preview.setPlainText(p.command_display())
        else:
            self.cmd_preview.setPlainText(
                "# 请先选择 llama-server 可执行文件和 GGUF 模型")

    def _emit_params(self) -> None:
        self.params_changed.emit(self.get_params())

    def _browse_exe(self) -> None:
        filt = "llama-server (llama-server.exe);;可执行文件 (*.exe);;所有文件 (*)"
        path, _ = QFileDialog.getOpenFileName(self, "选择 llama-server", "", filt)
        if path:
            self.exe_edit.setText(str(Path(path)))

    def _browse_model(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 GGUF 模型", "", "GGUF 模型 (*.gguf);;所有文件 (*)")
        if path:
            self.model_edit.setText(str(Path(path)))

    def _copy_command(self) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.cmd_preview.toPlainText())
