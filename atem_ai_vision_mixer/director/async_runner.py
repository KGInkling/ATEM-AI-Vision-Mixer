"""Run one model request off the control thread without queuing later ticks."""

import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from math import isfinite
from time import monotonic
from typing import Generic, TypeVar

Result = TypeVar("Result")
logger = logging.getLogger(__name__)


def _run_request(request: Callable[[], Result]) -> tuple[float, Result]:
    result = request()
    # A later poll must distinguish timely completion from an expired response.
    return monotonic(), result


class AsyncRunner(Generic[Result]):
    """Retain the last timely result while a single background request runs.

    Call tick and close from the control thread. Request functions must validate
    their output and configure their own transport timeout; a running Python
    thread cannot be forcibly cancelled.
    """

    def __init__(self, *, initial_result: Result, timeout_seconds: float) -> None:
        """Start with a caller-provided result and a finite positive deadline."""
        if not isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")

        self._timeout_seconds = timeout_seconds
        self._last_result = initial_result
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="director"
        )
        self._future: Future[tuple[float, Result]] | None = None
        self._started_at = 0.0
        self._deadline_reported = False
        self._closed = False

    def tick(self, request: Callable[[], Result]) -> Result:
        """Return the last result, starting a request only when the worker is idle."""
        if self._closed:
            raise RuntimeError("AsyncRunner is closed")

        if self._future is not None:
            if not self._future.done():
                if monotonic() - self._started_at >= self._timeout_seconds:
                    self._report_timeout()
                # Keep the occupied slot even after expiry: never queue a retry.
                return self._last_result

            self._consume_result(self._future)
            self._future = None

        self._started_at = monotonic()
        self._deadline_reported = False
        self._future = self._executor.submit(_run_request, request)
        return self._last_result

    def _consume_result(self, future: Future[tuple[float, Result]]) -> None:
        error = future.exception()
        if error is not None:
            # Model/transport errors can echo private payloads; log only the type.
            logger.warning(
                "Director request failed (%s); previous result retained.",
                type(error).__name__,
            )
            return

        completed_at, result = future.result()
        if completed_at - self._started_at < self._timeout_seconds:
            self._last_result = result
        else:
            self._report_timeout()

    def _report_timeout(self) -> None:
        if self._deadline_reported:
            return
        self._deadline_reported = True
        logger.warning("Director request deadline reached; previous result retained.")

    def close(self) -> None:
        """Stop accepting requests without waiting for an occupied worker."""
        if self._closed:
            return
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
