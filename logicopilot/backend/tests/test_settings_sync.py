"""Split servers pick up OpenAI key changes saved on another server - and only reload when
something actually changed (a reload also forgets rate-limit cooldowns)."""
from app.core import settings_sync
from app.core.mail_crypto import encrypt_secret
from app.models.openai_api_key import OpenAIApiKey


def test_reload_only_when_keys_change(db_session, monkeypatch):
    reloads = []
    monkeypatch.setattr(settings_sync, "reload_all", lambda db: reloads.append(1))

    fp = settings_sync.check_once(db_session, None)          # startup: remember, no reload
    assert reloads == []
    fp = settings_sync.check_once(db_session, fp)            # nothing changed
    assert reloads == []

    key = OpenAIApiKey(label="k1", key_encrypted=encrypt_secret("sk-test-1"), is_active=True)
    db_session.add(key)
    db_session.commit()
    fp = settings_sync.check_once(db_session, fp)            # added on "another server"
    assert reloads == [1]

    key.is_active = False
    db_session.commit()
    fp = settings_sync.check_once(db_session, fp)            # deactivated
    assert reloads == [1, 1]

    settings_sync.check_once(db_session, fp)                 # steady again
    assert reloads == [1, 1]


def test_single_key_override_counts(db_session, monkeypatch):
    from app.core.system_settings import get_system_settings

    reloads = []
    monkeypatch.setattr(settings_sync, "reload_all", lambda db: reloads.append(1))
    fp = settings_sync.check_once(db_session, None)
    get_system_settings(db_session).openai_api_key_encrypted = encrypt_secret("sk-new")
    db_session.commit()
    settings_sync.check_once(db_session, fp)
    assert reloads == [1]
