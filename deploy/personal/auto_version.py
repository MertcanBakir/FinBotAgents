"""Update the package version and README for one merged pull request."""

import base64
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path


def next_version(version, title):
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)', version)
    if not match:
        raise ValueError('Expected a three-part version')
    major, minor, patch = map(int, match.groups())
    if re.match(r'^(?:feat(?:\([^\n)]*\))?!?|fix(?:\([^\n)]*\))?!):', title, re.I):
        return f'{major}.{minor + 1}.0'
    return f'{major}.{minor}.{patch + 1}'


def render(base_version, title, source, text):
    matches = list(re.finditer(r'^__version__ = "(\d+\.\d+\.\d+)"$', source, re.M))
    if len(matches) != 1:
        raise ValueError('Expected exactly one package version')
    heading = re.match(r'\A# FinBotAgents(?: — v\d+\.\d+\.\d+)?(?=\n|$)', text)
    if not heading:
        raise ValueError('Invalid README heading')
    new = next_version(base_version, title)
    return (source[:matches[0].start(1)] + new + source[matches[0].end(1):],
            text[:heading.start()] + f'# FinBotAgents — v{new}' + text[heading.end():])


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


def package_path(pyproject):
    config = tomllib.loads(pyproject)
    attr = config['tool']['setuptools']['dynamic']['version']['attr']
    if not re.fullmatch(r'[A-Za-z_]\w*\.__version__', attr):
        raise ValueError('Unsupported package version source')
    return attr.split('.')[0] + '/__init__.py'


def package_version(source):
    versions = re.findall(r'^__version__ = "(\d+\.\d+\.\d+)"$', source, re.M)
    if len(versions) != 1:
        raise ValueError('Expected exactly one package version')
    return versions[0]


def check(base_sha, title):
    if not re.fullmatch(r'[0-9a-f]{40}', base_sha):
        raise ValueError('Invalid base commit')
    def from_base(path):
        return subprocess.check_output(['git', 'show', f'{base_sha}:{path}'], text=True)
    base_path = package_path(from_base('pyproject.toml'))
    base_version = package_version(from_base(base_path))
    path = Path(package_path(Path('pyproject.toml').read_text(encoding='utf-8')))
    source, readme = path.read_text(encoding='utf-8'), Path('README.md').read_text(encoding='utf-8')
    if render(base_version, title, source, readme) != (source, readme):
        raise ValueError('PR version or README heading does not match the base version and PR title')


def github_api(path, token, method='GET', data=None):
    request = urllib.request.Request(
        'https://api.github.com' + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                 'Content-Type': 'application/json', 'User-Agent': 'FinBotAgents-versioning'},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def update_pull_request(repo, number, token):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) or not re.fullmatch(r'[1-9]\d*', number):
        raise ValueError('Invalid repository or pull request number')
    prefix = '/repos/' + repo
    pr = github_api(prefix + '/pulls/' + number, token)
    if pr['state'] != 'open' or pr['base']['ref'] != 'main' or pr['head']['repo']['full_name'] != repo:
        raise ValueError('Expected an open same-repository PR targeting main')
    base_sha, head_sha = pr['base']['sha'], pr['head']['sha']
    def content(path, sha):
        value = github_api(prefix + '/contents/' + urllib.parse.quote(path, safe='/') + '?ref=' + sha, token)
        if value['encoding'] != 'base64':
            raise ValueError('Unexpected file encoding')
        return base64.b64decode(value['content']).decode('utf-8')
    base_path = package_path(content('pyproject.toml', base_sha))
    head_path = package_path(content('pyproject.toml', head_sha))
    original = (content(head_path, head_sha), content('README.md', head_sha))
    updated = render(package_version(content(base_path, base_sha)), pr['title'], *original)
    if updated == original:
        print('Version already matches the PR title and base.')
        return
    # Use Git's non-forced ref update to reject concurrent branch changes.
    commit = github_api(prefix + '/git/commits/' + head_sha, token)
    entries = [{'path': path, 'mode': '100644', 'type': 'blob', 'content': text}
               for path, text in zip((head_path, 'README.md'), updated, strict=True)]
    tree = github_api(prefix + '/git/trees', token, 'POST', {'base_tree': commit['tree']['sha'], 'tree': entries})
    result = github_api(prefix + '/git/commits', token, 'POST', {
        'message': 'chore: update PR version', 'tree': tree['sha'], 'parents': [head_sha]})
    ref = urllib.parse.quote(pr['head']['ref'], safe='/')
    github_api(prefix + '/git/refs/heads/' + ref, token, 'PATCH', {'sha': result['sha'], 'force': False})


if __name__ == '__main__':
    if sys.argv[1:] == ['check']:
        check(os.environ['PR_BASE_SHA'], os.environ['PR_TITLE'])
    elif sys.argv[1:] == ['update']:
        update_pull_request(os.environ['GITHUB_REPOSITORY'], os.environ['PR_NUMBER'], os.environ['APP_TOKEN'])
    else:
        raise SystemExit('Expected check or update')
