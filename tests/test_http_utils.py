from __future__ import annotations

from email.message import Message
from io import BytesIO
from collections.abc import Iterable
from types import SimpleNamespace

import pytest

from hermes_redis_gateway.http_utils import MAX_TRAILER_BYTES, MAX_TRAILER_LINE_BYTES, read_json


def _handler(body: bytes, headers: dict[str, str | Iterable[str]]) -> SimpleNamespace:
    message = Message()
    for key, value in headers.items():
        if isinstance(value, str):
            message[key] = value
        else:
            for item in value:
                message[key] = item
    return SimpleNamespace(headers=message, rfile=BytesIO(body))


def test_read_json_supports_chunked_request_body() -> None:
    raw = b'18\r\n{"prompt":"hello","x":1}\r\n0\r\n\r\n'

    payload = read_json(_handler(raw, {"Transfer-Encoding": "chunked"}), max_body_bytes=100)

    assert payload == {"prompt": "hello", "x": 1}


def test_read_json_rejects_oversized_chunked_body() -> None:
    raw = b'14\r\n{"prompt":"too big"}\r\n0\r\n\r\n'

    with pytest.raises(ValueError, match="request body is too large"):
        read_json(_handler(raw, {"Transfer-Encoding": "chunked"}), max_body_bytes=10)


def test_read_json_rejects_signed_chunk_size() -> None:
    raw = b'-1\r\n{"prompt":"must not read to eof"}\r\n0\r\n\r\n'

    with pytest.raises(ValueError, match="malformed chunked request body"):
        read_json(_handler(raw, {"Transfer-Encoding": "chunked"}), max_body_bytes=100)


def test_read_json_rejects_overlong_chunk_metadata() -> None:
    raw = b"1;" + (b"x" * 5000) + b'\r\n{\r\n0\r\n\r\n'

    with pytest.raises(ValueError, match="malformed chunked request body"):
        read_json(_handler(raw, {"Transfer-Encoding": "chunked"}), max_body_bytes=100)


def test_read_json_rejects_ambiguous_transfer_encoding_and_content_length() -> None:
    raw = b'18\r\n{"prompt":"hello","x":1}\r\n0\r\n\r\n'

    with pytest.raises(ValueError, match="ambiguous request framing"):
        read_json(
            _handler(raw, {"Transfer-Encoding": "chunked", "Content-Length": "24"}),
            max_body_bytes=100,
        )


def test_read_json_rejects_transfer_encoding_with_empty_content_length() -> None:
    raw = b'18\r\n{"prompt":"hello","x":1}\r\n0\r\n\r\n'

    with pytest.raises(ValueError, match="ambiguous request framing"):
        read_json(
            _handler(raw, {"Transfer-Encoding": "chunked", "Content-Length": ""}),
            max_body_bytes=100,
        )


def test_read_json_rejects_invalid_transfer_encoding_token() -> None:
    with pytest.raises(ValueError, match="unsupported transfer encoding"):
        read_json(_handler(b'{"prompt":"hello"}', {"Transfer-Encoding": "notchunked"}), max_body_bytes=100)


def test_read_json_rejects_repeated_chunked_transfer_encoding() -> None:
    raw = b'18\r\n{"prompt":"hello","x":1}\r\n0\r\n\r\n'

    with pytest.raises(ValueError, match="unsupported transfer encoding"):
        read_json(_handler(raw, {"Transfer-Encoding": "chunked, chunked"}), max_body_bytes=100)


def test_read_json_rejects_lf_only_chunk_size_line() -> None:
    raw = b'18\n{"prompt":"hello","x":1}\r\n0\r\n\r\n'

    with pytest.raises(ValueError, match="malformed chunked request body"):
        read_json(_handler(raw, {"Transfer-Encoding": "chunked"}), max_body_bytes=100)


def test_read_json_rejects_lf_only_final_chunk_line() -> None:
    raw = b'18\r\n{"prompt":"hello","x":1}\r\n0\n\r\n'

    with pytest.raises(ValueError, match="malformed chunked request body"):
        read_json(_handler(raw, {"Transfer-Encoding": "chunked"}), max_body_bytes=100)


def test_read_json_rejects_negative_content_length() -> None:
    with pytest.raises(ValueError, match="invalid content length"):
        read_json(_handler(b'{"prompt":"hello"}', {"Content-Length": "-1"}), max_body_bytes=100)


def test_read_json_rejects_invalid_content_length() -> None:
    with pytest.raises(ValueError, match="invalid content length"):
        read_json(_handler(b'{"prompt":"hello"}', {"Content-Length": "1x"}), max_body_bytes=100)


def test_read_json_rejects_duplicate_content_length() -> None:
    with pytest.raises(ValueError, match="ambiguous request framing"):
        read_json(
            _handler(b'{"prompt":"hello"}', {"Content-Length": ["18", "19"]}),
            max_body_bytes=100,
        )


def test_read_json_rejects_overlong_chunk_trailer_line() -> None:
    raw = b'18\r\n{"prompt":"hello","x":1}\r\n0\r\n' + (b"x" * (MAX_TRAILER_LINE_BYTES + 1)) + b'\r\n\r\n'

    with pytest.raises(ValueError, match="malformed chunked request body"):
        read_json(_handler(raw, {"Transfer-Encoding": "chunked"}), max_body_bytes=100)


def test_read_json_rejects_excessive_chunk_trailers() -> None:
    trailer_count = (MAX_TRAILER_BYTES // 4) + 1
    raw = b'18\r\n{"prompt":"hello","x":1}\r\n0\r\n' + (b"x: y\r\n" * trailer_count) + b'\r\n'

    with pytest.raises(ValueError, match="malformed chunked request body"):
        read_json(_handler(raw, {"Transfer-Encoding": "chunked"}), max_body_bytes=100)


def test_read_json_rejects_lf_only_trailer_terminator() -> None:
    raw = b'18\r\n{"prompt":"hello","x":1}\r\n0\r\n\n'

    with pytest.raises(ValueError, match="malformed chunked request body"):
        read_json(_handler(raw, {"Transfer-Encoding": "chunked"}), max_body_bytes=100)
