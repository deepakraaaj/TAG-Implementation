"""
Multi-Tenant Database Manager
Resolves database connections through the configured app registry.
"""

import asyncio
from contextlib import asynccontextmanager
import logging
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import aiomysql
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.apps import AppRegistry
from app.config import get_settings

_engines: Dict[str, Any] = {}
logger = logging.getLogger(__name__)


class MultiTenantDatabaseManager:
    """
    Manages database connections for multi-tenant architecture.
    Selects appropriate database based on app_id from payload.
    """

    @staticmethod
    def _registry() -> AppRegistry:
        return AppRegistry.from_settings(get_settings())

    @classmethod
    def get_database_url(cls, app_id: str) -> Optional[str]:
        """Get database URL for a specific app_id"""
        config = cls._registry().resolve_optional(app_id)
        return config.database_url if config is not None else None

    @staticmethod
    def _engine_kwargs(db_url: str) -> dict[str, Any]:
        settings = get_settings()
        kwargs: dict[str, Any] = {
            "echo": False,
            "pool_pre_ping": True,
        }
        if "sqlite" in str(db_url or "").lower():
            return kwargs
        kwargs.update(
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_timeout=settings.DB_POOL_TIMEOUT,
            pool_recycle=settings.DB_POOL_RECYCLE,
        )
        return kwargs

    @classmethod
    async def get_connection(cls, app_id: str):
        """Get a database connection for a specific app_id"""
        db_url = cls.get_database_url(app_id)
        if not db_url:
            available = [registered_app_id for registered_app_id, _config in cls._registry().list_apps()]
            raise ValueError(f"Unknown app_id: {app_id}. Available: {available}")

        settings = get_settings()
        parsed = urlsplit(db_url.replace("mysql+aiomysql://", "mysql://"))
        attempts = max(1, int(settings.DB_CONNECT_RETRIES or 1))
        backoff_seconds = max(0.0, float(settings.DB_CONNECT_RETRY_BACKOFF_SECONDS or 0.0))

        for attempt in range(1, attempts + 1):
            try:
                conn = await aiomysql.connect(
                    host=parsed.hostname,
                    port=parsed.port or 3306,
                    user=parsed.username,
                    password=parsed.password,
                    db=parsed.path.lstrip("/"),
                    autocommit=True,
                    connect_timeout=settings.DB_POOL_TIMEOUT,
                )
                return conn
            except Exception as exc:
                if attempt >= attempts:
                    raise ConnectionError(f"Failed to connect to {app_id} database: {exc}") from exc
                sleep_for = backoff_seconds * attempt
                logger.warning(
                    "Retrying database connection for app_id=%s attempt=%s/%s sleep=%.2fs error=%s",
                    app_id,
                    attempt,
                    attempts,
                    sleep_for,
                    type(exc).__name__,
                )
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)

    @classmethod
    async def get_engine(cls, app_id: str):
        """Get SQLAlchemy async engine for a specific app_id (for ORM operations)"""
        if app_id in _engines:
            return _engines[app_id]

        db_url = cls.get_database_url(app_id)
        if not db_url:
            raise ValueError(f"Unknown app_id: {app_id}")

        engine = create_async_engine(db_url, **cls._engine_kwargs(db_url))
        _engines[app_id] = engine
        return engine

    @classmethod
    @asynccontextmanager
    async def get_session(cls, app_id: str):
        """Get async session for a specific app_id"""
        engine = await cls.get_engine(app_id)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        
        async with async_session() as session:
            yield session

    @classmethod
    async def execute_query(cls, app_id: str, query: str, params: list = None):
        """Execute a raw SQL query against a specific database"""
        conn = await cls.get_connection(app_id)
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(query, params or [])
                result = await cursor.fetchall()
                return result
        finally:
            conn.close()

    @classmethod
    async def list_available_databases(cls) -> dict[str, str]:
        """List all configured databases with their descriptions"""
        return {
            app_id: config.display_name
            for app_id, config in cls._registry().list_apps()
        }

    @classmethod
    async def verify_connection(cls, app_id: str) -> bool:
        """Verify if a database connection is working"""
        try:
            conn = await cls.get_connection(app_id)
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT 1")
                result = await cursor.fetchone()
                conn.close()
                return result is not None
        except Exception:
            logger.exception("Connection verification failed for app_id=%s", app_id)
            return False

    @classmethod
    async def verify_startup_connections(cls, app_ids: list[str] | None = None) -> dict[str, bool]:
        registry = cls._registry()
        target_app_ids = app_ids or [app_id for app_id, _config in registry.list_apps()]
        return {
            app_id: await cls.verify_connection(app_id)
            for app_id in target_app_ids
        }

    @staticmethod
    async def close_all_connections():
        """Close all open database connections"""
        for engine in _engines.values():
            await engine.dispose()
        _engines.clear()
