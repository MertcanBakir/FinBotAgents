import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from finbotagents.service.backend import evaluation, maintenance, personal, runtime, worker
from finbotagents.service.errors import AnalysisServiceError
from tests.test_hosted_backend import settings as backend_settings


@pytest.fixture
def runtime_context(monkeypatch):
    settings = SimpleNamespace(personal_chatgpt_enabled=False)
    repo = SimpleNamespace(settings=settings, reap=lambda: [1, 2])
    monkeypatch.setattr(runtime, "settings_from_env", lambda: settings)
    monkeypatch.setattr(runtime, "Repository", lambda _: repo)
    monkeypatch.setattr(runtime, "ReportStorage", lambda *args: object())
    return settings, repo


def test_reap_reports_expired_jobs(runtime_context, capsys):
    assert runtime.main(["reap"]) == 0
    assert json.loads(capsys.readouterr().out) == {"expired_count": 2}


@pytest.mark.parametrize("failed", [0, 2])
def test_evaluate_returns_failure_when_any_evaluation_failed(runtime_context, monkeypatch, capsys, failed):
    class Job:
        def __init__(self, repo):
            self.repo = repo

        def run(self):
            return {"failed": failed}

    monkeypatch.setattr(evaluation, "EvaluationJob", Job)
    assert runtime.main(["evaluate"]) == (1 if failed else 0)
    assert json.loads(capsys.readouterr().out) == {"failed": failed}


@pytest.mark.parametrize("failed", [0, 1])
@pytest.mark.parametrize("apply", [False, True])
def test_cleanup_passes_apply_and_reports_failed_deletions(runtime_context, monkeypatch, capsys, failed, apply):
    seen = []

    def cleanup(repo, storage, *, apply):
        seen.append(apply)
        return {"delete_failed": failed}

    monkeypatch.setattr(maintenance, "cleanup_orphans", cleanup)
    assert runtime.main(["cleanup", *( ["--apply"] if apply else [])]) == (1 if failed else 0)
    assert seen == [apply]
    assert json.loads(capsys.readouterr().out) == {"delete_failed": failed}


def test_apply_rejected_for_other_commands(runtime_context):
    with pytest.raises(SystemExit) as exc:
        runtime.main(["reap", "--apply"])
    assert exc.value.code == 2


@pytest.mark.parametrize(("command", "maintenance_mode"), [("serve-worker", False), ("maintain", True)])
def test_long_running_commands_use_correct_service_mode(runtime_context, monkeypatch, command, maintenance_mode):
    seen = []
    monkeypatch.setattr(runtime, "serve", lambda repo, *, maintenance: seen.append(maintenance) or 0)
    assert runtime.main([command]) == 0
    assert seen == [maintenance_mode]


def test_session_does_not_construct_repository(runtime_context, monkeypatch, capsys):
    monkeypatch.setattr(personal, "manage_session", lambda settings, action, source: {"action": action})
    monkeypatch.setattr(runtime, "Repository", lambda _: pytest.fail("session must not open the database"))
    assert runtime.main(["session", "--action", "status"]) == 0
    assert json.loads(capsys.readouterr().out) == {"action": "status"}


def test_worker_runs_the_supplied_job_and_restores_signal_handlers(runtime_context, monkeypatch):
    job = uuid4()
    seen = []
    monkeypatch.setattr(worker, "Worker", lambda *args: SimpleNamespace(run=seen.append))
    assert runtime.main(["worker", str(job)]) == 0
    assert seen == [job]


