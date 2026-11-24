"""Update the package version and README for one merged pull request."""

import os
import re
import tomllib
from pathlib import Path


def next_version(version, title):
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)', version)
    if not match:
        raise ValueError('Expected a three-part version')
    major, minor, patch = map(int, match.groups())
    if re.match(r'^(?:feat(?:\([^\n)]*\))?!?|fix(?:\([^\n)]*\))?!):', title, re.I):
        return f'{major}.{minor + 1}.0'
    return f'{major}.{minor}.{patch + 1}'


def update_files(package, readme, title):
    source = package.read_text(encoding='utf-8')
    text = readme.read_text(encoding='utf-8')
    matches = list(re.finditer(r'^__version__ = "(\d+\.\d+\.\d+)"$', source, re.M))
    if len(matches) != 1:
        raise ValueError('Expected exactly one package version')
    current = matches[0].group(1)
    heading = f'# FinBotAgents — v{current}'
    if text.splitlines()[0] != heading:
        raise ValueError('README and package versions do not match')
    new = next_version(current, title)
    package.write_text(source[:matches[0].start(1)] + new + source[matches[0].end(1):], encoding='utf-8')
    readme.write_text(text.replace(heading, f'# FinBotAgents — v{new}', 1), encoding='utf-8')
    return new


if __name__ == '__main__':
    config = tomllib.loads(Path('pyproject.toml').read_text(encoding='utf-8'))
    attr = config['tool']['setuptools']['dynamic']['version']['attr']
    if not re.fullmatch(r'[A-Za-z_]\w*\.__version__', attr):
        raise ValueError('Unsupported package version source')
    package = Path(attr.split('.')[0]) / '__init__.py'
    print(update_files(package, Path('README.md'), os.environ['PR_TITLE']))
