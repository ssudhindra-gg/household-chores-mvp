Documents

- `_docs/process.md` - how work is organized

Commands

- `uv sync` - install dependencies
- `uv run pytest` - the whole suite
- `uv run pytest tests/test_smoke.py` - one test file

Rules

- Dependencies are added in `pyproject.toml`. Do not add one without
  asking