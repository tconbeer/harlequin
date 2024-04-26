.PHONY: check
check:
	black .
	ruff check . --fix
	pytest -m "not online"
	mypy

.PHONY: lint
lint:
	black .
	ruff check . --fix
	mypy

.PHONY: serve
serve:
	textual run --dev -c harlequin -P None -f .

.PHONY: sqlite
sqlite:
	textual run --dev -c harlequin -P sqlite

marketing: $(wildcard static/themes/*.svg) static/harlequin.gif

static/themes/%.svg: pyproject.toml src/scripts/export_screenshots.py
	python src/scripts/export_screenshots.py

static/harlequin.gif: static/harlequin.mp4
	ffmpeg -i static/harlequin.mp4 -vf "fps=24,scale=640:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" -loop 0 static/harlequin.gif

profiles: .profiles/buffers.html .profiles/fast_query.html

.profiles/buffers.html: src/scripts/profile_buffers.py pyproject.toml $(shell find src/harlequin -type f)
	pyinstrument -r html -o .profiles/buffers.html "src/scripts/profile_buffers.py"
	
.profiles/fast_query.html: src/scripts/profile_fast_query.py pyproject.toml $(shell find src/harlequin -type f)
	pyinstrument -r html -o .profiles/fast_query.html "src/scripts/profile_fast_query.py"
