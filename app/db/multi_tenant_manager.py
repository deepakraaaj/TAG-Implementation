"""
Multi-Tenant Database Manager
Resolves database connections through the configured app registry.
"""

from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import aiomysql
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from contextlib import asynccontextmanager

from app.apps import AppRegistry
from app.config import get_settings

_engines: Dict[str, Any] = {}


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

    @classmethod
    async def get_connection(cls, app_id: str):
        """Get a database connection for a specific app_id"""
        db_url = cls.get_database_url(app_id)
        if not db_url:
            available = list(cls._registry().enabled() and dict(cls._registry().list_apps()).keys() or [])
            raise ValueError(f"Unknown app_id: {app_id}. Available: {available}")

        try:
            parsed = urlsplit(db_url.replace("mysql+aiomysql://", "mysql://"))
            conn = await aiomysql.connect(
                host=parsed.hostname,
                port=parsed.port or 3306,
                user=parsed.username,
                password=parsed.password,
                db=parsed.path.lstrip("/"),
                autocommit=True
            )
            return conn
        except Exception as e:
            raise ConnectionError(f"Failed to connect to {app_id} database: {str(e)}")

    @classmethod
    async def get_engine(cls, app_id: str):
        """Get SQLAlchemy async engine for a specific app_id (for ORM operations)"""
        if app_id in _engines:
            return _engines[app_id]

        db_url = cls.get_database_url(app_id)
        if not db_url:
            raise ValueError(f"Unknown app_id: {app_id}")

        engine = create_async_engine(
            db_url,
            echo=False,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
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
        except Exception as e:
            print(f"Connection verification failed for {app_id}: {e}")
            return False

    @staticmethod
    async def close_all_connections():
        """Close all open database connections"""
        for engine in _engines.values():
            await engine.dispose()
        _engines.clear()
