from contextlib import asynccontextmanager
from fastapi import FastAPI
import logging
from ..services.cache import cache
from ..workflow.graph import create_graph

logger = logging.getLogger(__name__)

# Global workflow instance
workflow = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global workflow
    logger.info("Starting TAG Backend...")
    # Initialize services here (DB, Redis, etc.)
    await cache.connect()
    workflow = create_graph()
    yield
    await cache.close()
    logger.info("Shutting down TAG Backend...")
