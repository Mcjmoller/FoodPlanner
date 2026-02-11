"""
API Utilities for FoodPlanner
Provides rate limiting, exponential backoff, caching, and verbose debugging utilities.
"""
import time
import random
import functools
import json
import os
import logging
from datetime import datetime
from typing import Any, Callable, Optional
from collections import OrderedDict

# --- LOGGING SETUP ---
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('FoodPlanner')

# Reduce noise from external libraries
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)


def log_stage(stage_name: str):
    """Log the start of a pipeline stage."""
    logger.info(f"{'='*50}")
    logger.info(f"STAGE: {stage_name}")
    logger.info(f"{'='*50}")


def log_payload(payload: str, max_chars: int = 500):
    """Log the payload being sent to an API."""
    truncated = payload[:max_chars] + "..." if len(payload) > max_chars else payload
    logger.debug(f"PAYLOAD ({len(payload)} chars):\n{truncated}")


def log_response(response_text: str, elapsed_time: float, max_chars: int = 300):
    """Log an API response with timing."""
    truncated = response_text[:max_chars] + "..." if len(response_text) > max_chars else response_text
    logger.info(f"RESPONSE ({len(response_text)} chars, {elapsed_time:.2f}s):\n{truncated}")


def timed(func: Callable) -> Callable:
    """Decorator to log execution time of a function."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        logger.debug(f"START: {func.__name__}")
        try:
            result = func(*args, **kwargs)
            elapsed = time.time() - start
            logger.info(f"COMPLETED: {func.__name__} in {elapsed:.2f}s")
            return result
        except Exception as e:
            elapsed = time.time() - start
            logger.error(f"FAILED: {func.__name__} after {elapsed:.2f}s - {e}")
            raise
    return wrapper


def exponential_backoff_with_jitter(attempt: int, base_delay: float = 2.0, max_delay: float = 64.0) -> float:
    """
    Calculate delay with exponential backoff and jitter.
    
    Args:
        attempt: Current retry attempt (0-indexed)
        base_delay: Base delay in seconds
        max_delay: Maximum delay cap in seconds
    
    Returns:
        Delay in seconds with random jitter (±25%)
    """
    delay = min(base_delay * (2 ** attempt), max_delay)
    jitter = delay * random.uniform(-0.25, 0.25)
    return delay + jitter


def retry_on_rate_limit(
    max_retries: int = 10,
    base_delay: float = 2.0,
    max_delay: float = 120.0,
    rate_limit_codes: tuple = ("429", "RESOURCE_EXHAUSTED", "quota", "rate"),
    fatal_patterns: tuple = ("limit: 0", "quota exceeded"),
):
    """
    Decorator to retry API calls on rate limit errors with verbose logging.
    Supports Circuit Breaking on specific fatal error patterns.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(max_retries):
                start_time = time.time()
                try:
                    result = func(*args, **kwargs)
                    elapsed = time.time() - start_time
                    logger.info(f"✅ API call succeeded in {elapsed:.2f}s")
                    return result
                except Exception as e:
                    elapsed = time.time() - start_time
                    error_str = str(e).lower()
                    
                    # --- CIRCUIT BREAKER ---
                    for pattern in fatal_patterns:
                        if pattern in error_str:
                            logger.critical(f"⛔ CIRCUIT BREAKER TRIGGERED: Detected '{pattern}' in error.")
                            logger.critical(f"   Aborting retries to save quota.")
                            raise e

                    is_rate_limit = any(code.lower() in error_str for code in rate_limit_codes)
                    
                    if not is_rate_limit:
                        logger.error(f"❌ Non-recoverable error after {elapsed:.2f}s: {e}")
                        raise
                    
                    last_exception = e
                    
                    if attempt < max_retries - 1:
                        # Extract Retry-After if available
                        retry_after = "Unknown"
                        # Try standard attribute locations for google-genai or http errors
                        if hasattr(e, 'headers') and 'retry-after' in e.headers:
                            retry_after = f"{e.headers['retry-after']}s"
                        
                        delay = exponential_backoff_with_jitter(attempt, base_delay, max_delay)
                        logger.warning(f"⚠️  RATE LIMIT HIT (attempt {attempt + 1}/{max_retries})")
                        logger.warning(f"   Error: {str(e)[:150]}...")
                        logger.warning(f"   Server requested wait: {retry_after}")
                        logger.warning(f"   Backing off for {delay:.1f}s...")
                        time.sleep(delay)
                    else:
                        logger.error(f"❌ Max retries ({max_retries}) exhausted")
            
            raise last_exception
        
        return wrapper
    return decorator


