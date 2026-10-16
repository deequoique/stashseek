"""Real locking/constraint tests; requires an explicitly isolated test URL."""

from __future__ import annotations

import os
import importlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, Event
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.channels.identity import consume_link_token, create_link_token
from app.channels.types import ChannelEnvelope, TenantContext
from app.config import Settings
from app.external_auth import ExternalLoginError, ExternalLoginService
from app.external_providers.base import ExternalPrincipal, ProviderAuthorization
from app.models import (
    AppUser, Base, ChannelIdentity, ChannelLinkToken, ContentItem, ConversationThread,
    ConversationTurn, ExternalLoginFlow, ExternalLoginIdentity, IngestDispatch,
    WebAuthChallenge, WebSession,
)
from app.web_auth import InMemoryEmailSender, InvalidSession, WebAuthService

NOW = datetime(2026, 10, 15, 0, tzinfo=UTC)


class _Allow:
    def allow(self, *args, **kwargs):
        return True


class _Provider:
    name, application_id, configuration_id = "microsoft", "test-app", "test-config"

    def begin(self, *, redirect_uri, state):
        return ProviderAuthorization("https://login.microsoftonline.com/authorize?" + urlencode({"state": state}), {"verifier": "private-test-proof"})

    def complete(self, *, redirect_uri, code, material):
        assert material == {"verifier": "private-test-proof"}
        return ExternalPrincipal("test-issuer", code)


@pytest.fixture
def system():
    url = os.environ.get("STASHSEEK_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set STASHSEEK_TEST_DATABASE_URL to an isolated PostgreSQL for real concurrency tests")
    settings = Settings(database_url=url, notebook_agent_env="development", web_auth_enabled=True,
                        web_public_origin="https://app.test", web_auth_secret="s" * 32, email_provider=None)
    engine = create_engine(url, pool_pre_ping=True)
    schema = "test_external_login_" + uuid4().hex
    tables = [model.__table__ for model in (
        AppUser, ChannelIdentity, ExternalLoginIdentity, ExternalLoginFlow,
        WebAuthChallenge, WebSession, ChannelLinkToken, ConversationThread,
        ConversationTurn, ContentItem, IngestDispatch,
    )]
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        Base.metadata.create_all(connection, tables=tables)

    def factory():
        session = Session(engine, expire_on_commit=False)
        session.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        return session

    sender = InMemoryEmailSender()
    email = WebAuthService(factory, settings, sender, _Allow())
    service = ExternalLoginService(email, settings, {"microsoft": _Provider()})
    try:
        yield SimpleNamespace(factory=factory, email=email, service=service, sender=sender)
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def _pending(system, subject):
    started = system.service.start("microsoft", client_ip=uuid4().hex, now=NOW)
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    result = system.service.callback("microsoft", raw_flow_token=started.raw_flow_token,
                                     state=state, code=subject, now=NOW)
    return started, result


def _challenge(system, started, address):
    system.service.request_email_challenge(started.raw_flow_token, address, uuid4().hex, now=NOW)
    return system.sender.messages[-1].code


def _bind(system, started, address, code):
    return system.service.verify_email(started.raw_flow_token, address, code, confirm_binding=True, now=NOW)


def test_first_binding_is_atomic_and_repeat_retains_existing_email_owner(system):
    address = "existing@example.test"
    system.email.request_challenge(address, "test", now=NOW)
    original = system.email.verify(address, system.sender.messages[-1].code, now=NOW)
    started, result = _pending(system, "provider-subject")
    assert result.status == "email_required"
    with system.factory() as db:
        assert db.scalar(select(func.count()).select_from(ExternalLoginIdentity)) == 0
    login = _bind(system, started, address, _challenge(system, started, address))
    assert login.session.tenant.app_user_id == original.session.tenant.app_user_id
    _, repeat = _pending(system, "provider-subject")
    assert repeat.status == "completed"
    assert repeat.login.session.tenant == login.session.tenant


def test_same_provider_same_email_concurrent_binding_has_one_owner(system):
    proofs = []
    for _ in range(2):
        started, _ = _pending(system, "same-subject")
        proofs.append((started, "same@example.test", _challenge(system, started, "same@example.test")))
    barrier = Barrier(2)

    def complete(proof):
        barrier.wait(timeout=10)
        return _bind(system, *proof)

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(complete, proofs))
    assert len({result.session.tenant.app_user_id for result in results}) == 1
    with system.factory() as db:
        assert db.scalar(select(func.count()).select_from(AppUser)) == 1
        assert db.scalar(select(func.count()).select_from(ExternalLoginIdentity)) == 1
        assert db.scalar(select(func.count()).select_from(WebSession)) == 2


def test_same_subject_different_emails_rolls_back_losing_registration(system):
    proofs = []
    for address in ("one@example.test", "two@example.test"):
        started, _ = _pending(system, "same-subject")
        proofs.append((started, address, _challenge(system, started, address)))
    barrier = Barrier(2)

    def complete(proof):
        barrier.wait(timeout=10)
        try:
            return _bind(system, *proof)
        except ExternalLoginError as error:
            return error.error_code

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(complete, proofs))
    assert results.count("binding_conflict") == 1
    with system.factory() as db:
        assert db.scalar(select(func.count()).select_from(AppUser)) == 1
        assert db.scalar(select(func.count()).select_from(ChannelIdentity)) == 1
        assert db.scalar(select(func.count()).select_from(ExternalLoginIdentity)) == 1
        assert db.scalar(select(func.count()).select_from(WebSession)) == 1


