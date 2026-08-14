"""HTTP failure diagnostics for the scrapers.

The scrapers deliberately swallow HTTP errors (returning None) so a single
bad provider cannot crash the resolution chain. The side effect is that a
frozen-build "all providers fail" becomes impossible to diagnose — every
failure looks like ``stream_url: null`` / ``exception: none``.

This module adds cheap, structured logging at the HTTP layer:

* ``response_hook`` — an httpx event hook that logs any non-2xx response with
  its status code and a body snippet;
* ``log_http_error`` — to be called from each ``except`` in a scraper so
  transport/SSL errors (the usual PyInstaller suspects) are surfaced too.

Lines go to ``stderr`` and are appended to ``debug_streams.log`` in the
current working directory.
"""
import os
import sys
import time

import httpx
import requests

_LOG_PATH = os.path.join(os.getcwd(), "debug_streams.log")


def _emit(line: str) -> None:
    try:
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
    except Exception:
        pass
    try:
        with open(_LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def log_timing(label: str, seconds: float) -> None:
    """Emit a performance marker: ``[TIMING] <label> <milliseconds>ms``."""
    _emit(f"[TIMING] {label} {seconds * 1000:.0f}ms")


class timed:
    """Context manager that logs elapsed wall-clock time under a label.

    Example::

        with timed("miruro:pipe:goto"):
            page.goto(...)
    """

    def __init__(self, label: str):
        self._label = label

    def __enter__(self):
        self._t0 = time.monotonic()
        return self

    def __exit__(self, *_exc):
        log_timing(self._label, time.monotonic() - self._t0)
        return False


def response_hook(response) -> None:
    """Attach with ``httpx.Client(..., event_hooks={"response": [response_hook]})``."""
    if response is None:
        return
    status = getattr(response, "status_code", None)
    if status is None or status < 400:
        return
    body = ""
    try:
        body = (response.text or "")[:160].replace("\n", " ")
    except Exception:
        pass
    req = getattr(response, "request", None)
    method = req.method if req is not None else "?"
    _emit(f"[HTTP {status}] {method} {getattr(response, 'url', '')} body={body!r}")


def log_http_error(provider: str, method: str, url: str,
                   exc: BaseException = None, note: str = "") -> None:
    exc_txt = f"{type(exc).__name__}: {exc}" if exc is not None else "no exception"
    _emit(f"[HTTP ERROR] provider={provider} {method} {url} note={note} exc={exc_txt}")


class LoggingClient(httpx.Client):
    """``httpx.Client`` that logs HTTP failures for frozen-build diagnosis.

    Drop-in replacement for the scrapers' plain clients:

    * every non-2xx response is logged (status + body snippet) via the
      ``response`` event hook;
    * every transport/SSL/connection exception that reaches ``send()`` is
      logged with its type and message before being re-raised, so an
      ``except Exception: return None`` in a scraper no longer hides *why* a
      provider came back empty.
    """

    def __init__(self, provider: str, *args, **kwargs):
        hooks = dict(kwargs.pop("event_hooks", None) or {})
        hooks.setdefault("response", []).append(response_hook)
        kwargs["event_hooks"] = hooks
        self._provider = provider
        super().__init__(*args, **kwargs)

    def send(self, request, *args, **kwargs):
        try:
            return super().send(request, *args, **kwargs)
        except Exception as exc:
            log_http_error(self._provider, request.method, str(request.url), exc)
            raise


class LoggingRequestsSession(requests.Session):
    """``requests.Session`` equivalent of :class:`LoggingClient` (for mkissa)."""

    def __init__(self, provider: str, *args, **kwargs):
        self._provider = provider
        super().__init__(*args, **kwargs)

    def request(self, method, url, *args, **kwargs):
        try:
            resp = super().request(method, url, *args, **kwargs)
        except Exception as exc:
            log_http_error(self._provider, method, str(url), exc)
            raise
        if resp is not None and getattr(resp, "status_code", 200) >= 400:
            body = ""
            try:
                body = resp.text[:160].replace("\n", " ")
            except Exception:
                pass
            _emit(f"[HTTP {resp.status_code}] {method} {url} body={body!r}")
        return resp
