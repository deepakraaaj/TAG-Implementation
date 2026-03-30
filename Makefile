# TAG Backend Makefile

.PHONY: up down restart logs test test-pytest quality-gate benchmark-llm onboard-domain generate-domain clean

up:
	docker compose up --build -d

down:
	docker compose down

restart:
	docker compose restart tag_backend

logs:
	docker compose logs -f tag_backend

test:
	pytest -q

test-pytest:
	pytest -q

quality-gate:
	pytest -q
	pytest -q tests/unit/api/test_chat_endpoint_stream_contract.py tests/unit/chat/test_chat_service_stream_completion.py tests/unit/chat/test_chat_service_timeouts.py

benchmark-llm:
	python3 scripts/benchmark_llm.py

onboard-domain:
	.venv/bin/python scripts/onboard_domain.py --help

generate-domain:
	.venv/bin/python scripts/generate_domain.py --help

clean:
	find . -type d \( -name "__pycache__" -o -name ".pytest_cache" -o -name ".mypy_cache" -o -name ".ruff_cache" \) -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -f .coverage
