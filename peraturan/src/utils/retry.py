"""Retry logic with exponential backoff for Peraturan Crawler."""

import asyncio
import functools
from typing import Callable, TypeVar, Any
from collections.abc import Awaitable

from src.utils.logging import get_logger

logger = get_logger("retry")

T = TypeVar("T")


class RetryError(Exception):
    """Raised when all retry attempts have been exhausted."""

    def __init__(self, message: str, last_exception: Exception | None = None):
        super().__init__(message)
        self.last_exception = last_exception


async def retry_with_backoff(
    func: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int = 3,
    backoff_factor: float = 2.0,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    **kwargs: Any,
) -> T:
    """Execute an async function with exponential backoff retry.

    Args:
        func: Async function to execute
        *args: Positional arguments for func
        max_retries: Maximum number of retry attempts
        backoff_factor: Multiplier for delay after each retry
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay between retries
        retryable_exceptions: Tuple of exception types to retry on
        **kwargs: Keyword arguments for func

    Returns:
        Result of the function call

    Raises:
        RetryError: When all retries are exhausted
    """
    last_exception: Exception | None = None
    delay = initial_delay

    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except retryable_exceptions as e:
            last_exception = e

            if attempt == max_retries:
                logger.error(
                    f"All {max_retries} retries exhausted for {func.__name__}: {e}"
                )
                raise RetryError(
                    f"Failed after {max_retries} retries: {e}",
                    last_exception=e,
                ) from e

            logger.warning(
                f"Attempt {attempt + 1}/{max_retries + 1} failed for {func.__name__}: {e}. "
                f"Retrying in {delay:.1f}s..."
            )
            await asyncio.sleep(delay)
            delay = min(delay * backoff_factor, max_delay)

    # Should never reach here, but for type safety
    raise RetryError("Unexpected retry loop exit", last_exception=last_exception)


def with_retry(
    max_retries: int = 3,
    backoff_factor: float = 2.0,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator for adding retry logic to async functions.

    Args:
        max_retries: Maximum number of retry attempts
        backoff_factor: Multiplier for delay after each retry
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay between retries
        retryable_exceptions: Tuple of exception types to retry on

    Returns:
        Decorator function
    """
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            return await retry_with_backoff(
                func,
                *args,
                max_retries=max_retries,
                backoff_factor=backoff_factor,
                initial_delay=initial_delay,
                max_delay=max_delay,
                retryable_exceptions=retryable_exceptions,
                **kwargs,
            )
        return wrapper
    return decorator
