"""Retry, backoff and rate-limit guards for calls that leave this machine (plan Phase 8).

Three rules, in order of importance:

1. **Only reads are retried.** Every helper here is for an idempotent call — fetch bars, fetch a quote,
   fetch news, ask a model. An order submission is NOT idempotent: a timeout may mean "the broker never
   saw it" or "the broker filled it and the reply was lost", and those are indistinguishable from here.
   Retrying the second case buys a position twice. `broker.orders.submit` is therefore deliberately not
   wrapped, and `guard_write` exists to make that refusal explicit rather than an omission somebody
   later "fixes".
2. **Only transient failures are retried.** A 429, a 5xx, a timeout or a dropped connection can succeed
   on the next attempt. A 400/401/403/404 cannot — retrying a bad key just burns the rate limit and
   delays the alert. `is_transient` is the whole policy and it is tested.
3. **The limiter is a promise the caller keeps, not one the provider makes.** Alpaca's free plan allows
   200 requests/minute and answers 429 above it; the token bucket spaces our own calls so we mostly
   never find out. Limits live in `app_settings.rate_limits` so they change without a deploy.

Sleeping is injected (`sleeper`) so the tests exercise real backoff arithmetic without real waiting.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

# Fallbacks used when app_settings has not been read (offline tests, first boot).
DEFAULT_LIMITS: dict[str, dict] = {
    "alpaca_data": {"per_minute": 190, "burst": 20, "max_attempts": 4, "base_delay_s": 0.5},
    "alpaca_trading": {"per_minute": 190, "burst": 10, "max_attempts": 3, "base_delay_s": 0.5},
    "openrouter": {"per_minute": 30, "burst": 5, "max_attempts": 3, "base_delay_s": 2.0},
    "ollama": {"per_minute": 600, "burst": 30, "max_attempts": 2, "base_delay_s": 1.0},
    "finnhub": {"per_minute": 55, "burst": 10, "max_attempts": 3, "base_delay_s": 1.0},
    "yahoo": {"per_minute": 60, "burst": 10, "max_attempts": 3, "base_delay_s": 1.0},
    "reddit": {"per_minute": 55, "burst": 10, "max_attempts": 3, "base_delay_s": 1.0},
    "fmp": {"per_minute": 55, "burst": 10, "max_attempts": 3, "base_delay_s": 1.0},
    "telegram": {"per_minute": 20, "burst": 5, "max_attempts": 3, "base_delay_s": 1.0},
    "supabase": {"per_minute": 600, "burst": 60, "max_attempts": 3, "base_delay_s": 0.3},
}
MAX_DELAY_S = 30.0

# HTTP statuses worth another attempt. 408 request timeout, 425 too early, 429 rate limited, 5xx.
TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504, 522, 524}
TRANSIENT_NAME_HINTS = (
    "timeout", "timedout", "connecterror", "connectionerror", "connectionreset", "readerror",
    "remotedisconnected", "remoteprotocolerror", "poolTimeout".lower(), "incompleteread", "oserror",
    "apierror",  # alpaca-py wraps HTTP failures in APIError; the status is read below
)


class RetriesExhausted(RuntimeError):
    """Every attempt failed. Carries the last error so the alert names the real cause."""

    def __init__(self, provider: str, attempts: int, last: BaseException):
        self.provider, self.attempts, self.last = provider, attempts, last
        super().__init__(f"{provider}: {attempts} attempts failed, last error {type(last).__name__}: {str(last)[:200]}")


class WriteNotRetryable(RuntimeError):
    """Raised by `guard_write` — a non-idempotent call must not be placed behind a retry."""


# --------------------------------------------------------------------------- transient classification

def status_of(exc: BaseException) -> int | None:
    """The HTTP status behind an exception, whatever library raised it."""
    for attr in ("status_code", "status", "code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int) and 100 <= v <= 599:
            return v
    resp = getattr(exc, "response", None)
    v = getattr(resp, "status_code", None)
    return v if isinstance(v, int) else None


def is_transient(exc: BaseException) -> bool:
    """True when another attempt could plausibly succeed.

    A wrong key, a malformed request or an unknown symbol is permanent: retrying it wastes the rate
    limit and delays the Telegram alert that tells the owner the real problem.
    """
    st = status_of(exc)
    if st is not None:
        return st in TRANSIENT_STATUS
    name = type(exc).__name__.lower()
    if any(h in name for h in TRANSIENT_NAME_HINTS):
        return True
    text = str(exc).lower()
    return any(h in text for h in ("timed out", "timeout", "connection reset", "temporarily unavailable",
                                   "too many requests", "rate limit", "connection aborted"))


def retry_after_seconds(exc: BaseException) -> float | None:
    """Honour a provider's own `Retry-After` when it sends one — our backoff is a guess, that is not."""
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None) or {}
    try:
        raw = headers.get("Retry-After") or headers.get("retry-after")
    except Exception:  # noqa: BLE001 — headers can be any mapping-ish object
        return None
    if raw is None:
        return None
    try:
        return max(0.0, min(float(raw), MAX_DELAY_S))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- rate limiting

