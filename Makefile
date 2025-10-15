.PHONY: check
check:
	poetry run ruff format .
	poetry run ruff check . --fix
	poetry run pytest -m "not online"
	poetry run mypy

.PHONY: lint
lint:
	poetry run ruff format .
	poetry run ruff check . --fix
	poetry run mypy

.PHONY: serve
serve:
	poetry run textual run --dev -c harlequin -P dev -f . f1.db

.PHONY: sqlite
sqlite:
	poetry run textual run --dev -c harlequin -P sqlite

.PHONY: keys
keys:
	poetry run textual run --dev -c harlequin --keys

marketing: $(wildcard static/themes/*.svg) static/harlequin.gif

static/themes/%.svg: pyproject.toml src/scripts/export_screenshots.py
	poetry run python src/scripts/export_screenshots.py

static/harlequin.gif: static/harlequin.mp4
	ffmpeg -i static/harlequin.mp4 -vf "fps=24,scale=640:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" -loop 0 static/harlequin.gif

profiles: .profiles/buffers.html .profiles/fast_query.html

.profiles/buffers.html: src/scripts/profile_buffers.py pyproject.toml $(shell find src/harlequin -type f)
	poetry run pyinstrument -r html -o .profiles/buffers.html "src/scripts/profile_buffers.py"
	
.profiles/fast_query.html: src/scripts/profile_fast_query.py pyproject.toml $(shell find src/harlequin -type f)
	poetry run pyinstrument -r html -o .profiles/fast_query.html "src/scripts/profile_fast_query.py"
