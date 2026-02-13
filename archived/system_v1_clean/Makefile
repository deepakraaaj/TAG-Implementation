# TAG Backend Makefile

.PHONY: up down restart logs test clean

up:
	docker compose up --build -d

down:
	docker compose down

restart:
	docker compose restart tag_backend

logs:
	docker logs -f tag_backend

test:
	python3.10 -m unittest discover tests

test-docker:
	docker exec -it tag_backend python3 tests/test_history.py

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
