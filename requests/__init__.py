"""Lightweight fallback implementation of the :mod:`requests` package.

The real `requests` distribution is not available in this execution
environment.  To keep the codebase working we provide a tiny subset of the API
implemented on top of the Python standard library.  When the genuine package is
installed the shim dynamically loads it to preserve full compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.machinery
import importlib.util
import json
import logging
import re
import socket
import sys
import sysconfig
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

LOGGER = logging.getLogger(__name__)
_MODULE_NAME = __name__
_MODULE_PATH = Path(__file__).resolve()


def _try_import_real_requests() -> Optional[object]:
    """Try to import the real :mod:`requests` package from site-packages."""

    candidates: list[str] = []
    try:  # pragma: no cover - defensive, site may not be available
        import site  # type: ignore

        candidates.extend(site.getsitepackages())
        candidates.append(site.getusersitepackages())
    except Exception:  # pragma: no cover - optional helper
        pass

    for key in ("purelib", "platlib"):
        path = sysconfig.get_path(key)
        if path:
            candidates.append(path)

    seen = set()
    for path in candidates:
        if not path or path in seen:
            continue
        seen.add(path)
        try:
            spec = importlib.machinery.PathFinder.find_spec(_MODULE_NAME, [path])
        except (ImportError, AttributeError):
            continue
        if not spec or not getattr(spec, "loader", None):
            continue
        origin = getattr(spec, "origin", None)
        if origin and Path(origin).resolve() == _MODULE_PATH:
            continue
        module = importlib.util.module_from_spec(spec)
        loader = spec.loader
        assert loader is not None
        loader.exec_module(module)  # type: ignore[arg-type]
        LOGGER.debug("Using real requests package from %%s", origin)
        return module
    return None


_real_requests = _try_import_real_requests()
if _real_requests is not None:
    globals().update(_real_requests.__dict__)
else:

    class RequestException(Exception):
        """Base exception mirroring :class:`requests.RequestException`."""

    class HTTPError(RequestException):
        """HTTP error raised by :meth:`Response.raise_for_status`."""

        def __init__(self, message: str, response: Optional["Response"] = None):
            super().__init__(message)
            self.response = response

    class ConnectionError(RequestException):
        """Raised when a network connection could not be established."""

    class Timeout(RequestException):
        """Raised when an operation exceeds the configured timeout."""

    exceptions = SimpleNamespace(  # type: ignore[assignment]
        RequestException=RequestException,
        HTTPError=HTTPError,
        ConnectionError=ConnectionError,
        Timeout=Timeout,
    )

    _ENCODING_RE = re.compile(r"charset=([\w\-]+)", re.IGNORECASE)

    @dataclass
    class Response:
        """Simplified :class:`requests.Response` wrapper."""

        url: str
        status_code: int
        headers: Dict[str, str]
        content: bytes
        reason: str = ""

        def __post_init__(self) -> None:
            self._encoding: Optional[str] = None

        @property
        def ok(self) -> bool:
            return 200 <= self.status_code < 400

        @property
        def encoding(self) -> str:
            if self._encoding:
                return self._encoding
            content_type = self.headers.get("Content-Type", "")
            match = _ENCODING_RE.search(content_type)
            if match:
                self._encoding = match.group(1)
            else:
                self._encoding = "utf-8"
            return self._encoding

        @encoding.setter
        def encoding(self, value: str) -> None:
            self._encoding = value

        @property
        def text(self) -> str:
            return self.content.decode(self.encoding, errors="replace")

        def json(self) -> Any:
            return json.loads(self.text)

        def raise_for_status(self) -> None:
            if 400 <= self.status_code:
                message = f"{self.status_code} {self.reason}".strip()
                raise HTTPError(message or str(self.status_code), response=self)

    def _coerce_headers(headers: Optional[MutableMapping[str, str]]) -> Dict[str, str]:
        result: Dict[str, str] = {}
        if headers:
            for key, value in headers.items():
                result[str(key)] = str(value)
        return result

    def _merge_params(url: str, params: Optional[Mapping[str, Any] | Iterable[tuple[str, Any]]]) -> str:
        if not params:
            return url
        parsed = urllib_parse.urlsplit(url)
        query = urllib_parse.parse_qsl(parsed.query, keep_blank_values=True)
        if isinstance(params, Mapping):
            query.extend((str(k), str(v)) for k, v in params.items())
        else:
            query.extend((str(k), str(v)) for k, v in params)
        new_query = urllib_parse.urlencode(query, doseq=True)
        rebuilt = parsed._replace(query=new_query)
        return urllib_parse.urlunsplit(rebuilt)

    def _prepare_body(
        data: Optional[Any],
        json_payload: Optional[Any],
        headers: Dict[str, str],
    ) -> Optional[bytes]:
        if json_payload is not None:
            headers.setdefault("Content-Type", "application/json")
            return json.dumps(json_payload).encode("utf-8")
        if data is None:
            return None
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        if isinstance(data, str):
            return data.encode("utf-8")
        if isinstance(data, Mapping):
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
            return urllib_parse.urlencode(data, doseq=True).encode("utf-8")
        if isinstance(data, Iterable):
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
            return urllib_parse.urlencode(list(data), doseq=True).encode("utf-8")
        return str(data).encode("utf-8")

    def _build_response(result: urllib_request.addinfourl) -> Response:
        content = result.read()
        headers = {k: v for k, v in result.headers.items()} if result.headers else {}
        status = getattr(result, "code", getattr(result, "status", 0)) or 0
        reason = getattr(result, "reason", "") or ""
        url = result.geturl() if hasattr(result, "geturl") else ""
        return Response(url=url, status_code=status, headers=headers, content=content, reason=reason)

    class Session:
        """Very small subset of :class:`requests.Session`."""

        def __init__(self) -> None:
            self.headers: Dict[str, str] = {}

        def request(
            self,
            method: str,
            url: str,
            params: Optional[Mapping[str, Any] | Iterable[tuple[str, Any]]] = None,
            data: Optional[Any] = None,
            json: Optional[Any] = None,
            headers: Optional[MutableMapping[str, str]] = None,
            timeout: Optional[float] = None,
            **_: Any,
        ) -> Response:
            merged_headers = _coerce_headers(self.headers)
            merged_headers.update(_coerce_headers(headers))
            final_url = _merge_params(url, params)
            body = _prepare_body(data, json, merged_headers)
            request_obj = urllib_request.Request(final_url, data=body, method=method.upper())
            for key, value in merged_headers.items():
                request_obj.add_header(key, value)
            try:
                result = urllib_request.urlopen(request_obj, timeout=timeout)
            except urllib_error.HTTPError as exc:
                return _build_response(exc)
            except urllib_error.URLError as exc:
                if isinstance(exc.reason, socket.timeout):
                    raise Timeout(str(exc)) from exc
                raise ConnectionError(str(exc)) from exc
            return _build_response(result)

        def get(self, url: str, **kwargs: Any) -> Response:
            return self.request("GET", url, **kwargs)

        def post(self, url: str, **kwargs: Any) -> Response:
            return self.request("POST", url, **kwargs)

        def head(self, url: str, **kwargs: Any) -> Response:
            return self.request("HEAD", url, **kwargs)

        def close(self) -> None:  # pragma: no cover - API completeness
            pass

        def __enter__(self) -> "Session":  # pragma: no cover
            return self

        def __exit__(self, *_: Any) -> None:  # pragma: no cover
            self.close()

    def request(method: str, url: str, **kwargs: Any) -> Response:
        session = Session()
        return session.request(method, url, **kwargs)

    def get(url: str, **kwargs: Any) -> Response:
        return request("GET", url, **kwargs)

    def post(url: str, **kwargs: Any) -> Response:
        return request("POST", url, **kwargs)

    def head(url: str, **kwargs: Any) -> Response:
        return request("HEAD", url, **kwargs)

    RequestException = RequestException
    HTTPError = HTTPError
    ConnectionError = ConnectionError
    Timeout = Timeout

    __all__ = [
        "RequestException",
        "HTTPError",
        "ConnectionError",
        "Timeout",
        "exceptions",
        "request",
        "get",
        "post",
        "head",
        "Session",
        "Response",
    ]
