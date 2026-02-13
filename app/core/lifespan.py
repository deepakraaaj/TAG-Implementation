from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI

from app.services.cache import cache
from app.assistant.orchestration.graph import create_graph

logger = logging.getLogger(__name__)

workflow = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global workflow
    logger.info("Starting TAG Backend...")
    await cache.connect()
    workflow = create_graph()
    yield
    await cache.close()
    logger.info("Shutting down TAG Backend...")
