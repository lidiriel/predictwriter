"""Background prediction thread (plain Python, no ``uno``).

Context prediction can issue dozens of SQL queries, so it runs off the UI
thread. One worker thread owns the predictor (and so its SQLite connection,
which must stay in one thread). Only the latest request matters: a new
request replaces one that has not started yet.
"""

import logging
import threading
from typing import Any, Callable, List, Optional, Sequence, Tuple

from predictive.logsetup import LOGGER_NAME

log = logging.getLogger(LOGGER_NAME + ".worker")

Request = Tuple[Any, Sequence[str], str]  # (token, context words, prefix)


class PredictionWorker:
    """Runs ``predictor.predict(context, prefix)`` in a background thread."""

    def __init__(
        self,
        factory: Callable[[], Any],
        on_result: Callable[[Any, List[str]], None],
        name: str = "pw-predict",
    ) -> None:
        """
        Args:
            factory: Creates the predictor, in the worker thread (on first use).
            on_result: Called *in the worker thread* with ``(token, words)``.
        """
        self._factory = factory
        self._on_result = on_result
        self._cond = threading.Condition()
        self._request: Optional[Request] = None
        self._closed = False
        self.failed = False  # the predictor could not be created
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def submit(self, token: Any, context: Sequence[str], prefix: str) -> None:
        """Ask for a prediction; replaces any request not started yet."""
        with self._cond:
            self._request = (token, list(context), prefix)
            self._cond.notify()

    def close(self, timeout: float = 2.0) -> None:
        """Stop the thread (the current prediction, if any, completes first)."""
        with self._cond:
            self._closed = True
            self._request = None
            self._cond.notify()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout)

    def _run(self) -> None:
        predictor = None
        try:
            while True:
                with self._cond:
                    while self._request is None and not self._closed:
                        self._cond.wait()
                    if self._closed:
                        return
                    token, context, prefix = self._request
                    self._request = None
                if predictor is None and not self.failed:
                    try:
                        predictor = self._factory()
                    except Exception:
                        log.exception("cannot create the context predictor")
                        self.failed = True
                words: List[str] = []
                if predictor is not None:
                    try:
                        words = predictor.predict(context, prefix)
                    except Exception:
                        log.exception("context prediction failed")
                try:
                    self._on_result(token, words)
                except Exception:
                    log.exception("prediction result handler failed")
        finally:
            if predictor is not None and hasattr(predictor, "close"):
                try:
                    predictor.close()
                except Exception:
                    log.debug("predictor close failed", exc_info=True)