def test_email_registration_and_external_binding_share_race_safe_owner(system):
    address = "both@example.test"
    started, _ = _pending(system, "subject")
    bound_code = _challenge(system, started, address)
    system.email.request_challenge(address, "ordinary", now=NOW)
    email_code = system.sender.messages[-1].code
    barrier = Barrier(2)

    def complete(kind):
        barrier.wait(timeout=10)
        return (_bind(system, started, address, bound_code) if kind else
                system.email.verify(address, email_code, now=NOW))

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(complete, (True, False)))
    assert results[0].session.tenant == results[1].session.tenant
    with system.factory() as db:
        assert db.scalar(select(func.count()).select_from(AppUser)) == 1


def test_concurrent_callback_claim_exchanges_at_most_once(system):
    started = system.service.start("microsoft", now=NOW)
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    barrier = Barrier(2)

    def complete(_):
        barrier.wait(timeout=10)
        try:
            return system.service.callback("microsoft", raw_flow_token=started.raw_flow_token,
                                           state=state, code="subject", now=NOW).status
        except ExternalLoginError as error:
            return error.error_code

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(complete, range(2)))
    assert sorted(results) == ["email_required", "flow_used"]


def test_email_login_while_provider_http_is_pending_blocks_late_session(system, monkeypatch):
    first, _ = _pending(system, "same-subject")
    _bind(system, first, "provider@example.test", _challenge(system, first, "provider@example.test"))
    started = system.service.start("microsoft", now=NOW)
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    system.email.request_challenge("email@example.test", "ordinary", now=NOW)
    code = system.sender.messages[-1].code
    entered, release = Event(), Event()
    original = _Provider.complete

    def slow_complete(self, **kwargs):
        entered.set()
        assert release.wait(timeout=10)
        return original(self, **kwargs)

    monkeypatch.setattr(_Provider, "complete", slow_complete)

    def callback():
        try:
            return system.service.callback("microsoft", raw_flow_token=started.raw_flow_token,
                                           state=state, code="same-subject", now=NOW)
        except ExternalLoginError as error:
            return error.error_code

    with ThreadPoolExecutor(1) as executor:
        future = executor.submit(callback)
        try:
            assert entered.wait(timeout=10)
            email_login = system.email.verify("email@example.test", code, now=NOW,
                                              external_login_flow_token=started.raw_flow_token)
        finally:
            release.set()
        assert future.result(timeout=10) == "flow_used"
    assert system.email.resolve_session(email_login.raw_session_token, now=NOW).login_channel == "email"
    pending = system.service.pending(started.raw_flow_token, now=NOW)
    assert (pending.status, pending.error_code) == ("failed", "session_changed")
    with system.factory() as db:
        assert db.scalar(select(func.count()).select_from(WebSession)) == 2


def test_email_login_waits_for_binding_then_revokes_its_late_session(system, monkeypatch):
    started, _ = _pending(system, "subject")
    binding_code = _challenge(system, started, "provider@example.test")
    system.email.request_challenge("email@example.test", "ordinary", now=NOW)
    code = system.sender.messages[-1].code
    entered, release = Event(), Event()
    issue = system.email._issue_session

    def slow_issue(*args, **kwargs):
        result = issue(*args, **kwargs)
        if kwargs.get("external_login_identity_id") is not None:
            entered.set()
            assert release.wait(timeout=10)
        return result

    monkeypatch.setattr(system.email, "_issue_session", slow_issue)
    with ThreadPoolExecutor(2) as executor:
        binding = executor.submit(_bind, system, started, "provider@example.test", binding_code)
        try:
            assert entered.wait(timeout=10)
            email = executor.submit(system.email.verify, "email@example.test", code, now=NOW,
                                    external_login_flow_token=started.raw_flow_token)
        finally:
            release.set()
        provider_login, email_login = binding.result(timeout=10), email.result(timeout=10)
    with pytest.raises(InvalidSession):
        system.email.resolve_session(provider_login.raw_session_token, now=NOW)
    assert system.email.resolve_session(email_login.raw_session_token, now=NOW).login_channel == "email"
    pending = system.service.pending(started.raw_flow_token, now=NOW)
    assert (pending.status, pending.error_code) == ("failed", "session_changed")


