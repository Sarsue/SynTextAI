"""
Utilities for making operations more deterministic and reliable.
"""
import hashlib
import logging
from typing import Any, Dict, List, Optional, TypeVar, Callable, Type, Tuple
from functools import wraps
import json
import re

logger = logging.getLogger(__name__)

T = TypeVar('T')

def stable_hash(data: Any) -> str:
    """Generate a stable hash for any JSON-serializable data structure."""
    if isinstance(data, (str, int, float, bool, type(None))):
        data_str = str(data)
    else:
        # Convert to a consistent JSON string
        data_str = json.dumps(data, sort_keys=True, ensure_ascii=False)
    
    return hashlib.md5(data_str.encode('utf-8')).hexdigest()

def deterministic_cache(max_size: int = 1000):
    """Simple in-memory cache decorator with stable hashing."""
    cache: Dict[str, Any] = {}
    cache_keys = []  # For LRU eviction
    
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            # Create a stable cache key
            key_parts = [
                func.__module__,
                func.__name__,
                stable_hash(args),
                stable_hash(kwargs)
            ]
            cache_key = stable_hash(key_parts)
            
            # Check cache
            if cache_key in cache:
                logger.debug(f"Cache hit for {func.__name__}")
                return cache[cache_key]
                
            # Call the function
            result = await func(*args, **kwargs)
            
            # Cache the result
            if cache_key not in cache:
                if len(cache_keys) >= max_size:
                    # Evict the least recently used item
                    oldest_key = cache_keys.pop(0)
                    cache.pop(oldest_key, None)
                
                cache[cache_key] = result
                cache_keys.append(cache_key)
            
            return result
            
        return wrapper
    return decorator

def validate_and_clean_text(text: str, min_length: int = 10, max_length: int = 10000) -> Optional[str]:
    """Validate and clean text input with length constraints."""
    if not text or not isinstance(text, str):
        return None
        
    # Remove control characters except newlines and tabs
    cleaned = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    
    # Normalize whitespace but preserve paragraph breaks
    cleaned = '\n'.join(
        ' '.join(line.split())
        for line in cleaned.split('\n')
        if line.strip()
    )
    
    # Check length constraints
    if len(cleaned) < min_length or len(cleaned) > max_length:
        return None
        
    return cleaned

def batch_items(items: List[T], batch_size: int = 10) -> List[List[T]]:
    """Split items into batches of specified size."""
    return [items[i:i + batch_size] for i in range(0, len(items), batch_size)]

def safe_json_parse(json_str: str, expected_type: Type[T] = list) -> Tuple[Optional[T], Optional[str]]:
    """Safely parse JSON with multiple fallback strategies."""
    if not json_str or not isinstance(json_str, str):
        return None, "Empty or invalid input"
    
    # Try direct parse first
    try:
        parsed = json.loads(json_str)
        if isinstance(parsed, expected_type):
            return parsed, None
    except json.JSONDecodeError:
        pass
    
    # Try to fix common JSON issues
    repair_attempts = [
        # Fix trailing commas
        lambda s: json.loads(re.sub(r',\s*([}\]])', r'\1', s)),
        # Fix missing quotes around keys
        lambda s: json.loads(re.sub(r'([{,]\s*)([a-zA-Z0-9_]+)(\s*:)'
                                  r'(?=(?:[^"]*"[^"]*")*[^"]*$)', 
                                  r'\1"\2"\3', s)),
    ]
    
    for attempt in repair_attempts:
        try:
            parsed = attempt(json_str)
            if isinstance(parsed, expected_type):
                logger.warning("Repaired malformed JSON")
                return parsed, None
        except (json.JSONDecodeError, AttributeError):
            continue
    
    return None, f"Failed to parse as {expected_type.__name__}"
