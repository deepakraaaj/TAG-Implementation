from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import suppress

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from tests.e2e.conftest import app_client_context


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _docker_mysql_admin_url() -> str | None:
    container_name = str(os.getenv("E2E_MYSQL_DOCKER_CONTAINER", "lightning_db")).strip()
    if not container_name:
        return None

    try:
        env_raw = subprocess.check_output(
            ["docker", "inspect", container_name, "--format", "{{json .Config.Env}}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        port_raw = subprocess.check_output(
            [
                "docker",
                "inspect",
                container_name,
                "--format",
                "{{(index (index .NetworkSettings.Ports \"3306/tcp\") 0).HostPort}}",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None

    env_items = json.loads(env_raw)
    env_map = {}
    for item in env_items:
        if "=" in item:
            key, value = item.split("=", 1)
            env_map[key] = value

    root_password = env_map.get("MYSQL_ROOT_PASSWORD") or env_map.get("MARIADB_ROOT_PASSWORD")
    if not root_password or not port_raw:
        return None

    url = URL.create(
        "mysql+mysqlconnector",
        username="root",
        password=root_password,
        host="127.0.0.1",
        port=int(port_raw),
        database="mysql",
    )
    return url.render_as_string(hide_password=False)


def _mysql_admin_url() -> str:
    if not _truthy(os.getenv("RUN_MYSQL_E2E")):
        pytest.skip("Set RUN_MYSQL_E2E=1 to run MySQL e2e coverage.")

    explicit = str(os.getenv("E2E_MYSQL_ADMIN_URL", "")).strip()
    if explicit:
        url = make_url(explicit)
        if url.get_backend_name() != "mysql":
            pytest.skip("E2E_MYSQL_ADMIN_URL must point to a MySQL-compatible database.")
        sync_query = {k: v for k, v in dict(url.query or {}).items() if k not in {"allowPublicKeyRetrieval", "useSSL"}}
        sync_url = url.set(drivername="mysql+mysqlconnector", query=sync_query)
        return sync_url.render_as_string(hide_password=False)

    autodetected = _docker_mysql_admin_url()
    if autodetected:
        return autodetected

    pytest.skip("MySQL e2e requires E2E_MYSQL_ADMIN_URL or a reachable local lightning_db container.")


def _seed_mysql_database(db_url: str) -> None:
    engine = create_engine(db_url, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                """
                CREATE TABLE person (
                    id INT PRIMARY KEY,
                    first_name VARCHAR(100) NOT NULL,
                    last_name VARCHAR(100) NOT NULL,
                    email VARCHAR(255),
                    company_id INT NOT NULL,
                    is_active TINYINT(1) NOT NULL
                )
                """
            )
            conn.exec_driver_sql(
                """
                CREATE TABLE location (
                    id INT PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    code VARCHAR(50),
                    company_id INT NOT NULL,
                    is_active TINYINT(1) NOT NULL
                )
                """
            )
            conn.exec_driver_sql(
                """
                CREATE TABLE work_item (
                    id INT PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    description TEXT,
                    status INT NOT NULL,
                    priority INT NOT NULL,
                    scheduled_date DATETIME NULL,
                    assignee_id INT NULL,
                    location_id INT NULL,
                    company_id INT NOT NULL,
                    created_by INT NULL,
                    updated_by INT NULL,
                    date_created DATETIME NULL,
                    date_updated DATETIME NULL
                )
                """
            )

            conn.execute(
                text(
                    "INSERT INTO person (id, first_name, last_name, email, company_id, is_active) "
                    "VALUES (:id, :first_name, :last_name, :email, :company_id, :is_active)"
                ),
                [
                    {"id": 1, "first_name": "Asha", "last_name": "Patel", "email": "asha@example.com", "company_id": 1, "is_active": 1},
                    {"id": 2, "first_name": "Bala", "last_name": "Iyer", "email": "bala@example.com", "company_id": 1, "is_active": 1},
                ],
            )
            conn.execute(
                text(
                    "INSERT INTO location (id, name, code, company_id, is_active) "
                    "VALUES (:id, :name, :code, :company_id, :is_active)"
                ),
                [
                    {"id": 1, "name": "HQ", "code": "HQ", "company_id": 1, "is_active": 1},
                    {"id": 2, "name": "Plant 1", "code": "PL1", "company_id": 1, "is_active": 1},
                ],
            )

            work_items = []
            for idx in range(1, 26):
                work_items.append(
                    {
                        "id": idx,
                        "title": f"Open item {idx}",
                        "description": "Open work item",
                        "status": 0,
                        "priority": 1 if idx % 3 == 0 else 2,
                        "scheduled_date": f"2026-03-{(idx % 9) + 1:02d} 09:00:00",
                        "assignee_id": 1 if idx % 2 else 2,
                        "location_id": 1 if idx % 2 else 2,
                        "company_id": 1,
                        "created_by": 1,
                        "updated_by": 1,
                        "date_created": "2026-03-01 08:00:00",
                        "date_updated": "2026-03-01 08:00:00",
                    }
                )
            for idx in range(26, 29):
                work_items.append(
                    {
                        "id": idx,
                        "title": f"In progress item {idx}",
                        "description": "In progress work item",
                        "status": 1,
                        "priority": 2,
                        "scheduled_date": "2026-03-02 10:00:00",
                        "assignee_id": 1,
                        "location_id": 1,
                        "company_id": 1,
                        "created_by": 1,
                        "updated_by": 1,
                        "date_created": "2026-03-01 08:00:00",
                        "date_updated": "2026-03-01 08:00:00",
                    }
                )
            for idx in range(29, 32):
                work_items.append(
                    {
                        "id": idx,
                        "title": f"Done item {idx}",
                        "description": "Done work item",
                        "status": 2,
                        "priority": 3,
                        "scheduled_date": "2026-03-02 11:00:00",
                        "assignee_id": 2,
                        "location_id": 2,
                        "company_id": 1,
                        "created_by": 1,
                        "updated_by": 1,
                        "date_created": "2026-03-01 08:00:00",
                        "date_updated": "2026-03-01 08:00:00",
                    }
                )

            conn.execute(
                text(
                    """
                    INSERT INTO work_item (
                        id, title, description, status, priority, scheduled_date,
                        assignee_id, location_id, company_id, created_by, updated_by,
                        date_created, date_updated
                    ) VALUES (
                        :id, :title, :description, :status, :priority, :scheduled_date,
                        :assignee_id, :location_id, :company_id, :created_by, :updated_by,
                        :date_created, :date_updated
                    )
                    """
                ),
                work_items,
            )
    finally:
        engine.dispose()


@pytest.fixture()
def mysql_db_url() -> Iterator[str]:
    admin_url = _mysql_admin_url()
    admin_engine = create_engine(admin_url, pool_pre_ping=True)
    schema_name = f"tag_e2e_mysql_{int(time.time())}"
    schema_url = make_url(admin_url).set(database=schema_name)
    schema_db_url = schema_url.render_as_string(hide_password=False)

    try:
        with admin_engine.begin() as conn:
            conn.exec_driver_sql(f"CREATE DATABASE `{schema_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        _seed_mysql_database(schema_db_url)
        yield schema_db_url
    finally:
        with suppress(Exception):
            with admin_engine.begin() as conn:
                conn.exec_driver_sql(f"DROP DATABASE IF EXISTS `{schema_name}`")
        admin_engine.dispose()


@pytest.fixture()
def mysql_app_client(monkeypatch: pytest.MonkeyPatch, mysql_db_url: str):
    with app_client_context(monkeypatch, mysql_db_url) as client:
        yield client
