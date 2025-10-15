.PHONY: check
check:
	uv run ruff format .
	uv run ruff check . --fix
	uv run pytest -m "not online"
	uv run mypy

.PHONY: lint
lint:
	uv run ruff format .
	uv run ruff check . --fix
	uv run mypy

.PHONY: serve
serve:
	uv run textual run --dev -c harlequin -P dev -f . f1.db

.PHONY: sqlite
sqlite:
	uv run textual run --dev -c harlequin -P sqlite

.PHONY: keys
keys:
	uv run textual run --dev -c harlequin --keys

marketing: $(wildcard static/themes/*.svg) static/harlequin.gif

static/themes/%.svg: pyproject.toml src/scripts/export_screenshots.py
	uv run python src/scripts/export_screenshots.py

static/harlequin.gif: static/harlequin.mp4
	ffmpeg -i static/harlequin.mp4 -vf "fps=24,scale=640:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" -loop 0 static/harlequin.gif

profiles: .profiles/buffers.html .profiles/fast_query.html

.profiles/buffers.html: src/scripts/profile_buffers.py pyproject.toml $(shell find src/harlequin -type f)
	uv run pyinstrument -r html -o .profiles/buffers.html "src/scripts/profile_buffers.py"
	
.profiles/fast_query.html: src/scripts/profile_fast_query.py pyproject.toml $(shell find src/harlequin -type f)
	uv run pyinstrument -r html -o .profiles/fast_query.html "src/scripts/profile_fast_query.py"
