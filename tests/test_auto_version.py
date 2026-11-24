import importlib.util
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