@dataclass
class TokenBucket:
    """Classic token bucket: `burst` tokens available at once, refilled at `per_minute`/60 per second."""

    per_minute: float
    burst: float
    _tokens: float = field(default=0.0, init=False)
    # None until the first take: the clock is injectable, and seeding `_last` from time.monotonic() at
    # construction would make an injected clock read as hours of accumulated drift on its first call.
    _last: float | None = field(default=None, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._tokens = float(self.burst)

    def take(self, *, now: Callable[[], float] = time.monotonic) -> float:
        """Consume one token, returning the seconds the caller must wait first (0.0 when free)."""
        if self.per_minute <= 0:
            return 0.0
        with self._lock:
            t = now()
            if self._last is None:
                self._last = t
            self._tokens = min(float(self.burst), self._tokens + (t - self._last) * self.per_minute / 60.0)
            self._last = t
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return 0.0
            need = (1.0 - self._tokens) * 60.0 / self.per_minute
            self._tokens = 0.0
            self._last = t + need  # the wait is charged now, so concurrent callers queue instead of colliding
            return need


_buckets: dict[str, TokenBucket] = {}
_buckets_lock = threading.Lock()
_limits_cache: dict[str, dict] | None = None


def limits() -> dict[str, dict]:
    """`app_settings.rate_limits`, falling back to DEFAULT_LIMITS. Read once per process."""
    global _limits_cache
    if _limits_cache is not None:
        return _limits_cache
    merged = {k: dict(v) for k, v in DEFAULT_LIMITS.items()}
    try:
        from app.db import get_client

        row = get_client().table("app_settings").select("value").eq("key", "rate_limits").single().execute()
        for provider, cfg in (row.data.get("value") or {}).items():
            merged.setdefault(provider, {}).update(cfg or {})
    except Exception as e:  # noqa: BLE001 — defaults are safe; a DB outage must not disable retrying
        log.debug("rate_limits not read from app_settings (%s); using defaults", e)
    _limits_cache = merged
    return merged


def reset_limits_cache() -> None:
    """Forget the cached settings and buckets (used by tests and after editing `rate_limits`)."""
    global _limits_cache
    _limits_cache = None
    with _buckets_lock:
        _buckets.clear()


def config_for(provider: str) -> dict:
    cfg = dict(DEFAULT_LIMITS.get(provider, {"per_minute": 60, "burst": 10, "max_attempts": 3, "base_delay_s": 1.0}))
    cfg.update(limits().get(provider, {}))
    return cfg


def bucket_for(provider: str) -> TokenBucket:
    with _buckets_lock:
        b = _buckets.get(provider)
        if b is None:
            cfg = config_for(provider)
            b = _buckets[provider] = TokenBucket(per_minute=float(cfg.get("per_minute", 60)),
                                                 burst=float(cfg.get("burst", 10)))
        return b


# --------------------------------------------------------------------------- the retry itself

@dataclass(slots=True)
class Attempt:
    """What one attempt did — collected so a failure alert can say how hard we tried."""

    number: int
    error: str | None = None
    waited_s: float = 0.0
    transient: bool = True


def backoff_delay(attempt: int, base: float, *, jitter: Callable[[], float] = random.random) -> float:
    """Exponential with full jitter: `random(0, base * 2**(attempt-1))`, capped.

    Full jitter rather than a fixed multiplier because every retry in this system fires from the same
    cron minute — synchronised retries against one rate-limited provider are how a small outage becomes
    a 429 storm.
    """
    ceiling = min(base * (2 ** max(0, attempt - 1)), MAX_DELAY_S)
    return round(jitter() * ceiling, 3)


def call[T](provider: str, fn: Callable[[], T], *, what: str = "", max_attempts: int | None = None,
         sleeper: Callable[[float], None] = time.sleep,
         jitter: Callable[[], float] = random.random) -> T:
    """Run an idempotent call against `provider`, rate-limited and retried on transient failures.

    Raises `RetriesExhausted` when every attempt failed transiently, and re-raises the original
    exception immediately when it is permanent — a bad key should surface as a bad key.
    """
    cfg = config_for(provider)
    attempts = int(max_attempts if max_attempts is not None else cfg.get("max_attempts", 3))
    base = float(cfg.get("base_delay_s", 1.0))
    label = what or provider
    last: BaseException | None = None

    for n in range(1, max(1, attempts) + 1):
        wait = bucket_for(provider).take()
        if wait > 0:
            log.debug("%s: rate-limit pacing, waiting %.2fs", label, wait)
            sleeper(wait)
        try:
            return fn()
        except Exception as e:  # classified immediately below — permanent failures are re-raised
            last = e
            if not is_transient(e):
                log.info("%s: permanent failure, not retried (%s: %s)", label, type(e).__name__, str(e)[:160])
                raise
            if n >= attempts:
                break
            delay = retry_after_seconds(e)
            if delay is None:
                delay = backoff_delay(n, base, jitter=jitter)
            log.warning("%s: attempt %d/%d failed (%s); retrying in %.2fs", label, n, attempts,
                        str(e)[:160], delay)
            sleeper(delay)

    assert last is not None
    raise RetriesExhausted(label, attempts, last)


def call_preserving_error[T](provider: str, fn: Callable[[], T], **kw) -> T:
    """`call`, but the caller still sees the provider's OWN exception when every attempt fails.

    Modules like `app/llm/openrouter.py` translate an httpx error into a domain error — "credit
    exhausted", "rate limited", "the key was rejected" — and those messages are what reaches the owner.
    Wrapping the transport in `call()` would replace them with a generic RetriesExhausted and throw away
    the useful half. So the retrying happens here and the original error is re-raised unchanged.
    """
    try:
        return call(provider, fn, **kw)
    except RetriesExhausted as e:
        raise e.last from e


def guard_write(name: str) -> None:
    """State, in code, that a non-idempotent call must not be retried.

    Called at the top of the order-submission path. If somebody later wraps `submit` in `call()`, this
    is what stops it: a broker timeout is ambiguous, and the safe reading of an ambiguous order is
    'it may already exist'. Reconciliation (`/execute/orders/sync`) resolves it, a retry gambles on it.
    """
    raise WriteNotRetryable(
        f"{name} is not idempotent and is never auto-retried: a timeout cannot be distinguished from a "
        f"silent success, so a retry risks a duplicate order. Reconcile with /execute/orders/sync instead."
    )


def paced[T](provider: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator form for read helpers: `@paced("finnhub")`."""

    def deco(fn: Callable[..., T]) -> Callable[..., T]:
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return call(provider, lambda: fn(*args, **kwargs), what=f"{provider}.{fn.__name__}")

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
        return wrapper

    return deco
