"""Read aggregate Linux resource counters without shell commands or process data."""

import os
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from threading import Lock
from time import monotonic


def _cpu_snapshot(text):
    lines = text.splitlines()
    values = [int(value) for value in lines[0].split()[1:9]]
    if not lines[0].startswith('cpu ') or len(values) < 4 or any(value < 0 for value in values):
        raise ValueError('Invalid CPU counters')
    # Guest ticks are already included in user/nice; idle includes I/O wait.
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    cores = sum(line.startswith('cpu') and line[3:4].isdigit() for line in lines)
    return sum(values), idle, cores


def _memory_snapshot(text):
    fields = {}
    for line in text.splitlines():
        key, _, value = line.partition(':')
        if key in {'MemTotal', 'MemAvailable'}:
            fields[key] = int(value.split()[0]) * 1024
    total, available = fields['MemTotal'], fields['MemAvailable']
    if total <= 0 or not 0 <= available <= total:
        raise ValueError('Invalid memory counters')
    return {'total_bytes': total, 'available_bytes': available,
            'used_bytes': total - available, 'used_percent': round(100 * (total - available) / total, 2)}


def _resource_status(value, warning, critical):
    if value is None:
        return 'unknown'
    return 'critical' if value >= critical else 'warning' if value >= warning else 'ok'


class ResourceMonitor:
    """Nonblocking CPU sampling between requests; memory uses Linux MemAvailable."""

    def __init__(self, proc_dir=None, *, clock=monotonic):
        configured = proc_dir or os.environ.get('LUCRARI_HOST_PROC_DIR')
        self.proc_dir = Path(configured or '/proc')
        self.scope = 'host' if configured else 'system'
        self.clock = clock
        self.lock = Lock()
        self.previous = None
        self.cached_cpu = None
        self.cached_window = None

    def snapshot(self):
        with self.lock:
            result = {'scope': self.scope, 'sampled_at': datetime.now(UTC).isoformat(),
                      'cpu': {'status': 'unknown', 'used_percent': None, 'logical_cores': None,
                              'sample_window_seconds': None},
                      'memory': {'status': 'unknown', 'used_percent': None, 'total_bytes': None,
                                 'available_bytes': None, 'used_bytes': None},
                      'uptime_seconds': None}
            try:
                total, idle, cores = _cpu_snapshot((self.proc_dir / 'stat').read_text())
                now = self.clock()
                if self.previous is None:
                    self.previous = (now, total, idle)
                elif now - self.previous[0] >= 1:
                    before, previous_total, previous_idle = self.previous
                    delta, idle_delta = total - previous_total, idle - previous_idle
                    if delta > 0 and 0 <= idle_delta <= delta:
                        self.cached_cpu = round(100 * (delta - idle_delta) / delta, 2)
                        self.cached_window = round(now - before, 2)
                    else:
                        self.cached_cpu = self.cached_window = None
                    self.previous = (now, total, idle)
                result['cpu'].update(used_percent=self.cached_cpu, logical_cores=cores,
                                     sample_window_seconds=self.cached_window,
                                     status=_resource_status(self.cached_cpu, 90, 98))
            except (OSError, ValueError, IndexError):
                self.previous = self.cached_cpu = self.cached_window = None
            try:
                memory = _memory_snapshot((self.proc_dir / 'meminfo').read_text())
                result['memory'].update(memory, status=_resource_status(memory['used_percent'], 85, 95))
            except (OSError, ValueError, KeyError, IndexError):
                pass
            try:
                uptime = float((self.proc_dir / 'uptime').read_text().split()[0])
                if isfinite(uptime) and uptime >= 0:
                    result['uptime_seconds'] = round(uptime, 2)
            except (OSError, ValueError, IndexError):
                pass
            statuses = [result[name]['status'] for name in ('cpu', 'memory')]
            result['status'] = next((status for status in ('critical', 'warning', 'unknown')
                                     if status in statuses), 'ok')
            return result
