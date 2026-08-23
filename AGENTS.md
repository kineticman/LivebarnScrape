# Repository Guidelines

## Project Structure & Module Organization

The application is a Python 3.11 Flask service centered on `livebarn_manager.py`. Catalog and stream-capture utilities live in `build_catalog.py` and `refresh_single.py`; shared schedule transformations are in `schedule_utils.py`. Rink integrations belong in `schedule_providers/`: implement `ScheduleProvider` from `base_provider.py`, then register the provider in `schedule_providers/__init__.py`. Deployment files are `Dockerfile`, `docker-compose.yml`, `entrypoint.sh`, and `livebarn.service`. User documentation lives under `docs/`. Runtime databases belong in `data/` and must remain untracked.

## Build, Test, and Development Commands

- `python -m venv .venv && . .venv/bin/activate` creates and activates a local environment.
- `pip install -r requirements.txt` installs Flask, Playwright, Streamlink, and scheduling dependencies. For browser-based tools, also run `playwright install chromium`.
- `python -m compileall -q .` performs the current lightweight syntax check.
- `python -m unittest discover -s tests -v` runs the committed unit tests.
- `docker compose config` validates Compose configuration.
- `docker compose build` builds the production-style image.
- `docker compose up -d` starts the service at `http://localhost:${SERVER_PORT:-5000}`; use `docker compose logs -f` to inspect startup and scraping failures.

## Coding Style & Naming Conventions

Use four-space indentation and standard PEP 8 naming: `snake_case` for functions and modules, `PascalCase` for classes, and `UPPER_CASE` for constants. Add type hints to public helpers and provider interfaces. Keep network requests bounded with timeouts, log actionable context through the module logger, and isolate rink-specific parsing inside its provider. No formatter or linter is configured, so keep changes consistent with adjacent code and avoid unrelated reformatting.

## Testing Guidelines

Tests use Python's `unittest` framework; no coverage threshold is enforced. Before submitting, run the unit, compile, and Compose checks above, then smoke-test affected UI/API routes and playlist/XMLTV output. Provider changes should verify date parsing, surface mappings, empty responses, and upstream failures. Add tests under `tests/` using names such as `test_schedule_utils.py` and methods named `test_<behavior>`.

## Commit & Pull Request Guidelines

Recent history uses short, imperative subjects such as `Fix playlist generation...`, `Add PIN code support...`, and `Bump to v2.3`. Keep each commit focused. Pull requests should explain the user-visible effect, list verification commands, link relevant issues, and include screenshots for web UI changes. Call out schema, environment-variable, provider, or deployment changes explicitly.

## Security & Configuration

Never commit `.env`, LiveBarn credentials, captured HAR files, logs, or databases. Treat `dev/` and `data/` as local-only. Document new environment variables in `README.md` and `docker-compose.yml`, using safe defaults only when appropriate.
