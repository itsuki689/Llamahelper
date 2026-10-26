import os
import shutil
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


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
