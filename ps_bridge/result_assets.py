"""Bounded validation records for immutable, journal-bound result PNGs."""
import copy
import stat
import threading
from collections import OrderedDict
from pathlib import Path

from .asset_registry import validate_png
from .errors import BridgeError, require
from .protocol import BRIDGE_MAX_ASSET_BYTES


class ResultAssets:
    def __init__(self, capacity=128):
        self.capacity = capacity
        self.cache = OrderedDict()
        self.lock = threading.RLock()

    def _signature(self, path):
        try:
            value = path.stat()
        except OSError as exc:
            raise BridgeError("RESULT_NOT_FOUND", "Result asset is unavailable", status=404) from exc
        require(stat.S_ISREG(value.st_mode), "RESULT_NOT_FOUND", "Result asset is not a file", status=404)
        require(0 < value.st_size <= BRIDGE_MAX_ASSET_BYTES, "RESULT_INVALID", "Result exceeds PNG byte limit", status=413)
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns

    def _remember(self, path, result, signature):
        self.cache[path] = (signature, copy.deepcopy(result))
        self.cache.move_to_end(path)
        while len(self.cache) > self.capacity:
            self.cache.popitem(last=False)

    def register(self, path, result):
        """Called only after this process atomically writes its own encoded PNG.

        The encoder already knows the pixel dimensions and hashes the exact
        bytes it writes. Do not decode those same trusted bytes a second time.
        """
        path = Path(path).resolve()
        with self.lock:
            signature = self._signature(path)
            require(type(result.get("byteLength")) is int and signature[2] == result["byteLength"],
                    "RESULT_INVALID", "Persisted result length differs from its binding")
            previous = self.cache.get(path)
            require(previous is None or previous[1] == result, "RESULT_INVALID", "Frozen result binding changed")
            self._remember(path, result, signature)
        return path

    def verify(self, path, result):
        """Revalidate cold, evicted or changed files against the frozen journal."""
        path = Path(path).resolve()
        with self.lock:
            signature = self._signature(path)
            cached = self.cache.get(path)
            require(cached is None or cached[1] == result, "RESULT_INVALID", "Frozen result binding changed")
            if cached and cached[0] == signature:
                self.cache.move_to_end(path)
                return path
            require(type(result.get("byteLength")) is int and signature[2] == result["byteLength"],
                    "RESULT_INVALID", "Persisted result length differs from its binding")
            with path.open("rb") as stream:
                data = stream.read(BRIDGE_MAX_ASSET_BYTES + 1)
            actual = validate_png(data)
            require(all(type(result.get(key)) is type(actual[key]) and result[key] == actual[key]
                        for key in ("mime", "sha256", "byteLength", "width", "height")),
                    "RESULT_INVALID", "Result PNG differs from its frozen binding")
            require(self._signature(path) == signature, "RESULT_INVALID", "Result changed during validation")
            self._remember(path, result, signature)
        return path
