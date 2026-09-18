from __future__ import annotations

from pathlib import Path
from typing import Any


class LocalMetrics:
    def __init__(self) -> None:
        self._prev_cpu: tuple[int, int] | None = None

    def _cpu_times(self) -> tuple[int, int] | None:
        try:
            parts = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
            values = [int(x) for x in parts]
            total = sum(values)
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            return total, idle
        except Exception:
            return None

    def cpu_percent(self) -> float | None:
        now = self._cpu_times()
        if now is None:
            return None
        prev = self._prev_cpu
        self._prev_cpu = now
        if prev is None:
            return None
        total_delta = now[0] - prev[0]
        idle_delta = now[1] - prev[1]
        if total_delta <= 0:
            return None
        return round(100.0 * (total_delta - idle_delta) / total_delta, 2)

    def memory_percent(self) -> float | None:
        try:
            data: dict[str, int] = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, value = line.split(":", 1)
                data[key] = int(value.strip().split()[0])
            total = data["MemTotal"]
            available = data.get("MemAvailable", data.get("MemFree", 0))
            return round(100.0 * (total - available) / total, 2) if total else None
        except Exception:
            return None

    def load_average(self) -> tuple[float | None, float | None, float | None]:
        try:
            a, b, c, *_ = Path("/proc/loadavg").read_text().split()
            return float(a), float(b), float(c)
        except Exception:
            return None, None, None

    def cpu_temperature(self) -> float | None:
        values: list[float] = []
        for path in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
            try:
                raw = float(path.read_text().strip())
                value = raw / 1000.0 if raw > 500 else raw
                if 0 < value < 150:
                    values.append(value)
            except Exception:
                continue
        return round(max(values), 1) if values else None

    def snapshot(self) -> dict[str, Any]:
        load1, load5, load15 = self.load_average()
        return {
            "host_cpu_percent": self.cpu_percent(),
            "host_memory_percent": self.memory_percent(),
            "load_1m": load1,
            "load_5m": load5,
            "load_15m": load15,
            "cpu_temperature_c": self.cpu_temperature(),
        }
