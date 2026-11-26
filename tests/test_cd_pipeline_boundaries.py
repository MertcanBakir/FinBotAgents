import io
import json
import subprocess
import sys
import tarfile

import pytest

from deploy.personal import cd_pipeline as cd

SHA = "a" * 40


def make_archive(path, *, architecture="arm64", os_name="linux", tags=None):
    config = {"architecture": architecture, "os": os_name,
              "config": {"Labels": {"org.opencontainers.image.revision": SHA}},
              "rootfs": {"diff_ids": ["layer"]}}
    contents = {
        "manifest.json": json.dumps([{"RepoTags": tags, "Config": "config.json"}]).encode(),
        "config.json": json.dumps(config).encode(),
    }
    with tarfile.open(path, "w:gz") as archive:
        for name, data in contents.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    return config


@pytest.mark.parametrize(("tags", "arch", "os_name"), [
    (["another:image"], "arm64", "linux"),
    (None, "arm64", "linux"),
    ([cd.IMAGE], "amd64", "linux"),
    ([cd.IMAGE], "arm64", "windows"),
])
def test_saved_image_rejects_wrong_identity_or_platform(tmp_path, tags, arch, os_name):
    archive = tmp_path / "image.tar.gz"
    make_archive(archive, tags=tags, architecture=arch, os_name=os_name)
    with pytest.raises(ValueError):
        cd.saved_config(archive)


@pytest.mark.parametrize(("bad_field", "bad_value"), [
    ("RootFS", {"Layers": ["wrong"]}), ("Architecture", "amd64"), ("Os", "windows"),
    ("Config", {"Labels": {"org.opencontainers.image.revision": "wrong"}}),
])
def test_loaded_image_must_match_validated_archive(tmp_path, monkeypatch, bad_field, bad_value):
    config = make_archive(tmp_path / "image.tar.gz", tags=[cd.IMAGE])
    monkeypatch.setattr(cd, "verify_package", lambda _: {"sha": SHA})
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)
    info = {"RootFS": {"Layers": ["layer"]}, "Architecture": "arm64", "Os": "linux",
            "Config": config["config"]}
    info[bad_field] = bad_value
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: json.dumps([info]))
    with pytest.raises(ValueError, match="does not match"):
        cd.load(tmp_path)


def test_loaded_image_checks_runtime_configuration(tmp_path, monkeypatch):
    config = make_archive(tmp_path / "image.tar.gz", tags=[cd.IMAGE])
    monkeypatch.setattr(cd, "verify_package", lambda _: {"sha": SHA})
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)
    info = {"RootFS": {"Layers": ["layer"]}, "Architecture": "arm64", "Os": "linux",
            "Config": {**config["config"], "Entrypoint": ["unexpected"]}}
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: json.dumps([info]))
    with pytest.raises(ValueError, match="execution configuration"):
        cd.load(tmp_path)


@pytest.mark.parametrize("key", ["Env", "Entrypoint", "Cmd", "User", "WorkingDir", "Healthcheck", "StopSignal"])
def test_loaded_image_rejects_modified_runtime_setting(tmp_path, monkeypatch, key):
    config = make_archive(tmp_path / "image.tar.gz", tags=[cd.IMAGE])
    monkeypatch.setattr(cd, "verify_package", lambda _: {"sha": SHA})
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)
    info = {"RootFS": {"Layers": ["layer"]}, "Architecture": "arm64", "Os": "linux",
            "Config": {**config["config"], key: "modified"}}
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: json.dumps([info]))
    with pytest.raises(ValueError, match="execution configuration"):
        cd.load(tmp_path)


def test_loaded_image_accepts_matching_configuration(tmp_path, monkeypatch):
    config = make_archive(tmp_path / "image.tar.gz", tags=[cd.IMAGE])
    monkeypatch.setattr(cd, "verify_package", lambda _: {"sha": SHA})
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)
    info = {"RootFS": {"Layers": ["layer"]}, "Architecture": "arm64", "Os": "linux",
            "Config": config["config"]}
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: json.dumps([info]))
    assert cd.load(tmp_path) is None


