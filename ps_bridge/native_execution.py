"""Bounded observer-to-journal pump. Live events precede durable checkpoints."""
import asyncio
import time
from collections import deque
from functools import partial

from .editor_execution import NATIVE_EVENTS
from .json_codec import canonical_bytes


class NativeExecutionPump:
    CHECKPOINT_SECONDS = .250

    def __init__(self, pipeline, run_id):
        self.pipeline, self.run_id = pipeline, run_id
        self.pending = deque()
        self.bytes = 0
        self.wake = asyncio.Event()
        self.space = asyncio.Event()
        self.space.set()
        self.closed = False
        self.last_progress = 0
        self.task = None

    async def feed(self, kind, data):
        if kind not in NATIVE_EVENTS | {"executed"}:
            return False
        if self.task and self.task.done():
            await self.task
        live = self.pipeline.service.execution
        if not live.accepts(self.run_id, data):
            # The first association must validate the actual frozen queue entry.
            # Later events use that immutable identity, without touching disk.
            await self.pipeline._native_work(self.pipeline.native_event, self.run_id, kind, data)
            snapshot = live.snapshot(self.run_id)
            return bool(snapshot and isinstance(data, dict) and snapshot["promptId"] == data.get("prompt_id"))
        live.native(self.run_id, kind, data)
        if kind in {"progress_state", "progress_text"}:
            return True
        size = len(canonical_bytes(data))
        if kind == "progress" and self.pending and self.pending[-1][0] == kind and self.pending[-1][1].get("node") == data.get("node"):
            self.bytes -= self.pending.pop()[2]
        while len(self.pending) >= 64 or self.bytes + size > 20 * 1024 * 1024:
            self.space.clear()
            await self.space.wait()
            if self.task and self.task.done():
                await self.task
        self.pending.append((kind, data, size))
        self.bytes += size
        self.wake.set()
        if self.task is None:
            self.task = asyncio.create_task(self._drain())
            self.task.add_done_callback(lambda _: self.space.set())
        return True

    async def _drain(self):
        while not self.closed or self.pending:
            if not self.pending:
                self.wake.clear()
                await self.wake.wait()
                continue
            kind = self.pending[0][0]
            delay = self.CHECKPOINT_SECONDS - (time.monotonic() - self.last_progress)
            if kind == "progress" and len(self.pending) == 1 and delay > 0 and not self.closed:
                self.wake.clear()
                try:
                    await asyncio.wait_for(self.wake.wait(), delay)
                except asyncio.TimeoutError:
                    pass
                continue
            kind, data, size = self.pending.popleft()
            self.bytes -= size
            self.space.set()
            await self.pipeline._native_work(partial(self.pipeline.native_event, publish_live=False), self.run_id, kind, data)
            if kind == "progress":
                self.last_progress = time.monotonic()

    async def close(self):
        self.closed = True
        self.wake.set()
        if self.task:
            await self.task
