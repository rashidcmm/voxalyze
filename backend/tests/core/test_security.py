import time

from app.core.security import create_access_token, decode_access_token


def test_create_access_token_embeds_iat_and_sub():
    token = create_access_token(subject="user-123")
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "user-123"
    assert isinstance(payload["iat"], int)


def test_decode_access_token_rejects_garbage():
    assert decode_access_token("not-a-jwt") is None


def test_two_tokens_for_the_same_subject_have_non_decreasing_iat():
    first = create_access_token(subject="user-123")
    time.sleep(1)
    second = create_access_token(subject="user-123")
    assert decode_access_token(second)["iat"] >= decode_access_token(first)["iat"]
