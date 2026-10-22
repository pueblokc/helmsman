"""The UI is embedded in helmsman.HTML; make sure its script at least parses."""
import re
import shutil
import subprocess

import pytest

import helmsman


def _script():
    m = re.search(r"<script>(.*)</script>", helmsman.HTML, re.S)
    assert m
    return m.group(1)


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_embedded_js_parses(tmp_path):
    f = tmp_path / "ui.js"
    f.write_text(_script(), encoding="utf-8")
    r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_ui_uses_home_slot_minus_one():
    assert "goPreset(-1)" in _script() and "goPreset(0)" not in _script()


def test_ui_no_innerhtml_injection():
    # Camera / preset names come from the NVR; they must only ever be set via textContent.
    assigned = re.findall(r"innerHTML\s*=\s*([^;]+);", _script())
    assert assigned and set(assigned) == {"''"}