@pytest.mark.parametrize("index", [
    {"manifests": []},
    {"manifests": [{"digest": "one", "platform": {"os": "linux", "architecture": "arm64"}},
                   {"digest": "two", "platform": {"os": "linux", "architecture": "arm64"}}]},
])
def test_published_index_requires_exactly_one_linux_arm_image(index):
    with pytest.raises(ValueError, match="exactly one"):
        cd.published_config(index, lambda _: pytest.fail("must reject before lookup"))


@pytest.mark.parametrize("bad_version,bad_sha,bad_run,bad_attempt", [
    ("v1.0.0", SHA, "1", "1"), ("1.0.0", "bad", "1", "1"),
    ("1.0.0", SHA, "run", "1"), ("1.0.0", SHA, "1", "attempt"),
    ("1.0.0", SHA, "1" * 100, "1"),
])
def test_image_tags_reject_invalid_provenance(bad_version, bad_sha, bad_run, bad_attempt):
    with pytest.raises(ValueError):
        cd.image_tag(bad_version, bad_sha, bad_run, bad_attempt)


@pytest.mark.parametrize(("action", "function"), [
    ("prepare", "prepare"), ("package", "package"), ("load", "load"), ("published", "verify_published"),
])
def test_cli_dispatches_to_requested_action(monkeypatch, action, function):
    seen = []
    monkeypatch.setattr(cd, function, lambda value: seen.append(value))
    monkeypatch.setattr(sys, "argv", ["cd_pipeline.py", action, "chosen-value"])
    cd.main()
    assert seen == ["chosen-value"]


@pytest.mark.parametrize("head_is_current", [True, False])
def test_current_command_rejects_superseded_main(monkeypatch, head_is_current):
    monkeypatch.setattr(sys, "argv", ["cd_pipeline.py", "current", SHA])
    monkeypatch.setattr(cd, "github", lambda path: {"object": {"sha": SHA if head_is_current else "b" * 40}})
    if head_is_current:
        cd.main()
    else:
        with pytest.raises(ValueError, match="superseded"):
            cd.main()


@pytest.mark.parametrize(("attr", "version_source"), [
    ("package.other", '__version__ = "0.8.0"'),
    ("pkg.__version__", "no version here"),
    ("pkg.__version__", '__version__ = "0.8.0"\n__version__ = "0.8.1"'),
])
def test_version_reader_rejects_ambiguous_or_unsupported_version_source(monkeypatch, attr, version_source):
    project = f'[tool.setuptools.dynamic]\nversion = {{attr = "{attr}"}}\n'
    monkeypatch.setattr(subprocess, "check_output", lambda args, **kwargs: project if args[-1].endswith("pyproject.toml") else version_source)
    with pytest.raises(ValueError):
        cd.version_from_git(SHA)


def test_version_reader_rejects_invalid_revision():
    with pytest.raises(ValueError, match="revision"):
        cd.version_from_git("HEAD")


@pytest.mark.parametrize("responses", [[], [{"imageManifest": "{}"}, {"imageManifest": "{}"}]])
def test_published_verification_rejects_missing_or_ambiguous_image(tmp_path, monkeypatch, responses):
    (tmp_path / "manifest.json").write_text(json.dumps({"config_digest": "sha256:expected"}))
    monkeypatch.setenv("IMAGE_TAG", "image-tag")
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: json.dumps({"images": responses}))
    with pytest.raises(ValueError, match="did not return"):
        cd.verify_published(tmp_path)


def test_published_verification_accepts_matching_config(tmp_path, monkeypatch):
    digest = "sha256:expected"
    (tmp_path / "manifest.json").write_text(json.dumps({"config_digest": digest}))
    monkeypatch.setenv("IMAGE_TAG", "image-tag")
    response = {"images": [{"imageManifest": json.dumps({"config": {"digest": digest}})}]}
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: json.dumps(response))
    assert cd.verify_published(tmp_path) is None
