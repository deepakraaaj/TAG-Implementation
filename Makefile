# TAG Backend Makefile

.PHONY: up down restart logs test test-pytest quality-gate clean

up:
	docker compose up --build -d

down:
	docker compose down

restart:
	docker compose restart tag_backend

logs:
	docker logs -f tag_backend

test:
	pytest -q

test-pytest:
	pytest -q

quality-gate:
	pytest -q
	pytest -q tests/unit/api/test_chat_endpoint_stream_contract.py tests/unit/chat/test_chat_service_stream_completion.py tests/unit/chat/test_chat_service_timeouts.py

test-docker:
	docker exec -it tag_backend python3 tests/test_history.py

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
