"""Bounded cross-thread handoff; no service lock or asyncio Future in producers."""
import asyncio
import copy
from collections import deque
import logging
import threading

from .json_codec import canonical_bytes

logger = logging.getLogger(__name__)


class ManagerNotifications:
    def __init__(self, consume, recover):
        self.consume, self.recover = consume, recover
        self.lock = threading.Lock()
        self.pending = deque()
        self.bytes = 0
        self.resync = False
        self.loop = None
        self.task = None
        self.scheduled = False
        self.closed = False

    def post(self, kind, payload):
        payload = copy.deepcopy(payload)
        size = len(canonical_bytes(payload))
        with self.lock:
            if self.closed:
                return
            if len(self.pending) >= 64 or self.bytes + size > 4 * 1024 * 1024:
                # Authority is persisted in the services, never in this delivery queue.
                self.pending.clear()
                self.bytes = 0
                self.resync = True
            if size <= 4 * 1024 * 1024:
                self.pending.append((kind, payload, size))
                self.bytes += size
            if self.loop and not self.scheduled:
                self.scheduled = True
                self.loop.call_soon_threadsafe(self._start)

    def bind(self):
        with self.lock:
            self.loop = asyncio.get_running_loop()
            if (self.pending or self.resync) and not self.scheduled:
                self.scheduled = True
                self.loop.call_soon(self._start)

    def _start(self):
        if not self.closed and (self.task is None or self.task.done()):
            self.task = asyncio.create_task(self._drain())

    async def _drain(self):
        while True:
            with self.lock:
                if self.resync:
                    self.resync = False
                    item = None
                elif self.pending:
                    item = self.pending.popleft()
                    self.bytes -= item[2]
                else:
                    self.scheduled = False
                    return
            try:
                if item is None:
                    await self.recover()
                else:
                    await self.consume(item[0], item[1])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Bridge notification delivery failed")

    async def flush(self):
        self.bind()
        while True:
            self._start()
            if self.task:
                await asyncio.shield(self.task)
            with self.lock:
                if not self.pending and not self.resync:
                    return

    async def close(self):
        with self.lock:
            self.closed = True
            self.pending.clear()
            self.bytes = 0
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
