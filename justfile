check: test lint typecheck

test:
    uv run pytest

lint:
    uv run ruff check src tests

typecheck:
    uv run mypy src
