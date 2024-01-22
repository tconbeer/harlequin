.PHONY: check
check:
	black .
	ruff . --fix
	pytest -m "not online"
	mypy

.PHONY: lint
lint:
	black .
	ruff . --fix
	mypy

.PHONY: serve
serve:
	textual run --dev -c harlequin -f .

.PHONY: sqlite
sqlite:
	textual run --dev -c harlequin -a sqlite

marketing: $(wildcard static/themes/*.svg) static/harlequin.gif

static/themes/%.svg: pyproject.toml src/scripts/export_screenshots.py
	python src/scripts/export_screenshots.py

static/harlequin.gif: static/harlequin.mp4
	ffmpeg -i static/harlequin.mp4 -vf "fps=24,scale=640:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" -loop 0 static/harlequin.gif
