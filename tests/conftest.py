import gc
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="session", autouse=True)
def qt_application_lifetime():
    """Keep one application alive, and destroy Qt objects before Python exits.

    Module qapp fixtures borrow this instance. Offscreen windows can remain in
    Python/Qt signal cycles after close(); leaving them to interpreter shutdown
    causes a native crash with PySide6 6.12 on Windows.
    """
    from PySide6.QtCore import QCoreApplication, QEvent, QProcess, QThread
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete, isValid

    app = QApplication.instance() or QApplication([])
    yield app
    windows = list(app.topLevelWidgets())
    for window in windows:
        if isValid(window):
            window.close()
    deadline = time.monotonic() + 10
    while True:
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        busy = any(
            (isinstance(child, QThread) and (child.isRunning() or not child.wait(0)))
            or (isinstance(child, QProcess)
                and child.state() != QProcess.ProcessState.NotRunning)
            for window in windows if isValid(window)
            for child in window.findChildren(QThread) + window.findChildren(QProcess)
        )
        if not busy or time.monotonic() >= deadline:
            break
        time.sleep(0.01)
    assert not busy, "Qt test session still owns a running process or thread"
    for window in app.topLevelWidgets():
        if isValid(window):
            window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()
    delete(app)


@pytest.fixture
def fake_exe(tmp_path, monkeypatch) -> Path:
    """Launch the stdlib-only fake with an interpreter and an argument list.

    Use the base interpreter to avoid the Windows venv redirector creating an
    extra child outside QProcess/Popen ownership. Keep production model/CLI
    arguments intact, and exercise a script path containing spaces everywhere.
    """
    from core.params import ServerParams

    script_dir = tmp_path / "fake server with spaces"
    script_dir.mkdir()
    script = script_dir / "fake_llama_server.py"
    shutil.copyfile(Path(__file__).with_name("fake_llama_server.py"), script)
    build_command = ServerParams.build_command

    def fake_command(params):
        command = build_command(params)
        return [command[0], "-u", str(script), *command[1:]]

    monkeypatch.setattr(ServerParams, "build_command", fake_command)
    return Path(getattr(sys, "_base_executable", sys.executable))


@pytest.fixture(autouse=True)
def isolated_widget_settings(tmp_path, monkeypatch):
    """Keep GUI preset migrations away from the user's widget configuration."""
    monkeypatch.setattr("widget.settings.app_dir", lambda: tmp_path)
