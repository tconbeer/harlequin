.PHONY: check
check:
	pytest -m "not online"
	black .
	ruff . --fix
	mypy

.PHONY: lint
lint:
	black .
	ruff . --fix
	mypy

.PHONY: serve
serve:
	textual run --dev -c harlequin f1.db

.PHONY: screenshots
screenshots:
	python src/marketer/export_screenshots.py
