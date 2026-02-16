"""Per-request user context using contextvars.

FastMCP's stateless-http mode may run tool handlers in an async context
that does not inherit the ContextVar set by ASGI middleware.  As a
fallback we store tokens in a per-task dictionary keyed by asyncio task
ID, which is safe for concurrent async requests (unlike a single
module-level variable which suffers from race conditions).
"""

import asyncio
import logging
from contextvars import ContextVar
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Primary: ContextVar (works when context propagates correctly)
current_access_token: ContextVar[Optional[str]] = ContextVar("current_access_token", default=None)

# Fallback: per-task dictionary for when ContextVar doesn't propagate.
# Keyed by id(asyncio.current_task()) to avoid cross-request contamination.
_task_tokens: Dict[int, str] = {}


def _current_task_id() -> Optional[int]:
    """Return the id of the current asyncio task, or None."""
    task = asyncio.current_task()
    return id(task) if task is not None else None


def get_access_token() -> Optional[str]:
    """Return the current access token, trying ContextVar first then per-task fallback."""
    cv_val = current_access_token.get()
    if cv_val:
        logger.info("get_access_token: resolved from contextvar")
        return cv_val

    task_id = _current_task_id()
    fb_val = _task_tokens.get(task_id) if task_id is not None else None
    logger.info(
        f"get_access_token: contextvar=None, "
        f"task_fallback={'set' if fb_val else 'None'} (task_id={task_id})"
    )
    return fb_val


def set_access_token(token: Optional[str]):
    """Set the access token in both ContextVar and per-task fallback."""
    current_access_token.set(token)
    task_id = _current_task_id()
    if task_id is not None and token is not None:
        _task_tokens[task_id] = token
    logger.info(f"set_access_token: token={'set' if token else 'None'} (task_id={task_id})")


def reset_access_token(cv_token=None):
    """Reset the access token in both ContextVar and per-task fallback."""
    if cv_token is not None:
        current_access_token.reset(cv_token)
    task_id = _current_task_id()
    if task_id is not None:
        _task_tokens.pop(task_id, None)
    logger.info(f"reset_access_token: cleared (task_id={task_id})")
