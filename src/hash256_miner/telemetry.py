"""Hash rate and counters (GPU / CPU split)."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class SlidingWindowMeter:
    window_sec: float = 2.0
    _events: deque[tuple[float, int]] = field(default_factory=deque)

    def add(self, n: int) -> None:
        now = time.perf_counter()
        self._events.append((now, n))
        self._prune(now)

    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0][0] > self.window_sec:
            self._events.popleft()

    def rate_hz(self) -> float:
        now = time.perf_counter()
        self._prune(now)
        if len(self._events) < 2:
            return 0.0
        total = sum(e[1] for e in self._events)
        dt = self._events[-1][0] - self._events[0][0]
        return total / dt if dt > 0 else 0.0


@dataclass
class Telemetry:
    gpu_hashes: int = 0
    cpu_hashes: int = 0
    gpu_meter: SlidingWindowMeter = field(default_factory=SlidingWindowMeter)
    cpu_meter: SlidingWindowMeter = field(default_factory=SlidingWindowMeter)
    total_meter: SlidingWindowMeter = field(default_factory=SlidingWindowMeter)

    def record_gpu(self, n: int) -> None:
        self.gpu_hashes += n
        self.gpu_meter.add(n)
        self.total_meter.add(n)

    def record_cpu(self, n: int) -> None:
        self.cpu_hashes += n
        self.cpu_meter.add(n)
        self.total_meter.add(n)

    def total_rate_mh_s(self) -> float:
        return self.total_meter.rate_hz() / 1e6
