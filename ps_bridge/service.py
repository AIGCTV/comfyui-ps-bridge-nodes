"""Compose bridge services lazily after ComfyUI is initialized."""
from __future__ import annotations
import os
from pathlib import Path
from .storage import DefinitionStore
from .asset_registry import AssetRegistry
from .requests import RunStore
from .test_mode import TestTargetRegistry
from .test_mode_state import TestModeCoordinator
from .parameter_sync import SessionRegistry
from .run_preparation import RunPreparation
from .backend_runner import BackendRunner, ComfyAdapter
from .work import BlockingWork
from .pipeline import Pipeline
from .result_assets import ResultAssets
from .editor_execution import EditorExecution

class BridgeService:
    def __init__(self, root, input_dir, *, installed=None, adapter=None):
        self.root = Path(root)
        self.definitions = DefinitionStore(self.root / "definitions", installed=installed)
        self.assets = AssetRegistry(self.root / "assets", input_dir)
        self.result_assets = ResultAssets()
        self.runs = RunStore(self.root / "runs")
        self.targets = TestTargetRegistry(installed=installed)
        self.sessions = SessionRegistry(self.definitions, self.targets)
        self.test_mode = TestModeCoordinator(self.sessions, self.targets)
        self.preparation = RunPreparation(self.sessions, self.assets, self.runs)
        self.runner = BackendRunner(self.runs, adapter) if adapter is not None else None
        self.work = BlockingWork()
        self.execution = EditorExecution(self)
        self.pipeline = Pipeline(self)
        if self.runner:
            self.runner.service = self

_service = None

def get_service():
    global _service
    if _service is None:
        import folder_paths
        from server import PromptServer
        from .paths import DATA_DIR
        root = Path(os.getenv("PS_BRIDGE_DATA_DIR", str(DATA_DIR / "bridge-v3")))
        _service = BridgeService(root, folder_paths.get_input_directory(), adapter=ComfyAdapter(PromptServer.instance))
    return _service
