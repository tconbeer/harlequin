.PHONY: lint
lint:
	black .
	ruff . --fix
	mypy