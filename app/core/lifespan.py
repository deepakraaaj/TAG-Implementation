import asyncio
from contextlib import asynccontextmanager, nullcontext
import logging

from fastapi import FastAPI

from app.core.dependencies import get_container

logger = logging.getLogger(__name__)
workflow = None

# Cap so a hung/slow warmup never blocks the server from accepting traffic.
_WARMUP_TIMEOUT_SECONDS = 60


async def _warmup_workflow(container, compiled_workflow) -> None:
    """Run one throwaway greeting through the workflow.

    The first workflow invocation pays a large cold-start cost (graph compile,
    lazy imports, client init). Doing it here means the first real user request
    is fast instead of eating ~15s.
    """
    try:
        from langchain_core.messages import HumanMessage

        from app.domains.registry import DomainRegistry

        domain_name = ""
        app_id = ""
        try:
            app_id, app_config = container.app_registry.resolve_default()
            domain_name = str(getattr(app_config, "domain_name", "") or "")
        except Exception:
            pass

        inputs = {
            "messages": [HumanMessage(content="hello")],
            "metadata": {"warmup": True, "app_id": app_id or "", "domain_name": domain_name},
            "retry_count": 0,
        }
        domain_ctx = DomainRegistry.use_domain(domain_name) if domain_name else nullcontext()
        with domain_ctx:
            await asyncio.wait_for(
                compiled_workflow.ainvoke(inputs),
                timeout=_WARMUP_TIMEOUT_SECONDS,
            )
        logger.info("Workflow warmup complete (domain=%s).", domain_name or "default")
    except Exception as exc:  # noqa: BLE001 - warmup must never break startup
        logger.warning("Workflow warmup skipped/failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global workflow
    logger.info("Starting TAG Backend...")
    container = get_container()
    await container.startup()
    app.state.container = container
    workflow = container.get_workflow()
    readiness_snapshot = await container.readiness_snapshot()
    app.state.startup_readiness = readiness_snapshot
    logger.info(
        "Startup readiness status=%s ready=%s",
        readiness_snapshot.get("status"),
        readiness_snapshot.get("ready"),
    )
    await _warmup_workflow(container, workflow)
    yield
    await container.shutdown()
    workflow = None
    logger.info("Shutting down TAG Backend...")
