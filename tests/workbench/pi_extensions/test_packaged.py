"""The compiled Pi extensions ship as package data in the wheel."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
EXTENSION_DIRECTORY = REPO_ROOT / "src" / "pitwall" / "workbench" / "pi_extensions"
WHEEL_PREFIX = "pitwall/workbench/pi_extensions/"


def test_js_files_in_wheel(tmp_path: Path) -> None:
    sources = sorted(path.stem for path in EXTENSION_DIRECTORY.glob("*.ts"))
    assert {
        "extension",
        "native-extension",
        "provider-extension",
        "restricted-extension",
        "comparison-lifecycle-observer",
    } <= set(sources)

    completed = subprocess.run(  # noqa: S603  # reason: fixed argv, the interpreter running this test, no shell
        [
            sys.executable,
            "-m",
            "hatchling",
            "build",
            "--target",
            "wheel",
            "--directory",
            str(tmp_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert completed.returncode == 0, (
        f"wheel build failed:\n{completed.stdout[-2000:]}\n{completed.stderr[-2000:]}"
    )
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1, wheels
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())

    missing = [name for name in sources if f"{WHEEL_PREFIX}{name}.js" not in names]
    assert not missing, f"compiled extensions missing from the wheel: {missing}"
    # The nearest package.json marks the directory as ES modules for Node.
    assert f"{WHEEL_PREFIX}package.json" in names
    assert f"{WHEEL_PREFIX}COMPILER" in names
