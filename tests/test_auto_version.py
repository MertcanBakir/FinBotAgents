import importlib.util
import shutil
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('auto_version', Path(__file__).parents[1] / 'deploy/personal/auto_version.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize(('title', 'expected'), [
    ('fix: repair', '0.8.1'), ('feat: add feature', '0.9.0'),
    ('feat!: breaking', '0.9.0'), ('fix!: breaking', '0.9.0'),
    ('docs: update', '0.8.1'), ('feature: not feat', '0.8.1'),
])
def test_bump(title, expected):
    assert module.next_version('0.8.0', title) == expected


def test_updates_version_and_readme_together(tmp_path):
    package = tmp_path / 'package.py'
    readme = tmp_path / 'README.md'
    package.write_text('__version__ = "0.8.0"\n')
    readme.write_text('# FinBotAgents — v0.8.0\n\nContent\n')
    assert module.update_files(package, readme, 'feat: feature') == '0.9.0'
    assert package.read_text() == '__version__ = "0.9.0"\n'
    assert readme.read_text() == '# FinBotAgents — v0.9.0\n\nContent\n'


def test_refuses_mismatched_readme(tmp_path):
    package = tmp_path / 'package.py'
    readme = tmp_path / 'README.md'
    package.write_text('__version__ = "0.8.0"\n')
    readme.write_text('# FinBotAgents — v0.7.1\n')
    with pytest.raises(ValueError):
        module.update_files(package, readme, 'fix: fix')
    assert package.read_text() == '__version__ = "0.8.0"\n'


def test_pr_target_is_derived_from_base_not_previous_attempt():
    assert module.next_version('0.8.0', 'feat: one') == '0.9.0'
    assert module.next_version('0.8.0', 'fix: one') == '0.8.1'


def test_render_accepts_already_versioned_pr_without_bumping_again():
    package, readme = module.render('0.8.0', 'feat: one', '__version__ = "0.9.0"\n', '# FinBotAgents — v0.9.0\n')
    assert package == '__version__ = "0.9.0"\n'
    assert readme == '# FinBotAgents — v0.9.0\n'


def test_ecr_tag_binds_version_to_run_and_commit():
    from deploy.personal.cd_pipeline import image_tag
    assert image_tag('0.8.1', 'a' * 40, '123', '2') == f'git-{"a" * 40}-v0.8.1-run-123-2'
    with pytest.raises(ValueError):
        image_tag('bad tag', 'a' * 40, '123', '2')


@pytest.mark.skipif(shutil.which('git') is None, reason='Git is not installed in the production worker image')
def test_version_reader_extracts_the_version_from_the_exact_commit(tmp_path, monkeypatch):
    import subprocess

    from deploy.personal.cd_pipeline import version_from_git
    monkeypatch.chdir(tmp_path)
    subprocess.run(['git', 'init', '-q'], check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'], check=True)
    Path('pyproject.toml').write_text('[tool.setuptools.dynamic]\nversion = {attr = "pkg.__version__"}\n')
    Path('pkg').mkdir()
    Path('pkg/__init__.py').write_text('__version__ = "0.8.1"\n')
    subprocess.run(['git', 'add', '.'], check=True)
    subprocess.run(['git', 'commit', '-qm', 'test'], check=True)
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    Path('pkg/__init__.py').write_text('__version__ = "0.9.0"\n')
    assert version_from_git(sha) == '0.8.1'


def test_pr_update_uses_base_version_and_updates_only_version_files(monkeypatch):
    import base64

    project = '[tool.setuptools.dynamic]\nversion = {attr = "finbotagents.__version__"}\n'
    files = {
        ('pyproject.toml', 'a' * 40): project,
        ('pyproject.toml', 'b' * 40): project,
        ('finbotagents/__init__.py', 'a' * 40): '__version__ = "0.8.0"\n',
        ('finbotagents/__init__.py', 'b' * 40): '__version__ = "0.8.0"\n',
        ('README.md', 'b' * 40): '# FinBotAgents — v0.8.0\n',
    }
    requests = []
    def api(path, token, method='GET', data=None):
        requests.append((path, method, data))
        if '/pulls/' in path:
            return {'state': 'open', 'base': {'ref': 'main', 'sha': 'a' * 40},
                    'head': {'ref': 'feature', 'sha': 'b' * 40, 'repo': {'full_name': 'owner/repo'}},
                    'title': 'feat: new feature'}
        if '/contents/' in path:
            filename, sha = path.split('/contents/')[1].split('?ref=')
            return {'encoding': 'base64', 'content': base64.b64encode(files[(filename, sha)].encode()).decode()}
        if method == 'GET':
            return {'tree': {'sha': 'tree'}}
        if path.endswith('/git/trees'):
            return {'sha': 'newtree'}
        if path.endswith('/git/commits'):
            return {'sha': 'newcommit'}
        return {}
    monkeypatch.setattr(module, 'github_api', api)
    module.update_pull_request('owner/repo', '12', 'test')
    tree = next(data for path, method, data in requests if path.endswith('/git/trees'))
    assert {item['path'] for item in tree['tree']} == {'README.md', 'finbotagents/__init__.py'}
    assert all('0.9.0' in item['content'] for item in tree['tree'])
    assert requests[-1][2] == {'sha': 'newcommit', 'force': False}


def test_candidate_tag_uses_version_from_validated_commit(tmp_path, monkeypatch):
    from deploy.personal import cd_pipeline as pipeline

    sha = 'a' * 40
    run = {'repository': {'full_name': pipeline.REPOSITORY},
           'head_repository': {'full_name': pipeline.REPOSITORY}, 'workflow_id': 10,
           'event': 'push', 'head_branch': 'main', 'status': 'completed', 'conclusion': 'success',
           'head_sha': sha, 'id': 100, 'run_attempt': 2}
    def api(path):
        if path.endswith('/actions/runs/100'):
            return run
        if path.endswith('/actions/workflows/ci.yml'):
            return {'id': 10}
        if path.endswith('/git/ref/heads/main'):
            return {'object': {'sha': sha}}
        if '/artifacts?' in path:
            return {'artifacts': [{'name': f'worker-image-{sha}-2', 'expired': False}]}
        if '/jobs?' in path:
            return {'jobs': [{'name': 'CI passed', 'conclusion': 'success'}]}
        raise AssertionError(path)
    monkeypatch.setattr(pipeline, 'github', api)
    monkeypatch.setattr(pipeline, 'version_from_git', lambda ref: '0.8.1' if ref == sha else 'wrong')
    output = tmp_path / 'output'
    monkeypatch.setenv('GITHUB_OUTPUT', str(output))
    pipeline.prepare('100')
    assert f'tag=git-{sha}-v0.8.1-run-100-2' in output.read_text()

