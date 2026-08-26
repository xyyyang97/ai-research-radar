.PHONY: test network-test lint typecheck ci build clean

test:
	pytest

network-test:
	pytest -m network

lint:
	ruff check .

typecheck:
	mypy src/ai_research_radar

ci: lint typecheck test build

build:
	python -m build

clean:
	rm -rf dist build *.egg-info .pytest_cache .mypy_cache .ruff_cache \
	    src/*.egg-info .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
