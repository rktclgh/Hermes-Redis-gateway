from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from typing import Any


def read_json(handler: BaseHTTPRequestHandler, max_body_bytes: int) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length > max_body_bytes:
        raise ValueError(f"request body is too large; max {max_body_bytes} bytes")
    raw = handler.rfile.read(length) if length else b"{}"
    payload = json.loads(raw.decode("utf-8") or "{}")
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")
    return payload


def write_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any], headers: dict[str, str] | None = None) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)
