from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable


@dataclass(slots=True)
class MetricSample:
    metric: str
    value: float | str | bool | None
    source: str
    sampled_at: str


class HealthGuard:
    def __init__(
        self,
        collect_fast: Callable[[], Awaitable[dict[str, Any]]],
        collect_normal: Callable[[], Awaitable[dict[str, Any]]],
        on_samples: Callable[[list[MetricSample]], Awaitable[None]],
        on_anomaly: Callable[[str, str, dict[str, Any]], Awaitable[None]],
        on_recovery: Callable[[str, str, dict[str, Any]], Awaitable[None]],
        on_error: Callable[[str, BaseException], None] | None = None,
        on_success: Callable[[str], None] | None = None,
        fast_interval: int = 120,
        normal_interval: int = 300,
    ) -> None:
        self.collect_fast = collect_fast
        self.collect_normal = collect_normal
        self.on_samples = on_samples
        self.on_anomaly = on_anomaly
        self.on_recovery = on_recovery
        self.on_error = on_error
        self.on_success = on_success
        self.fast_interval = fast_interval
        self.normal_interval = normal_interval
        self._streaks: dict[str, int] = {}
        self._active: set[str] = set()

    async def _evaluate(self, values: dict[str, Any]) -> None:
        checks = {
            "cpu_temperature_c": (80.0, "thermal", "CPU temperature is persistently high"),
            "host_cpu_percent": (90.0, "performance", "Host CPU load is persistently high"),
            "host_memory_percent": (90.0, "memory", "Host memory usage is persistently high"),
            "storage_used_percent": (90.0, "storage", "System storage usage is high"),
        }
        for metric, (threshold, category, title) in checks.items():
            value = values.get(metric)
            if not isinstance(value, (int, float)):
                continue
            if float(value) >= threshold:
                self._streaks[metric] = self._streaks.get(metric, 0) + 1
                if self._streaks[metric] >= 3 and metric not in self._active:
                    self._active.add(metric)
                    await self.on_anomaly(
                        category,
                        title,
                        {"metric": metric, "value": value, "threshold": threshold},
                    )
            else:
                was_active = metric in self._active
                self._streaks.pop(metric, None)
                self._active.discard(metric)
                if was_active:
                    await self.on_recovery(
                        category,
                        title,
                        {"metric": metric, "value": value, "threshold": threshold},
                    )

    async def _loop(self, collector: Callable[[], Awaitable[dict[str, Any]]], interval: int, source: str) -> None:
        while True:
            try:
                values = await collector()
                now = datetime.now(UTC).isoformat()
                samples = [
                    MetricSample(metric=k, value=v, source=source, sampled_at=now)
                    for k, v in values.items()
                    if v is not None
                ]
                if samples:
                    await self.on_samples(samples)
                await self._evaluate(values)
                if self.on_success is not None:
                    self.on_success(f"health_guard:{source}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.on_error is not None:
                    self.on_error(f"health_guard:{source}", exc)
                else:
                    print(
                        f"Suzie Doctor Health Guard error | source={source} "
                        f"type={type(exc).__name__} error={exc}",
                        flush=True,
                    )
            await asyncio.sleep(interval)

    async def run(self) -> None:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._loop(self.collect_fast, self.fast_interval, "fast"))
            tg.create_task(self._loop(self.collect_normal, self.normal_interval, "normal"))
