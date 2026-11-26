import json
import os
import shutil
import subprocess
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

from deploy.personal import deploy_host as host

OLD = "tradingagents.service.backend.runtime"
NEW = "finbotagents.service.backend.runtime"


def test_compose_transition_changes_only_the_three_runtime_references():
    original = (f"api: {OLD}:application\nworker: {OLD}\nmaintenance: {OLD}\n"
                "other: unchanged\n")
    updated = host.transition_compose(original)
    assert updated == original.replace(OLD, NEW)
    assert host.transition_compose(updated) == updated


@pytest.mark.parametrize("text", ["nothing", OLD, OLD * 4, NEW + OLD * 3])
def test_compose_transition_rejects_unexpected_layout(text):
    with pytest.raises(host.DeploymentError):
        host.transition_compose(text)


def test_compose_validation_checks_only_the_three_expected_entrypoints():
    config = {"services": {
        "api": {"entrypoint": ["uvicorn", OLD + ":application", "--factory"], "environment": {"TEST": "v"}},
        "worker": {"entrypoint": ["python", "-m", OLD], "command": ["serve-worker"]},
        "maintenance": {"entrypoint": ["python", "-m", OLD], "command": ["maintain"]},
        "edge": {"image": "caddy:2"},
    }}
    expected = host.expected_compose_transition(config)
    assert expected["services"]["api"]["entrypoint"][1] == NEW + ":application"
    assert expected["services"]["worker"]["entrypoint"][-1] == NEW
    assert expected["services"]["edge"] == config["services"]["edge"]
    assert config["services"]["api"]["entrypoint"][1] == OLD + ":application"


@pytest.mark.parametrize("entrypoint", [None, ["python", "-m", "unexpected"], [OLD, OLD]])
def test_compose_validation_rejects_unexpected_service_entrypoint(entrypoint):
    config = {"services": {"api": {"entrypoint": entrypoint}, "worker": {}, "maintenance": {}}}
    with pytest.raises(host.DeploymentError, match="Compose"):
        host.expected_compose_transition(config)


def test_real_compose_config_only_changes_expected_entrypoints(tmp_path):
    docker = shutil.which("docker")
    if not docker or subprocess.run([docker, "compose", "version"], capture_output=True).returncode:
        pytest.skip("Docker Compose is not available")
    source = Path(__file__).resolve().parents[1] / "compose.personal.yml"
    original = source.read_text().replace(NEW, OLD)
    compose = tmp_path / "compose.yml"
    env_file = tmp_path / "env"
    env_file.write_text("LUCRARI_IMAGE=example.invalid/finbot:test\n"
                        "LUCRARI_PRIVATE_DIR=/tmp/finbot-test-private\nLUCRARI_DOMAIN=example.invalid\n")
    command = [docker, "compose", "--project-name", "finbot-compose-test", "--env-file", str(env_file),
               "-f", str(compose), "config", "--format", "json"]
    env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    compose.write_text(original)
    before = json.loads(subprocess.check_output(command, env=env, text=True))
    expected = host.expected_compose_transition(before)
    compose.write_text(host.transition_compose(original))
    after = json.loads(subprocess.check_output(command, env=env, text=True))
    assert after == expected


@pytest.mark.parametrize("package", ["tradingagents", "finbotagents"])
def test_probe_works_for_the_current_image_package(monkeypatch, capsys, package):
    for name in ("finbotagents", "tradingagents"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    root = types.ModuleType(package)
    root.__path__ = []
    service = types.ModuleType(f"{package}.service")
    service.__path__ = []
    backend = types.ModuleType(f"{package}.service.backend")
    backend.__path__ = []
    runtime = types.ModuleType(f"{package}.service.backend.runtime")
    repository = types.ModuleType(f"{package}.service.backend.repository")
    runtime.settings_from_env = lambda: object()

    class Connection:
        def execute(self, sql):
            if "clock_timestamp" in sql:
                return types.SimpleNamespace(fetchone=lambda: {"value": 100})
            if "refreshed_at" in sql:
                return types.SimpleNamespace(fetchone=lambda: {"value": 90})
            if "count(*)" in sql:
                return types.SimpleNamespace(fetchone=lambda: {"value": 0})
            return types.SimpleNamespace(fetchone=lambda: {"value": None})

    class Repository:
        def __init__(self, settings):
            pass

        def provider_models(self, provider):
            return ["model"]

        @contextmanager
        def transaction(self):
            yield Connection()

    repository.Repository = Repository
    for module in (root, service, backend, runtime, repository):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(host.importlib.util if hasattr(host, "importlib") else __import__("importlib").util,
                        "find_spec", lambda name: object() if name == package else None)
    exec(host.PROBE, {})
    assert json.loads(capsys.readouterr().out)["ok"] is True