class RateLimiter:
    """Token bucket rate limiter for throttling requests."""
    
    def __init__(self, requests_per_window: int = 10, window_seconds: float = 60.0):
        self.requests_per_window = requests_per_window
        self.window_seconds = window_seconds
        self.tokens = requests_per_window
        self.last_refill = time.time()
    
    def _refill(self):
        now = time.time()
        elapsed = now - self.last_refill
        tokens_to_add = (elapsed / self.window_seconds) * self.requests_per_window
        self.tokens = min(self.requests_per_window, self.tokens + tokens_to_add)
        self.last_refill = now
    
    def acquire(self, block: bool = False, timeout: float = 30.0) -> bool:
        start = time.time()
        
        while True:
            self._refill()
            
            if self.tokens >= 1:
                self.tokens -= 1
                logger.debug(f"RateLimiter: Token acquired ({self.tokens:.1f} remaining)")
                return True
            
            if not block:
                return False
            
            if time.time() - start >= timeout:
                return False
            
            time.sleep(0.1)
    
    def wait(self):
        self.acquire(block=True)


class SimpleCache:
    """Simple in-memory cache with TTL."""
    
    def __init__(self, ttl_seconds: int = 3600, max_size: int = 100):
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
    
    def _cleanup_expired(self):
        now = time.time()
        expired_keys = [
            key for key, (_, timestamp) in self._cache.items()
            if now - timestamp > self.ttl_seconds
        ]
        for key in expired_keys:
            del self._cache[key]
    
    def _enforce_size_limit(self):
        while len(self._cache) > self.max_size:
            self._cache.popitem(last=False)
    
    def get(self, key: str) -> Optional[Any]:
        self._cleanup_expired()
        
        if key in self._cache:
            value, timestamp = self._cache[key]
            if time.time() - timestamp <= self.ttl_seconds:
                self._cache.move_to_end(key)
                logger.debug(f"CACHE HIT: {key[:30]}...")
                return value
            else:
                del self._cache[key]
        
        logger.debug(f"CACHE MISS: {key[:30]}...")
        return None
    
    def set(self, key: str, value: Any):
        self._cache[key] = (value, time.time())
        self._cache.move_to_end(key)
        self._enforce_size_limit()
        logger.debug(f"CACHE SET: {key[:30]}... ({len(self._cache)} entries)")
    
    def clear(self):
        self._cache.clear()


class ProgressSaver:
    """Save intermediate progress to allow resumption after fatal errors."""
    
    def __init__(self, save_path: str = "pipeline_progress.json"):
        self.save_path = save_path
        self.progress = {
            "last_updated": None,
            "stage": None,
            "scraped_data": None,
            "lists": None,
            "ai_response": None,
            "email_sent": False
        }
    
    def update(self, stage: str, **data):
        """Update progress and save to disk."""
        self.progress["last_updated"] = datetime.now().isoformat()
        self.progress["stage"] = stage
        self.progress.update(data)
        
        try:
            with open(self.save_path, "w", encoding="utf-8") as f:
                json.dump(self.progress, f, indent=2, ensure_ascii=False)
            logger.debug(f"Progress saved: stage={stage}")
        except Exception as e:
            logger.warning(f"Failed to save progress: {e}")
    
    def load(self) -> dict:
        """Load previous progress if available."""
        if os.path.exists(self.save_path):
            try:
                with open(self.save_path, "r", encoding="utf-8") as f:
                    self.progress = json.load(f)
                logger.info(f"Loaded previous progress from stage: {self.progress.get('stage')}")
                return self.progress
            except Exception as e:
                logger.warning(f"Failed to load progress: {e}")
        return {}
    
    def clear(self):
        """Clear saved progress after successful completion."""
        if os.path.exists(self.save_path):
            os.remove(self.save_path)
            logger.debug("Progress file cleared")



def process_in_batches(
    items: list, 
    batch_size: int, 
    process_func: Callable[[list], Any],
    delay_between_batches: float = 1.0
):
    """
    Process a list of items in batches using a provided processor function.
    
    Args:
        items: List of items to process
        batch_size: Number of items per batch
        process_func: Function to call for each batch (must accept a list)
        delay_between_batches: Sleep time between batches
    """
    total = len(items)
    for i in range(0, total, batch_size):
        batch = items[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (total + batch_size - 1) // batch_size
        
        logger.info(f"🔄 Processing Batch {batch_num}/{total_batches} ({len(batch)} items)")
        
        try:
            # Call the processor function with the batch
            process_func(batch)
            logger.info(f"✅ Batch {batch_num} completed")
        except Exception as e:
            logger.error(f"❌ Batch {batch_num} failed: {e}")
            raise e
            
        if i + batch_size < total:
            time.sleep(delay_between_batches)

# Pre-configured instances
gemini_rate_limiter = RateLimiter(requests_per_window=2, window_seconds=60.0)  # Free Tier: 2 RPM
sheets_rate_limiter = RateLimiter(requests_per_window=60, window_seconds=60.0)
response_cache = SimpleCache(ttl_seconds=3600, max_size=50)
progress_saver = ProgressSaver()

