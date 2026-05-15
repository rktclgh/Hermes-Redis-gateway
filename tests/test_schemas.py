from hermes_redis_gateway.schemas import public_metadata, require_prompt


def test_require_prompt_rejects_blank() -> None:
    try:
        require_prompt({"prompt": "   "})
    except ValueError as exc:
        assert "prompt is required" in str(exc)
    else:
        raise AssertionError("blank prompt should fail")


def test_public_metadata_redacts_unknown_fields() -> None:
    metadata = public_metadata(
        {
            "metadata": {
                "requestId": "req-1",
                "userId": "u-1",
                "secret": "must-not-leak",
            }
        }
    )

    assert metadata == {"requestId": "req-1", "userId": "u-1"}
