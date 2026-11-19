from unittest.mock import Mock

from fastapi.testclient import TestClient

from tests.test_hosted_backend import settings
from tradingagents.service.backend.api import create_app
from tradingagents.service.backend.health import ResourceMonitor
from tradingagents.service.errors import AnalysisServiceError


def _proc(tmp_path, *, busy=100, idle=900, available=600):
    (tmp_path / 'stat').write_text(f'cpu  {busy} 0 0 {idle} 0 0 0 0 0 0\ncpu0 0\ncpu1 0\n')
    (tmp_path / 'meminfo').write_text(f'MemTotal: 1000 kB\nMemAvailable: {available} kB\nMemFree: 1 kB\n')
    (tmp_path / 'uptime').write_text('12345.6 23456.7\n')


def test_host_memory_and_cpu_delta_without_blocking(tmp_path):
    clock = Mock(return_value=0)
    monitor = ResourceMonitor(tmp_path, clock=clock)
    _proc(tmp_path)
    first = monitor.snapshot()
    assert first['scope'] == 'host'
    assert first['cpu']['used_percent'] is None
    assert first['memory']['used_percent'] == 40
    assert first['memory']['total_bytes'] == 1024000
    assert first['uptime_seconds'] == 12345.6
    _proc(tmp_path, busy=150, idle=950)
    clock.return_value = 2
    second = monitor.snapshot()
    assert second['cpu']['used_percent'] == 50
    assert second['cpu']['logical_cores'] == 2
    assert second['cpu']['sample_window_seconds'] == 2
    assert second['status'] == 'ok'


def test_resource_warning_is_separate_from_api_liveness(tmp_path):
    _proc(tmp_path, available=100)
    result = ResourceMonitor(tmp_path).snapshot()
    assert result['memory']['status'] == 'warning'
    assert result['status'] == 'warning'


def test_cpu_critical_and_fast_poll_reuses_previous_measurement(tmp_path):
    _proc(tmp_path)
    clock = Mock(return_value=0)
    monitor = ResourceMonitor(tmp_path, clock=clock)
    monitor.snapshot()
    _proc(tmp_path, busy=200, idle=900)
    clock.return_value = 2
    result = monitor.snapshot()
    assert result['cpu']['used_percent'] == 100
    assert result['cpu']['status'] == result['status'] == 'critical'
    _proc(tmp_path, busy=200, idle=1000)
    clock.return_value = 2.2
    cached = monitor.snapshot()
    assert cached['cpu']['used_percent'] == 100
    assert cached['cpu']['sample_window_seconds'] == 2


def test_cpu_cache_reset_and_missing_counters_are_not_zero(tmp_path):
    _proc(tmp_path)
    clock = Mock(return_value=0)
    monitor = ResourceMonitor(tmp_path, clock=clock)
    monitor.snapshot()
    clock.return_value = 2
    _proc(tmp_path, busy=1, idle=1)
    assert monitor.snapshot()['cpu']['used_percent'] is None
    (tmp_path / 'meminfo').unlink()
    result = monitor.snapshot()
    assert result['memory']['used_percent'] is None
    assert result['memory']['status'] == 'unknown'
    assert result['status'] == 'unknown'


def test_non_linux_or_unreadable_host_metrics_do_not_break_health(tmp_path):
    result = ResourceMonitor(tmp_path).snapshot()
    assert result['status'] == 'unknown'
    assert result['cpu']['used_percent'] is None
    assert result['memory']['total_bytes'] is None
    assert 'error' not in result


def test_health_keeps_public_contract_and_restricts_details_to_owner():
    from uuid import uuid4

    owner = uuid4()
    repo, auth, monitor = Mock(), Mock(), Mock()
    repo.settings = settings(personal_user_id=owner)
    auth.authenticate.return_value = owner
    monitor.snapshot.return_value = {'scope': 'host', 'status': 'ok'}
    app = create_app(repository=repo, authenticator=auth, vault=Mock(), probe=Mock(),
                     storage=Mock(), dispatcher=Mock(), resource_monitor=monitor)
    with TestClient(app) as client:
        public = client.get('/healthz')
        assert public.json() == {'status': 'ok'}
        assert public.headers['cache-control'] == 'no-store'
        monitor.snapshot.assert_not_called()
        auth.authenticate.assert_not_called()
        details = client.get('/healthz', headers={'Authorization': 'Bearer owner-token'})
        assert details.status_code == 200
        assert details.json()['resources']['scope'] == 'host'
        assert details.json()['checks'] == {'api': 'ok'}
        auth.authenticate.return_value = uuid4()
        assert client.get('/healthz', headers={'Authorization': 'Bearer other-token'}).status_code == 403
        auth.authenticate.side_effect = AnalysisServiceError('AUTHENTICATION_REQUIRED')
        assert client.get('/healthz', headers={'Authorization': 'Bearer expired-token'}).status_code == 401
        assert monitor.snapshot.call_count == 1
