import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def isolated_widget_settings(tmp_path, monkeypatch):
    """Keep GUI preset migrations away from the user's widget configuration."""
    monkeypatch.setattr("widget.settings.app_dir", lambda: tmp_path)
