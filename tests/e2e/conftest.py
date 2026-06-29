from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


class InMemoryCache:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.connected = False
        self._redis = None

    @staticmethod
    def _serialize_key_part(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return [InMemoryCache._serialize_key_part(item) for item in value]
        if isinstance(value, (set, frozenset)):
            normalized = [InMemoryCache._serialize_key_part(item) for item in value]
            return sorted(
                normalized,
                key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
            )
        if isinstance(value, dict):
            return {
                str(key): InMemoryCache._serialize_key_part(item)
                for key, item in sorted(value.items(), key=lambda entry: str(entry[0]))
            }
        return {"__type__": type(value).__name__, "__value__": str(value)}

    def generate_key(self, prefix: str, *args: Any) -> str:
        payload = json.dumps(
            [self._serialize_key_part(arg) for arg in args],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"{prefix}:{digest}"

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False
        self.store.clear()

    def is_configured(self) -> bool:
        return True

    async def ping(self) -> bool:
        return self.connected

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        self.store[key] = value
        return True

    async def delete(self, key: str) -> int:
        if key in self.store:
            del self.store[key]
            return 1
        return 0


def _seed_sqlite_database(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.executescript(
            """
            CREATE TABLE person (
                id INTEGER PRIMARY KEY,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                email TEXT,
                company_id INTEGER NOT NULL,
                is_active INTEGER NOT NULL
            );

            CREATE TABLE location (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                code TEXT,
                company_id INTEGER NOT NULL,
                is_active INTEGER NOT NULL
            );

            CREATE TABLE work_item (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL,
                priority INTEGER NOT NULL,
                scheduled_date TEXT,
                assignee_id INTEGER,
                location_id INTEGER,
                company_id INTEGER NOT NULL,
                created_by INTEGER,
                updated_by INTEGER,
                date_created TEXT,
                date_updated TEXT
            );
            """
        )

        cur.executemany(
            "INSERT INTO person (id, first_name, last_name, email, company_id, is_active) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, "Asha", "Patel", "asha@example.com", 1, 1),
                (2, "Bala", "Iyer", "bala@example.com", 1, 1),
            ],
        )
        cur.executemany(
            "INSERT INTO location (id, name, code, company_id, is_active) VALUES (?, ?, ?, ?, ?)",
            [
                (1, "HQ", "HQ", 1, 1),
                (2, "Plant 1", "PL1", 1, 1),
            ],
        )

        rows: list[tuple[Any, ...]] = []
        for idx in range(1, 26):
            rows.append(
                (
                    idx,
                    f"Open item {idx}",
                    "Open work item",
                    0,
                    1 if idx % 3 == 0 else 2,
                    f"2026-03-{(idx % 9) + 1:02d} 09:00:00",
                    1 if idx % 2 else 2,
                    1 if idx % 2 else 2,
                    1,
                    1,
                    1,
                    "2026-03-01 08:00:00",
                    "2026-03-01 08:00:00",
                )
            )
        for idx in range(26, 29):
            rows.append(
                (
                    idx,
                    f"In progress item {idx}",
                    "In progress work item",
                    1,
                    2,
                    "2026-03-02 10:00:00",
                    1,
                    1,
                    1,
                    1,
                    1,
                    "2026-03-01 08:00:00",
                    "2026-03-01 08:00:00",
                )
            )
        for idx in range(29, 32):
            rows.append(
                (
                    idx,
                    f"Done item {idx}",
                    "Done work item",
                    2,
                    3,
                    "2026-03-02 11:00:00",
                    2,
                    2,
                    1,
                    1,
                    1,
                    "2026-03-01 08:00:00",
                    "2026-03-01 08:00:00",
                )
            )

        cur.executemany(
            """
            INSERT INTO work_item (
                id, title, description, status, priority, scheduled_date,
                assignee_id, location_id, company_id, created_by, updated_by,
                date_created, date_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def sqlite_db_url(tmp_path: Path) -> str:
    db_path = tmp_path / "e2e.sqlite3"
    _seed_sqlite_database(db_path)
    return f"sqlite:///{db_path}"


@contextmanager
def app_client_context(monkeypatch: pytest.MonkeyPatch, db_url: str):
    from app.assistant.engine.intent import intent_detection_service, intent_service
    from app.assistant.engine.router import router_service
    from app.assistant.engine.sql import sql_builder_service
    from app.assistant.nodes.core import chat_node as chat_node_module
    from app.api.v1.endpoints import chat as chat_endpoint
    from app.config import get_settings
    from app.core import lifespan as app_lifespan
    from app.core.dependencies import service_container
    from app.domains.registry import DomainRegistry
    from app.main import app

    async def _raise_no_llm(*_args, **_kwargs):
        raise RuntimeError("LLM access disabled in e2e tests")

    fake_cache = InMemoryCache()
    settings = get_settings()
    original_settings = {
        "DATABASE_URL": settings.DATABASE_URL,
        "REDIS_URL": settings.REDIS_URL,
        "CACHE_ENABLED": settings.CACHE_ENABLED,
        "APP_ENV": settings.APP_ENV,
        "DOMAIN": settings.DOMAIN,
        "APPS_CONFIG_PATH": settings.APPS_CONFIG_PATH,
        "DEFAULT_CHAT_APP_ID": settings.DEFAULT_CHAT_APP_ID,
    }

    settings.DATABASE_URL = db_url
    settings.REDIS_URL = ""
    settings.CACHE_ENABLED = False
    settings.APP_ENV = "test"
    settings.DOMAIN = "starter"
    settings.APPS_CONFIG_PATH = None
    settings.DEFAULT_CHAT_APP_ID = None

    monkeypatch.setenv("DOMAIN", "starter")
    monkeypatch.delenv("APPS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("DEFAULT_CHAT_APP_ID", raising=False)
    DomainRegistry._instance = None
    domain = DomainRegistry.get_current_domain()
    domain._manifest["query_templates"] = {}

    monkeypatch.setattr(router_service, "ainvoke_with_retry", _raise_no_llm)
    monkeypatch.setattr(intent_service, "ainvoke_with_retry", _raise_no_llm)
    monkeypatch.setattr(intent_detection_service, "ainvoke_with_retry", _raise_no_llm)
    monkeypatch.setattr(sql_builder_service, "ainvoke_with_retry", _raise_no_llm)
    monkeypatch.setattr(chat_node_module, "ainvoke_with_retry", _raise_no_llm)

    monkeypatch.setattr(service_container, "cache", fake_cache)

    service_container._container = None
    app_lifespan.workflow = None
    chat_endpoint.chat_service._target = None
    chat_endpoint.user_service._target = None

    try:
        with TestClient(app) as client:
            yield client
    finally:
        service_container._container = None
        app_lifespan.workflow = None
        chat_endpoint.chat_service._target = None
        chat_endpoint.user_service._target = None
        DomainRegistry._instance = None

        settings.DATABASE_URL = original_settings["DATABASE_URL"]
        settings.REDIS_URL = original_settings["REDIS_URL"]
        settings.CACHE_ENABLED = original_settings["CACHE_ENABLED"]
        settings.APP_ENV = original_settings["APP_ENV"]
        settings.DOMAIN = original_settings["DOMAIN"]
        settings.APPS_CONFIG_PATH = original_settings["APPS_CONFIG_PATH"]
        settings.DEFAULT_CHAT_APP_ID = original_settings["DEFAULT_CHAT_APP_ID"]


@pytest.fixture()
def app_client(monkeypatch: pytest.MonkeyPatch, sqlite_db_url: str):
    with app_client_context(monkeypatch, sqlite_db_url) as client:
        yield client
