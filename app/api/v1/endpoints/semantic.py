from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from app.domains.registry import DomainRegistry

router = APIRouter(prefix="/semantic")


def _semantic_retriever_from_request(request: Request):
    container = getattr(request.app.state, "container", None)
    retriever = getattr(container, "semantic_retriever", None)
    if retriever is None:
        raise HTTPException(status_code=503, detail="Semantic retriever is not available.")
    return retriever


@router.post("/reindex")
async def reindex_semantic_bundle(
    request: Request,
    domain: Annotated[str | None, Query(description="Optional TAG domain name.")] = None,
):
    retriever = _semantic_retriever_from_request(request)
    target_domain = str(domain or "").strip()
    if target_domain:
        with DomainRegistry.use_domain(target_domain) as active_domain:
            indexed_chunks = int(retriever.reindex() or 0)
            resolved_domain = active_domain.name
    else:
        active_domain = DomainRegistry.get_current_domain()
        indexed_chunks = int(retriever.reindex() or 0)
        resolved_domain = active_domain.name

    return {
        "status": "ok",
        "domain": resolved_domain,
        "indexed_chunks": indexed_chunks,
    }


@router.get("/search")
async def search_semantic_bundle(
    request: Request,
    query: Annotated[str, Query(min_length=1, description="Semantic search query.")] = "",
    domain: Annotated[str | None, Query(description="Optional TAG domain name.")] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 6,
):
    retriever = _semantic_retriever_from_request(request)
    target_domain = str(domain or "").strip()
    if target_domain:
        with DomainRegistry.use_domain(target_domain) as active_domain:
            hits = retriever.search(query, limit=limit)
            resolved_domain = active_domain.name
    else:
        active_domain = DomainRegistry.get_current_domain()
        hits = retriever.search(query, limit=limit)
        resolved_domain = active_domain.name

    return {
        "status": "ok",
        "domain": resolved_domain,
        "query": query,
        "hits": hits,
    }
