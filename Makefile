# `online` tests need a network connection and secrets. Keep this a marker
# expression, not a whole flag: a second -m silently overrides the first.
TEST_MARKERS := not online

.PHONY: check
check:
	uv sync --group test --group static
	uv run ruff format .
	uv run ruff check . --fix
	uv run pytest -m '$(TEST_MARKERS)'
	uv run --python 3.12 --group test pytest -m 'py12 and ($(TEST_MARKERS))'
	uv sync --group test --group static
	uv run mypy
	uv run lint-imports

.PHONY: lint
lint:
	uv sync --group test --group static
	uv run ruff format .
	uv run ruff check . --fix
	uv run mypy
	uv run lint-imports

.PHONY: cold-start
cold-start:
	uv sync --group test --group static
	uv run python scripts/cold_start.py

# what the release publishes to harlequin.sh, staged into dist/artifacts so
# you can read it before a workflow opens the PR that vendors it.
.PHONY: artifacts
artifacts:
	uv sync --group static
	uv run python scripts/publish_artifacts.py

.PHONY: serve
serve:
	uv sync --group dev
	uv run textual run --dev -c harlequin -P dev -f . f1.db

.PHONY: sqlite
sqlite:
	uv sync --group dev
	uv run textual run --dev -c harlequin -P sqlite

.PHONY: keys
keys:
	uv sync --group dev
	uv run textual run --dev -c harlequin --keys

marketing: $(wildcard static/themes/*.svg)

static/themes/%.svg: pyproject.toml scripts/export_screenshots.py
	uv sync --group dev
	uv run scripts/export_screenshots.py

profiles: .profiles/buffers.html .profiles/fast_query.html

.profiles/buffers.html: scripts/profile_buffers.py pyproject.toml $(shell find src/harlequin -type f)
	uv sync --group dev
	uv run pyinstrument -r html -o .profiles/buffers.html "src/scripts/profile_buffers.py"
	
.profiles/fast_query.html: scripts/profile_fast_query.py pyproject.toml $(shell find src/harlequin -type f)
	uv sync --group dev
	uv run pyinstrument -r html -o .profiles/fast_query.html "src/scripts/profile_fast_query.py"
