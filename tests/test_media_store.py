from src.media_store import encoded_name, object_key


def test_object_key_uses_the_prefix_and_session(monkeypatch):
    monkeypatch.setenv("BACKBLAZE_OBJECT_PREFIX", "atmon/clips")
    assert object_key("11111111-1111-4111-8111-111111111111") == (
        "atmon/clips/11111111-1111-4111-8111-111111111111.webm"
    )


def test_encoded_name_keeps_slashes():
    assert encoded_name("atmon/clips/session.webm") == "atmon/clips/session.webm"
