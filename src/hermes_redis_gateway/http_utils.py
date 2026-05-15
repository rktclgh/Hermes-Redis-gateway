from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from typing import Any


MAX_CHUNK_LINE_BYTES = 4096
MAX_TRAILER_LINE_BYTES = 4096
MAX_TRAILER_BYTES = 8192


def read_json(handler: BaseHTTPRequestHandler, max_body_bytes: int) -> dict[str, Any]:
    raw = _read_body(handler, max_body_bytes)
    payload = json.loads(raw.decode("utf-8") or "{}")
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")
    return payload


def _read_body(handler: BaseHTTPRequestHandler, max_body_bytes: int) -> bytes:
    transfer_encodings = _transfer_encoding_tokens(handler)
    if transfer_encodings:
        if transfer_encodings[-1] != "chunked":
            raise ValueError("unsupported transfer encoding")
        if handler.headers.get_all("Content-Length", []):
            raise ValueError("ambiguous request framing")
        return _read_chunked_body(handler, max_body_bytes)

    length = _content_length(handler)
    if length > max_body_bytes:
        raise ValueError(f"request body is too large; max {max_body_bytes} bytes")
    return handler.rfile.read(length) if length else b"{}"


def _content_length(handler: BaseHTTPRequestHandler) -> int:
    length_headers = handler.headers.get_all("Content-Length", [])
    if len(length_headers) > 1:
        raise ValueError("ambiguous request framing")
    raw_length = (length_headers[0] if length_headers else "0") or "0"
    if not raw_length.isdecimal():
        raise ValueError("invalid content length")
    return int(raw_length)


def _transfer_encoding_tokens(handler: BaseHTTPRequestHandler) -> list[str]:
    values = handler.headers.get_all("Transfer-Encoding", [])
    tokens: list[str] = []
    for value in values:
        tokens.extend(token.strip().lower() for token in value.split(",") if token.strip())
    unsupported = [token for token in tokens if token != "chunked"]
    if unsupported or tokens.count("chunked") > 1:
        raise ValueError("unsupported transfer encoding")
    return tokens


def _read_chunked_body(handler: BaseHTTPRequestHandler, max_body_bytes: int) -> bytes:
    body = bytearray()
    while True:
        size_line = handler.rfile.readline(MAX_CHUNK_LINE_BYTES + 1)
        if not size_line or len(size_line) > MAX_CHUNK_LINE_BYTES or not size_line.endswith(b"\r\n"):
            raise ValueError("malformed chunked request body")
        size_text = size_line.split(b";", 1)[0].strip()
        if not size_text or any(char not in b"0123456789abcdefABCDEF" for char in size_text):
            raise ValueError("malformed chunked request body")
        try:
            chunk_size = int(size_text, 16)
        except ValueError as exc:
            raise ValueError("malformed chunked request body") from exc
        if chunk_size == 0:
            _consume_chunk_trailers(handler)
            break
        if len(body) + chunk_size > max_body_bytes:
            raise ValueError(f"request body is too large; max {max_body_bytes} bytes")
        body.extend(handler.rfile.read(chunk_size))
        line_end = handler.rfile.read(2)
        if line_end != b"\r\n":
            raise ValueError("malformed chunked request body")
    return bytes(body) or b"{}"


def _consume_chunk_trailers(handler: BaseHTTPRequestHandler) -> None:
    consumed = 0
    while True:
        trailer = handler.rfile.readline(MAX_TRAILER_LINE_BYTES + 1)
        consumed += len(trailer)
        if not trailer or len(trailer) > MAX_TRAILER_LINE_BYTES or consumed > MAX_TRAILER_BYTES:
            raise ValueError("malformed chunked request body")
        if not trailer.endswith(b"\r\n"):
            raise ValueError("malformed chunked request body")
        if trailer == b"\r\n":
            return


def write_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any], headers: dict[str, str] | None = None) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)
