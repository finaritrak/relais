# Contributing

Thanks for helping! Issues and pull requests are welcome, in English or French.

## Setup

```bash
git clone https://github.com/finaritrak/relais
cd relais
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Before opening a pull request

```bash
ruff check . && ruff format . && mypy && pytest --cov
```

- Add a test for every behaviour change. HTTP calls are mocked with `httpx.MockTransport`
  (see `tests/conftest.py`) — tests must never call a real provider.
- **Never commit credentials** or real personal data, even in tests.
- New redaction pattern? Add positive AND negative test cases (what must not be masked).

## Releasing (maintainers)

1. Bump `version` in `pyproject.toml` and `__version__` in `src/relais/__init__.py`.
2. Update `CHANGELOG.md`.
3. Create a GitHub Release `vX.Y.Z` → the *Publish to PyPI* workflow uploads the package.

One-time setup: on pypi.org → *Your projects → Publishing → Add a new pending publisher*,
with owner `finaritrak`, repository `relais`, workflow `publish.yml`, environment `pypi`.
