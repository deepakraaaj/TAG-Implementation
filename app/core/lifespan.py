from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI

from app.core.dependencies import get_container

logger = logging.getLogger(__name__)
workflow = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global workflow
    logger.info("Starting TAG Backend...")
    container = get_container()
    await container.startup()
    app.state.container = container
    workflow = container.get_workflow()
    yield
    await container.shutdown()
    workflow = None
    logger.info("Shutting down TAG Backend...")
