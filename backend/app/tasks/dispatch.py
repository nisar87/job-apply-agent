"""Run a background job now (inline) or hand it to a Celery worker."""

from collections.abc import Callable

from app.config import get_settings


def enqueue(task: Callable, *args, **kwargs):
    """`task` is a Celery task. Inline mode calls its function directly, which keeps
    development and tests free of a running worker."""
    if get_settings().tasks_inline:
        return task.run(*args, **kwargs) if hasattr(task, "run") else task(*args, **kwargs)
    return task.delay(*args, **kwargs)
