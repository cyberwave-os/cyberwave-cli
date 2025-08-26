"""Retry utilities for robust operations."""

import asyncio
import logging
import random
import time
from functools import wraps
from typing import Any, Callable, Optional, Type, Union

logger = logging.getLogger(__name__)


class RetryStrategy:
    """Base class for retry strategies."""
    
    def __init__(self, max_attempts: int = 3):
        self.max_attempts = max_attempts
    
    def get_delay(self, attempt: int) -> float:
        """Get delay for the given attempt number."""
        raise NotImplementedError


class ExponentialBackoff(RetryStrategy):
    """Exponential backoff retry strategy."""
    
    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        jitter: bool = True
    ):
        super().__init__(max_attempts)
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter
    
    def get_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay."""
        delay = self.base_delay * (2 ** attempt)
        delay = min(delay, self.max_delay)
        
        if self.jitter:
            # Add random jitter to avoid thundering herd
            jitter_range = delay * 0.1
            delay += random.uniform(-jitter_range, jitter_range)
        
        return max(0, delay)


class LinearBackoff(RetryStrategy):
    """Linear backoff retry strategy."""
    
    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        increment: float = 1.0
    ):
        super().__init__(max_attempts)
        self.base_delay = base_delay
        self.increment = increment
    
    def get_delay(self, attempt: int) -> float:
        """Calculate linear backoff delay."""
        return self.base_delay + (attempt * self.increment)


def with_retry(
    strategy: RetryStrategy,
    exceptions: Union[Type[Exception], tuple] = Exception,
    on_retry: Optional[Callable[[int, Exception], None]] = None
):
    """
    Decorator that adds retry logic to async functions.
    
    Args:
        strategy: Retry strategy to use
        exceptions: Exception types to retry on
        on_retry: Optional callback called on each retry
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            
            for attempt in range(strategy.max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == strategy.max_attempts - 1:
                        # Last attempt, re-raise the exception
                        break
                    
                    delay = strategy.get_delay(attempt)
                    
                    if on_retry:
                        on_retry(attempt + 1, e)
                    
                    logger.warning(
                        f"Attempt {attempt + 1}/{strategy.max_attempts} failed for {func.__name__}: {e}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    
                    await asyncio.sleep(delay)
            
            # If we get here, all attempts failed
            logger.error(f"All {strategy.max_attempts} attempts failed for {func.__name__}")
            raise last_exception
        
        return wrapper
    return decorator


def retry_sync(
    strategy: RetryStrategy,
    exceptions: Union[Type[Exception], tuple] = Exception,
    on_retry: Optional[Callable[[int, Exception], None]] = None
):
    """
    Decorator that adds retry logic to synchronous functions.
    
    Args:
        strategy: Retry strategy to use
        exceptions: Exception types to retry on
        on_retry: Optional callback called on each retry
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            
            for attempt in range(strategy.max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == strategy.max_attempts - 1:
                        # Last attempt, re-raise the exception
                        break
                    
                    delay = strategy.get_delay(attempt)
                    
                    if on_retry:
                        on_retry(attempt + 1, e)
                    
                    logger.warning(
                        f"Attempt {attempt + 1}/{strategy.max_attempts} failed for {func.__name__}: {e}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    
                    time.sleep(delay)
            
            # If we get here, all attempts failed
            logger.error(f"All {strategy.max_attempts} attempts failed for {func.__name__}")
            raise last_exception
        
        return wrapper
    return decorator


# Convenience functions

def simple_retry(max_attempts: int = 3, delay: float = 1.0):
    """Simple retry decorator with fixed delay."""
    strategy = LinearBackoff(max_attempts=max_attempts, base_delay=delay, increment=0)
    return with_retry(strategy)


def exponential_retry(max_attempts: int = 3, base_delay: float = 1.0):
    """Simple exponential backoff retry decorator."""
    strategy = ExponentialBackoff(max_attempts=max_attempts, base_delay=base_delay)
    return with_retry(strategy)