def test_personal_worker_rejects_direct_dispatch(runtime_context, capsys):
    runtime_context[0].personal_chatgpt_enabled = True
    assert runtime.main(["worker", str(uuid4())]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "PERSONAL_DATABASE_DISPATCH_REQUIRED"


def test_child_receives_validated_identifiers_and_path(runtime_context, monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(worker, "run_child", lambda *args: seen.append(args))
    job, token = uuid4(), uuid4()
    assert runtime.main(["child", str(job), str(token), str(tmp_path)]) == 0
    assert seen[0][1:] == (job, token, tmp_path)


@pytest.mark.parametrize(("exception", "expected"), [
    (AnalysisServiceError("ACCESS_DENIED"), "ACCESS_DENIED"),
    (ValueError("secret"), "BACKEND_EXECUTION_FAILED"),
])
def test_command_errors_do_not_expose_untrusted_messages(runtime_context, monkeypatch, capsys, exception, expected):
    def fail():
        raise exception

    monkeypatch.setattr(runtime, "settings_from_env", fail)
    assert runtime.main(["reap"]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["error"]["code"] == expected
    assert "secret" not in output


def test_settings_read_from_environment(monkeypatch):
    monkeypatch.delenv("LUCRARI_BACKEND_SETTINGS_FILE", raising=False)
    raw = settings_json()
    monkeypatch.setenv("LUCRARI_BACKEND_SETTINGS", raw)
    assert runtime.settings_from_env().allowed_models == {"openai": ("gpt-5.6-sol",)}


def test_settings_file_requires_private_permissions(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(settings_json())
    monkeypatch.setenv("LUCRARI_BACKEND_SETTINGS_FILE", str(path))
    path.chmod(0o600)
    assert runtime.settings_from_env().allowed_models["openai"] == ("gpt-5.6-sol",)
    path.chmod(0o644)
    with pytest.raises(AnalysisServiceError, match="BACKEND_CONFIGURATION_INVALID"):
        runtime.settings_from_env()


def test_settings_reject_relative_path_and_symlink(monkeypatch, tmp_path):
    for path in ("relative/settings.json", str(tmp_path / "link")):
        if path.endswith("link"):
            (tmp_path / "link").symlink_to(tmp_path / "target")
        monkeypatch.setenv("LUCRARI_BACKEND_SETTINGS_FILE", path)
        with pytest.raises(AnalysisServiceError, match="BACKEND_CONFIGURATION_INVALID"):
            runtime.settings_from_env()


def test_settings_error_does_not_expose_contents(monkeypatch):
    monkeypatch.delenv("LUCRARI_BACKEND_SETTINGS_FILE", raising=False)
    monkeypatch.setenv("LUCRARI_BACKEND_SETTINGS", "private invalid settings")
    with pytest.raises(AnalysisServiceError) as exc:
        runtime.settings_from_env()
    assert "private" not in str(exc.value)


@pytest.mark.parametrize("dispatcher", [None, "invalid"])
def test_application_requires_known_dispatcher(monkeypatch, dispatcher):
    monkeypatch.setattr(runtime, "settings_from_env", backend_settings)
    if dispatcher is None:
        monkeypatch.delenv("LUCRARI_DISPATCHER", raising=False)
    else:
        monkeypatch.setenv("LUCRARI_DISPATCHER", dispatcher)
    with pytest.raises(AnalysisServiceError, match="DISPATCHER_NOT_CONFIGURED"):
        runtime.application()


def test_application_rejects_local_dispatch_for_personal_chatgpt(monkeypatch):
    monkeypatch.setattr(runtime, "settings_from_env", lambda: SimpleNamespace(personal_chatgpt_enabled=True))
    monkeypatch.setenv("LUCRARI_DISPATCHER", "local-process")
    with pytest.raises(AnalysisServiceError, match="PERSONAL_DATABASE_DISPATCH_REQUIRED"):
        runtime.application()


def settings_json():
    import base64

    return json.dumps({
        "database_url": "postgresql://localhost/test",
        "supabase_url": "https://test-project.supabase.co",
        "supabase_publishable_key": "public-test-key",
        "supabase_service_key": "server-only-test-key",
        "encryption_keys": {"v1": base64.b64encode(b"k" * 32).decode()},
        "active_encryption_key": "v1",
        "allowed_models": {"openai": ["gpt-5.6-sol"]},
    })
