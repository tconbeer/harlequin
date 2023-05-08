.PHONY: check
check:
	pytest
	black .
	ruff . --fix
	mypy

.PHONY: lint
lint:
	black .
	ruff . --fix
	mypy
