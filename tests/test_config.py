import pytest

from userbot.config import _parse_user_ids


def test_parse_user_ids():
    assert _parse_user_ids("123, 456") == frozenset({123, 456})


def test_parse_user_ids_empty_is_fail_closed():
    assert _parse_user_ids("") == frozenset()


def test_parse_user_ids_rejects_invalid_value():
    with pytest.raises(RuntimeError):
        _parse_user_ids("123,not-an-id")
