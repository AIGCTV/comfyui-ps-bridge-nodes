"""Bounded blocking work shared by HTTP and native observation."""
import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from .errors import require


class BlockingWork:
    def __init__(self, workers=2, capacity=10):
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ps-bridge")
        self.capacity = capacity
        self.pending = 0

    async def run(self, function, *args, **kwargs):
        require(self.pending < self.capacity, "BUSY", "Bridge work queue is full", retryable=True, status=503)
        self.pending += 1
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self.executor, functools.partial(function, *args, **kwargs))
        # Keep the slot until the actual worker completes, even if HTTP disconnects.
        future.add_done_callback(lambda _: setattr(self, "pending", self.pending - 1))
        return await asyncio.shield(future)

    def close(self):
        self.executor.shutdown(wait=False)

    async def finish(self, function, *args, **kwargs):
        """Internal notifications/cleanup retain their place when HTTP fills the queue."""
        from .errors import BridgeError
        while True:
            try:
                return await self.run(function, *args, **kwargs)
            except BridgeError as exc:
                if exc.code != "BUSY":
                    raise
                await asyncio.sleep(0.05)
