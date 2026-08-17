"""深色 Apple 风格主题（QSS）。"""

ACCENT = "#0A84FF"
GREEN = "#30D158"
RED = "#FF453A"
YELLOW = "#FFD60A"
GRAY = "#98989D"
BG = "#1C1C1E"
CARD = "#2C2C2E"
CARD_BORDER = "#3A3A3C"

FONT_STACK = '"SF Pro Text", "SF Pro Display", "Segoe UI", "Microsoft YaHei UI", sans-serif'
MONO_STACK = '"SF Mono", "Cascadia Code", "Consolas", "JetBrains Mono", monospace'

APP_QSS = f"""
* {{
    font-family: {FONT_STACK};
    font-size: 13px;
    color: #F2F2F7;
    outline: none;
}}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#root {{ background: {BG}; }}

/* ---------------- 侧边栏 ---------------- */
QFrame#sidebar {{
    background: #232325;
    border-right: 1px solid #38383A;
}}
QLabel#appTitle {{
    font-size: 17px; font-weight: 700;
    padding: 18px 16px 6px 16px;
}}
QLabel#appSubtitle {{
    color: {GRAY}; font-size: 11px;
    padding: 0 16px 14px 16px;
}}
QPushButton#navBtn {{
    text-align: left;
    padding: 9px 14px;
    margin: 1px 10px;
    border: none;
    border-radius: 8px;
    color: #D1D1D6;
    font-size: 13px;
}}
QPushButton#navBtn:hover {{ background: #3A3A3C; }}
QPushButton#navBtn:checked {{
    background: {ACCENT};
    color: white;
    font-weight: 600;
}}

/* ---------------- 卡片 ---------------- */
QFrame#card {{
    background: {CARD};
    border: 1px solid {CARD_BORDER};
    border-radius: 12px;
}}
QLabel#cardTitle {{
    color: {GRAY};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.4px;
}}
QLabel#statValue {{
    font-size: 24px;
    font-weight: 700;
}}
QLabel#statSub {{ color: {GRAY}; font-size: 11px; }}
QLabel#bigTps {{
    font-size: 44px;
    font-weight: 800;
    color: {GREEN};
}}
QLabel#pageTitle {{
    font-size: 20px; font-weight: 700;
    padding-bottom: 4px;
}}

/* ---------------- 控件 ---------------- */
QPushButton {{
    background: #3A3A3C;
    border: none;
    border-radius: 8px;
    padding: 7px 14px;
    color: #F2F2F7;
}}
QPushButton:hover {{ background: #48484A; }}
QPushButton:pressed {{ background: #545456; }}
QPushButton:disabled {{ background: #2C2C2E; color: #636366; }}
QPushButton#primaryBtn {{
    background: {ACCENT};
    color: white;
    font-weight: 600;
}}
QPushButton#primaryBtn:hover {{ background: #2b95ff; }}
QPushButton#primaryBtn:disabled {{ background: #26456b; color: #8a8a8e; }}
QPushButton#dangerBtn {{ background: {RED}; color: white; font-weight: 600; }}
QPushButton#dangerBtn:hover {{ background: #ff5c52; }}
QPushButton#dangerBtn:disabled {{ background: #5b3230; color: #8a8a8e; }}

QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: #1F1F21;
    border: 1px solid #3A3A3C;
    border-radius: 8px;
    padding: 6px 8px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{
    color: #636366;
    background: #232325;
}}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: #2C2C2E;
    border: 1px solid #3A3A3C;
    selection-background-color: {ACCENT};
}}
QSpinBox::up-button, QSpinBox::down-button {{ width: 18px; border: none; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 18px; height: 18px;
    border-radius: 5px;
    border: 1px solid #4A4A4C;
    background: #1F1F21;
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
}}

/* ---------------- 表格 ---------------- */
QTableWidget {{
    background: {CARD};
    border: 1px solid {CARD_BORDER};
    border-radius: 12px;
    gridline-color: #3A3A3C;
}}
QTableWidget::item {{ padding: 6px; }}
QTableWidget::item:selected {{ background: #0A84FF44; }}
QHeaderView::section {{
    background: #232325;
    color: {GRAY};
    padding: 8px;
    border: none;
    border-bottom: 1px solid #3A3A3C;
    font-weight: 600;
}}

/* ---------------- 其他 ---------------- */
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #4A4A4C; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: #5A5A5C; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: transparent; height: 10px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: #4A4A4C; border-radius: 5px; min-width: 30px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QToolTip {{
    background: #2C2C2E; color: #F2F2F7;
    border: 1px solid #3A3A3C; padding: 6px;
}}
QProgressBar {{
    background: #1F1F21;
    border-radius: 5px;
    height: 10px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QListWidget {{
    background: {CARD};
    border: 1px solid {CARD_BORDER};
    border-radius: 12px;
    padding: 4px;
}}
QListWidget::item {{ padding: 8px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {ACCENT}; color: white; }}
QListWidget::item:hover:!selected {{ background: #3A3A3C; }}
"""


def log_line_color(line: str) -> str:
    """按日志内容返回高亮颜色。"""
    low = line.lower()
    if any(k in low for k in ("error", "failed", "oom", "out of memory",
                              "crash", "abort", "fatal", "[!]")):
        return RED
    if any(k in low for k in ("warn", "warning", "deprecated")):
        return YELLOW
    if "tokens per second" in low or "tok/s" in low or "per token" in low:
        return GREEN
    if any(k in low for k in ("load time", "model loaded", "llama_model_loader",
                              "server is listening", "starting the main loop")):
        return ACCENT
    if line.startswith("$") or line.startswith("[*]") or line.startswith("[bench]"):
        return "#BF5AF2"
    return "#D1D1D6"
