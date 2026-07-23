from __future__ import annotations

from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
ROOT_DIR = PACKAGE_DIR.parent
DATA_DIR = ROOT_DIR / "data"
WORKFLOWS_DIR = DATA_DIR / "workflows"
PS_INPUTS_DIR = DATA_DIR / "ps_inputs"
PS_IMAGES_DIR = PS_INPUTS_DIR / "images"
RENDERS_DIR = DATA_DIR / "renders"
LATEST_STATE_PATH = PS_INPUTS_DIR / "latest.json"


def ensure_data_dirs() -> None:
    for path in (DATA_DIR, WORKFLOWS_DIR, PS_INPUTS_DIR, PS_IMAGES_DIR, RENDERS_DIR):
        path.mkdir(parents=True, exist_ok=True)
