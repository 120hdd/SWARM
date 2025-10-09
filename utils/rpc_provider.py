from typing import List, Optional, Tuple
import threading
import time

from web3 import HTTPProvider


class RotatingHTTPProvider(HTTPProvider):
    """
    HTTP provider that rotates between multiple RPC URLs when rate-limited
    or on connection errors. Attempts each URL in order and advances on
    failures. Thread-safe for simple usage.
    """

    def __init__(self, rpc_urls: List[str], request_kwargs: Optional[dict] = None):
        if not rpc_urls:
            raise ValueError("rpc_urls must be a non-empty list")
        # Initialize with the first URL for superclass
        super().__init__(endpoint_uri=rpc_urls[0], request_kwargs=request_kwargs)
        self._urls: List[str] = list(dict.fromkeys([u.strip() for u in rpc_urls if u and u.strip()]))
        self._idx: int = 0
        self._lock = threading.Lock()

    @property
    def current_url(self) -> str:
        with self._lock:
            return self._urls[self._idx]

    def _advance(self) -> None:
        with self._lock:
            self._idx = (self._idx + 1) % len(self._urls)
            # update underlying provider endpoint
            self.endpoint_uri = self._urls[self._idx]

    def _should_rotate_on_error(self, error_obj: dict) -> bool:
        # Common rate limit indicators across providers
        if not error_obj:
            return False
        msg = str(error_obj.get("message", "")).lower()
        code = error_obj.get("code")
        # Heuristics: HTTP 429-like, or provider-specific rate messages
        rate_tokens = [
            "rate limit", "too many requests", "daily request count exceeded",
            "exceeded", "request limit", "over capacity",
            "project id request rate exceeded",
        ]
        if any(tok in msg for tok in rate_tokens):
            return True
        # Some providers use generic internal error codes for throttling
        if code in (-32005, -32000, 429):
            return True
        return False

    def make_request(self, method: str, params: list):  # type: ignore[override]
        attempts = 0
        last_exc: Optional[BaseException] = None
        last_error_resp: Optional[dict] = None
        total = len(self._urls)

        while attempts < total:
            try:
                response = super().make_request(method, params)
                # If JSON-RPC error indicates rate limit, rotate and retry
                if isinstance(response, dict) and "error" in response and self._should_rotate_on_error(response["error"]):
                    self._advance()
                    attempts += 1
                    last_error_resp = response
                    # tiny backoff to avoid hot-spinning
                    time.sleep(0.1)
                    continue
                return response
            except Exception as e:  # Connection errors, timeouts, etc.
                last_exc = e
                self._advance()
                attempts += 1
                time.sleep(0.1)

        # Exhausted all URLs: raise last exception if available, otherwise return last error
        if last_exc is not None:
            raise last_exc
        # Fall back to last error response
        return last_error_resp if last_error_resp is not None else {"error": {"code": 429, "message": "All RPC URLs rate limited or failed"}}

