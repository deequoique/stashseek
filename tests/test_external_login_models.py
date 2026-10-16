from datetime import UTC, datetime, timedelta

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AppUser, ChannelIdentity, ExternalLoginIdentity, WebSession


def test_external_identity_keys_and_owner_provenance_constraints():
    table = ExternalLoginIdentity.__table__
    for field in ("provider", "application_id", "namespace", "subject", "app_user_id", "channel_identity_id"):
        assert table.c[field].nullable is False
    unique = next(c for c in table.constraints if c.name == "uq_external_login_principal")
    assert [column.name for column in unique.columns] == ["provider", "application_id", "namespace", "subject"]
    anchor = next(c for c in table.constraints if c.name == "fk_external_login_anchor_owner")
    assert anchor.deferrable and anchor.initially == "DEFERRED"
    provenance = next(iter(WebSession.__table__.c.external_login_identity_id.foreign_keys))
    assert provenance.ondelete == "RESTRICT"
    assert "ck_web_session_external_provenance" in {c.name for c in WebSession.__table__.constraints}


@pytest.fixture
def model_engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def fk(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    for model in (AppUser, ChannelIdentity, ExternalLoginIdentity, WebSession):
        model.__table__.create(engine)
    with Session(engine) as db:
        db.add_all([AppUser(id=1), AppUser(id=2)])
        db.flush()
        db.add(ChannelIdentity(id=10, app_user_id=1, channel="web", account_id="web", external_user_id="user@example.test"))
        db.commit()
    yield engine
    engine.dispose()


def mapping(**changes):
    values = dict(id=100, app_user_id=1, channel_identity_id=10,
                  provider="microsoft", application_id="app", namespace="issuer", subject="subject")
    values.update(changes)
    return ExternalLoginIdentity(**values)


def test_composite_anchor_owner_mismatch_cannot_commit(model_engine):
    with Session(model_engine) as db:
        db.add(mapping(app_user_id=2))
        with pytest.raises(IntegrityError):
            db.commit()


def test_identity_namespace_and_case_sensitive_subject_are_part_of_unique_key(model_engine):
    with Session(model_engine) as db:
        db.add_all([mapping(), mapping(id=101, subject="Subject"), mapping(id=102, namespace="different")])
        db.commit()
    with Session(model_engine) as db:
        db.add(mapping(id=103))
        with pytest.raises(IntegrityError):
            db.commit()


def test_provenance_cannot_be_removed_or_hard_deleted_into_email_login(model_engine):
    with Session(model_engine) as db:
        row = mapping()
        db.add(row)
        db.flush()
        db.add(WebSession(id=200, public_id="session", token_hash="hash", csrf_token_hash="csrf",
                          app_user_id=1, channel_identity_id=10, login_channel="microsoft",
                          external_login_identity_id=100, expires_at=datetime.now(UTC) + timedelta(days=1)))
        db.commit()
    with Session(model_engine) as db:
        db.delete(db.get(ExternalLoginIdentity, 100))
        with pytest.raises(IntegrityError):
            db.commit()
    with Session(model_engine) as db:
        db.get(WebSession, 200).external_login_identity_id = None
        with pytest.raises(IntegrityError):
            db.commit()


def test_deferred_anchor_fk_allows_explicit_atomic_owner_migration(model_engine):
    with Session(model_engine) as db:
        db.add(mapping())
        db.commit()
    with Session(model_engine) as db:
        db.get(ChannelIdentity, 10).app_user_id = 2
        db.flush()
        db.get(ExternalLoginIdentity, 100).app_user_id = 2
        db.commit()
    with Session(model_engine) as db:
        assert db.scalar(select(ExternalLoginIdentity)).app_user_id == 2


def test_additive_external_login_migration_remains_in_single_head_chain():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_heads() == ["f8a9b0c1d2e3"]
    assert script.get_revision("f8a9b0c1d2e3").down_revision == "e2f3a4b5c6d7"
    assert script.get_revision("e2f3a4b5c6d7").down_revision == "d04fdae36884"