def test_supported_merge_preserves_provider_anchor_history_and_revokes_absorbed_session(system):
    started, _ = _pending(system, "subject-b")
    login = _bind(system, started, "b@example.test", _challenge(system, started, "b@example.test"))
    target = login.session.tenant
    with system.factory() as db:
        source = AppUser()
        db.add(source)
        db.flush()
        identity = ChannelIdentity(app_user_id=source.id, channel="telegram", account_id="bot", external_user_id="source")
        db.add(identity)
        db.flush()
        source_tenant = TenantContext(source.id, identity.id, identity.channel, identity.account_id, identity.external_user_id)
        thread = ConversationThread(public_id=uuid4().hex, app_user_id=target.app_user_id,
                                    channel_identity_id=target.channel_identity_id, channel="web", account_id="web", external_conversation_id="test")
        db.add(thread)
        db.flush()
        db.add(ConversationTurn(thread_id=thread.id, message_id="q", user_text="preserved question", assistant_text="preserved answer", sources=[], model_messages=[]))
        db.add(ContentItem(user_id=target.app_user_id, platform="youtube", platform_id="test", kind="video", url="https://youtube.com/watch?v=test"))
        token = create_link_token(db, source_tenant, target_channel="web", now=NOW)
        db.commit()
    envelope = ChannelEnvelope(channel="web", account_id="web", external_user_id="b@example.test", conversation_id="test", message_id="link", text="link")
    with system.factory() as db:
        merged = consume_link_token(db, envelope, token, now=NOW)
        db.commit()
    assert merged.app_user_id == source_tenant.app_user_id
    with pytest.raises(InvalidSession):
        system.email.resolve_session(login.raw_session_token, now=NOW)
    _, repeat = _pending(system, "subject-b")
    assert repeat.login.session.tenant.app_user_id == merged.app_user_id
    assert repeat.login.session.tenant.channel_identity_id == target.channel_identity_id
    with system.factory() as db:
        assert db.scalar(select(ContentItem.user_id)) == merged.app_user_id
        assert db.scalar(select(ConversationThread.app_user_id)) == merged.app_user_id
        assert db.scalar(select(ConversationTurn.user_text)) == "preserved question"
        assert db.get(AppUser, target.app_user_id) is None


def test_cli_cannot_rebind_external_email_anchor(system, monkeypatch):
    from app import cli
    started, _ = _pending(system, "subject")
    login = _bind(system, started, "a@example.test", _challenge(system, started, "a@example.test"))
    with system.factory() as db:
        other = AppUser()
        db.add(other)
        db.commit()
        other_id = other.id
    monkeypatch.setattr(cli, "get_session_factory", lambda: system.factory)
    with pytest.raises(SystemExit, match="external login bindings"):
        cli._users(SimpleNamespace(user_command="rebind-identity", identity_id=login.session.tenant.channel_identity_id, user_id=other_id))
    assert system.email.resolve_session(login.raw_session_token, now=NOW).tenant == login.session.tenant


def test_additive_auth_migration_roundtrip_preserves_legacy_rows():
    url = os.environ.get("STASHSEEK_TEST_DATABASE_URL")
    if not url:
        pytest.skip("explicit isolated PostgreSQL URL required")
    engine = create_engine(url)
    schema = "test_external_migration_" + uuid4().hex
    migration = importlib.import_module("migrations.versions.e2f3a4b5c6d7_external_login_foundation")
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            # Only the predecessor auth-table columns touched by this revision
            # are needed. This exercises its actual PostgreSQL DDL without
            # claiming a roundtrip of unrelated vector/ingestion migrations.
            for statement in (
                "CREATE TABLE app_user(id BIGSERIAL PRIMARY KEY)",
                "CREATE TABLE channel_identity(id BIGSERIAL PRIMARY KEY, app_user_id BIGINT NOT NULL REFERENCES app_user(id))",
                "CREATE TABLE web_auth_challenge(id BIGSERIAL PRIMARY KEY, email TEXT NOT NULL, code_hash TEXT NOT NULL)",
                "CREATE TABLE web_session(id BIGSERIAL PRIMARY KEY, app_user_id BIGINT NOT NULL REFERENCES app_user(id), channel_identity_id BIGINT REFERENCES channel_identity(id), login_channel TEXT NOT NULL)",
                "INSERT INTO app_user(id) VALUES (1)",
                "INSERT INTO channel_identity(id,app_user_id) VALUES (1,1)",
                "INSERT INTO web_auth_challenge(email,code_hash) VALUES ('old@example.test','hash')",
                "INSERT INTO web_session(app_user_id,channel_identity_id,login_channel) VALUES (1,1,'web'),(1,NULL,'telegram'),(1,NULL,'wechat')",
            ):
                connection.execute(text(statement))
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration.upgrade()
                assert connection.execute(text("SELECT login_channel FROM web_session ORDER BY id")).scalars().all() == ["web", "telegram", "wechat"]
                assert connection.execute(text("SELECT count(*) FROM web_auth_challenge WHERE external_login_flow_id IS NULL")).scalar_one() == 1
                connection.execute(text("INSERT INTO external_login_identity(provider,application_id,namespace,subject,app_user_id,channel_identity_id) VALUES ('qq','app','namespace','subject',1,1)"))
                connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                migration.downgrade()
                assert connection.execute(text("SELECT login_channel FROM web_session ORDER BY id")).scalars().all() == ["web", "telegram", "wechat"]
                migration.upgrade()
                assert connection.execute(text("SELECT count(*) FROM web_auth_challenge")).scalar_one() == 1
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
