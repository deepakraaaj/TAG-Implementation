import asyncio
import logging
from typing import Any, Callable, Optional


logger = logging.getLogger(__name__)


async def ainvoke_with_retry(
    llm: Any,
    prompt: str,
    *,
    max_tokens: Optional[int] = None,
    attempts: int = 3,
    backoff_seconds: float = 0.35,
    validator: Optional[Callable[[Any], bool]] = None,
    task_name: str = "llm_call",
) -> Any:
    """
    Retry wrapper for LLM async invocations.
    - Retries on exceptions.
    - Retries on invalid responses if a validator is provided.
    """
    if attempts < 1:
        attempts = 1

    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            if max_tokens is None:
                response = await llm.ainvoke(prompt)
            else:
                response = await llm.ainvoke(prompt, max_tokens=max_tokens)

            if validator is not None and not validator(response):
                raise ValueError(f"{task_name} produced invalid response on attempt {attempt}")

            return response
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= attempts:
                break
            sleep_time = backoff_seconds * attempt
            logger.warning(
                "%s failed attempt %s/%s: %s. Retrying in %.2fs",
                task_name,
                attempt,
                attempts,
                exc,
                sleep_time,
            )
            await asyncio.sleep(sleep_time)

    if last_error:
        raise last_error
    raise RuntimeError(f"{task_name} failed with unknown error")

