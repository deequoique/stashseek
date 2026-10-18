# Contributing to StashSeek Chat

[English](CONTRIBUTING.md) | [简体中文](CONTRIBUTING.zh-CN.md)

Thanks for your interest in StashSeek Chat. Bug reports, documentation fixes, and code are all welcome.

## Ways to help

- **Report a bug.** Open an issue with the commit you ran, the runtime profile (`read`, `full`, or `langbot`), the steps to reproduce, and what you expected. Remove tokens, cookies, and private library content from logs before posting.
- **Improve the docs.** If a step in the [first-run tutorial](docs/tutorials/first-run.md) or a [how-to guide](docs/how-to/README.md) did not match what you saw, a fix is very welcome.
- **Add a content source.** Server-side connectors live in [`app/connectors/`](app/connectors/). [`youtube.py`](app/connectors/youtube.py) and [`bilibili.py`](app/connectors/bilibili.py) are working examples built on [`base.py`](app/connectors/base.py).
- **Pick up an issue.** Issues labeled [`good first issue`](https://github.com/deequoique/stashseek/labels/good%20first%20issue) are scoped for newcomers. [`help wanted`](https://github.com/deequoique/stashseek/labels/help%20wanted) marks larger items where help is welcome.

For anything bigger than a small fix, please open or comment on an issue first, so we can agree on the approach before you spend time on it.

## Development setup

You need Python 3.11+, Node.js 22 with pnpm 11, and Docker.

### Backend

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

cp .env.example .env
docker compose up -d postgres redis
```

`.env.example` only holds placeholders. Saving videos and asking natural-language questions also need `ZHIPU_API_KEY` and an Agent model credential; the tests that CI runs do not. To run the whole stack, follow the [first-run tutorial](docs/tutorials/first-run.md) and [Enable the full library](docs/how-to/run-full-library.md).

Run the tests:

```bash
.venv/bin/python -m pytest -q
```

Tests that need PostgreSQL read the database settings from your environment or `.env`, create their own temporary databases, and skip when no database is reachable. **Only point them at a local, disposable database**, never at a shared or production one.

CI runs the workflows in [`.github/workflows/`](.github/workflows/) on every pull request. Each one lists the exact pytest files it runs, so you can run the same set locally. CI also runs `.venv/bin/alembic heads`, which must print exactly one head; if you add a migration, base it on the current head.

### Browser extension

```bash
cd extension
pnpm install
pnpm typecheck && pnpm lint && pnpm test && pnpm build && pnpm run audit
```

To load the build in Chrome, or to build against a local server with `pnpm package:local`, see [extension/README.md](extension/README.md).

If you change the Web API, run `pnpm generate:api` in `extension/` and commit the regenerated contract. CI fails when `pnpm check:api` finds it out of date.

## Pull requests

- Branch from `dev` and open the pull request against `dev`. `main` is the release branch and deploys to production.
- Keep each pull request to one change, and add or update tests when behavior changes.
- Use the [Conventional Commits](https://www.conventionalcommits.org/) style the history already follows, for example `feat(extension): ...`, `fix(bilibili): ...`, or `docs: ...`.
- Run the CI checks above locally before pushing.
- Update the docs under `docs/` when you change user-visible behavior, configuration, or commands.

## Privacy and secrets

StashSeek Chat holds people's private libraries, so:

- Never commit `.env` files, API keys, cookies, or real user content.
- Keep test fixtures synthetic, or taken from sources you have the right to share.
- Read [Privacy and trust](docs/explanation/privacy-and-trust.md) before changing how content, credentials, or logs are handled.

## Reporting security issues

Please don't report vulnerabilities in public issues. Use GitHub's private reporting instead: **Security → Report a vulnerability** on this repository.

## AI workflow files

`AGENTS.md` and `.trellis/` hold the maintainer's AI-assisted development workflow. You don't need them to contribute, though AI coding tools will read them automatically.

## License

By contributing, you agree that your contributions are licensed under the [GNU Affero General Public License v3.0](LICENSE), the same license as the rest of the project.
