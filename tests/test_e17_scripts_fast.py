"""E17 koşu betiklerinin her zaman koşan hızlı kontrolü.

Ağır uçtan-uca testler `tests/test_e17_scripts.py`'dedir ve yalnız
`E17_SCRIPTS_TESTS=1` ile koşar; bu dosya varsayılan pakette betiklerin en
azından sözdizimsel olarak sağlam ve Python yardımcılarının derlenebilir
olduğunu doğrular.
"""

from __future__ import annotations

import py_compile
import shutil
import subprocess
from pathlib import Path

import pytest

E17 = Path(__file__).resolve().parent.parent / "scripts" / "e17"
SHELL_SCRIPTS = sorted(E17.glob("*.sh"))
PY_SCRIPTS = sorted(E17.glob("*.py"))


def test_betik_kumesi_beklenen():
    adlar = {p.name for p in SHELL_SCRIPTS}
    assert {"lib17.sh", "run_smoke.sh", "run_dev.sh", "run_val.sh", "run_final.sh"} <= adlar
    assert {"analyze_e17.py", "envtool.py", "verify_variants.py"} <= {p.name for p in PY_SCRIPTS}


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash yok")
@pytest.mark.parametrize("path", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_bash_sozdizimi(path):
    r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("path", PY_SCRIPTS, ids=lambda p: p.name)
def test_python_derlenir(path, tmp_path):
    py_compile.compile(str(path), cfile=str(tmp_path / "x.pyc"), doraise=True)
