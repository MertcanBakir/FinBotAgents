# FinBotAgents — v0.8.0

![FinBotAgents](tradingagents/assets/finbotagents.png)

FinBotAgents is the financial analysis backend for Lucrari. It runs a multi-agent research workflow and returns a structured report that the mobile app can display.

## Versioning

The version workflow updates the PR branch from the current main version:
`feat:` and `feat!:` or `fix!:` increase the minor version; other titles increase
the patch version. CI checks both the package version and README heading. ECR
image tags include that version and the exact source commit and CI run.

Install a GitHub App on this repository with Contents read/write and Pull requests
read permission. Configure the repository variable `VERSIONING_APP_CLIENT_ID`
and secret `VERSIONING_APP_PRIVATE_KEY`. Only same-repository PR branches are
updated automatically. Configure `main` protection to require the `CI passed`
check and an up-to-date branch; external PRs need their version updated manually.
If App setup is incomplete, version checks fail until the PR is updated. The
existing production deployment continues to use immutable image digests.

## How it works

Lucrari submits an authenticated request to `POST /v1/analyses`. The API validates the request, reserves a job in PostgreSQL and returns its ID. A separate worker claims the job, runs the analyst, research, trading and risk workflow, then stores the report in private Supabase Storage. Lucrari reads the job status and retrieves the report through the API.

## Develop locally

Use Python 3.11 or newer. The production worker image uses Python 3.13 on Linux ARM64.

```sh
git clone https://github.com/MertcanBakir/FinBotAgents.git
cd FinBotAgents
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,backend]'
python -m pytest
ruff check .
```

For a standalone analysis, run `finbotagents --help` and configure a supported provider using [`.env.example`](.env.example). The existing `tradingagents` command also works. The [local analysis service guide](docs/local-analysis-service.md) covers the non-interactive entry point.

## Run the backend

- [Hosted API and worker](docs/hosted-backend.md) documents the API, authentication and job lifecycle.
- [Single-owner setup](docs/personal-backend.md) explains how to configure the private settings and launch the API, worker, maintenance and Caddy services with `compose.personal.yml` and `Dockerfile.worker`.
- [Provider connections](docs/provider-connections-api.md) describes user-owned model credentials.
- [CI](deploy/personal/CI.md), [deployment](deploy/personal/CD.md) and [database migrations](deploy/personal/MIGRATIONS.md) cover validation and delivery.

## License and attribution

FinBotAgents builds on [TradingAgents by Tauric Research](https://github.com/TauricResearch/TradingAgents). See the [Apache 2.0 license](LICENSE).
