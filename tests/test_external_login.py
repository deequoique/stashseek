"""Credential-free shared-flow tests; PostgreSQL races live in their own suite."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.external_auth import ExternalLoginError, ExternalLoginService
from app.external_providers.base import ExternalPrincipal, ProviderAuthorization, ProviderError
from app.models import (
    AppUser, ChannelIdentity, ExternalLoginFlow, ExternalLoginIdentity, WebAuthChallenge, WebSession,
)
from app.web.auth import WebAuthError as LegacyError, WebAuthService as LegacyAuth
from app.web_auth import InMemoryEmailSender, InvalidSession, InvalidVerification, RedisSessionCache, WebAuthService, _token_hash

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)


class FakeProvider:
    name = "microsoft"
    application_id = "fake-app"
    configuration_id = "fake-config-v1"
    subject = "subject-A"

    def __init__(self):
        self.complete_calls = 0
        self.fail = False
        self.on_complete = None

    def begin(self, *, redirect_uri, state):
        return ProviderAuthorization("https://identity.example.test/authorize?" + urlencode({"state": state}),
                                     {"state": state, "verifier": "server-only-pkce-verifier"})

    def complete(self, *, redirect_uri, code, material):
        self.complete_calls += 1
        assert material["verifier"] == "server-only-pkce-verifier"
        if self.on_complete:
            self.on_complete()
        if self.fail:
            raise ProviderError()
        return ExternalPrincipal("https://identity.example.test/tenant", self.subject)


class AllowAll:
    def allow(self, *_args, **_kwargs):
        return True


@pytest.fixture
def auth_runtime():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    for model in (AppUser, ChannelIdentity, ExternalLoginIdentity, ExternalLoginFlow, WebAuthChallenge, WebSession):
        model.__table__.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    settings = Settings(database_url="sqlite://", notebook_agent_env="development", web_auth_enabled=True,
                        web_public_origin="https://app.example.test", web_auth_secret="test-auth-secret-" * 4)
    sender = InMemoryEmailSender()
    email = WebAuthService(factory, settings, sender, AllowAll())
    provider = FakeProvider()
    providers = {"microsoft": provider}
    service = ExternalLoginService(email, settings, providers)
    yield SimpleNamespace(engine=engine, factory=factory, settings=settings, sender=sender,
                          email=email, provider=provider, providers=providers, service=service)
    engine.dispose()


def authorize(rt, *, previous=""):
    started = rt.service.start("microsoft", previous_flow_token=previous, now=NOW)
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    result = rt.service.callback("microsoft", raw_flow_token=started.raw_flow_token,
                                 state=state, code="one-time-code", now=NOW)
    return started, state, result


def bind(rt, started, email="person@example.test"):
    rt.service.request_email_challenge(started.raw_flow_token, email, "127.0.0.1", now=NOW)
    return rt.service.verify_email(started.raw_flow_token, email, rt.sender.sent[-1].code,
                                  confirm_binding=True, now=NOW)


def test_binding_email_states_the_provider(auth_runtime):
    rt = auth_runtime
    started, _state, _result = authorize(rt)
    rt.service.request_email_challenge(
        started.raw_flow_token, "person@example.test", "127.0.0.1", now=NOW,
    )
    message = rt.sender.sent[-1]
    assert message.purpose == "linking a Microsoft account"


def test_first_use_proves_both_identities_before_creating_account(auth_runtime):
    rt = auth_runtime
    started, _state, result = authorize(rt)
    assert result.status == "email_required" and result.login is None
    rt.service.validate_flow_csrf(started.raw_flow_token, started.raw_csrf_token, now=NOW)
    with rt.factory() as db:
        assert db.scalar(select(func.count()).select_from(AppUser)) == 0
        assert db.scalar(select(func.count()).select_from(WebSession)) == 0
    login = bind(rt, started, "  Person@Example.test ")
    assert login.session.tenant.external_user_id == "person@example.test"
    assert login.session.login_channel == "microsoft"
    assert login.session.external_login_identity_id is not None
    assert rt.email.resolve_session(login.raw_session_token, now=NOW) == login.session
    assert rt.service.pending(started.raw_flow_token, session_token=login.raw_session_token, now=NOW).status == "completed"
    with rt.factory() as db:
        mapping = db.scalar(select(ExternalLoginIdentity))
        session = db.scalar(select(WebSession))
        assert mapping.app_user_id == login.session.tenant.app_user_id
        assert mapping.channel_identity_id == login.session.tenant.channel_identity_id
        assert session.token_hash != login.raw_session_token
        assert session.external_login_identity_id == mapping.id


def test_existing_email_owner_is_preserved_and_repeat_login_needs_no_email(auth_runtime):
    rt = auth_runtime
    rt.email.request_challenge("person@example.test", "ip", now=NOW)
    original = rt.email.verify("person@example.test", rt.sender.sent[-1].code, now=NOW)
    started, _, _ = authorize(rt)
    login = bind(rt, started)
    assert login.session.tenant == original.session.tenant
    mail_count = len(rt.sender.sent)
    _second, _, repeat = authorize(rt)
    assert repeat.status == "completed"
    assert repeat.login.session.tenant == original.session.tenant
    assert len(rt.sender.sent) == mail_count
    with rt.factory() as db:
        assert db.scalar(select(func.count()).select_from(AppUser)) == 1
        assert db.scalar(select(func.count()).select_from(ExternalLoginIdentity)) == 1


def test_sdk_material_encrypted_claim_commits_before_http_and_replay_does_not_exchange(auth_runtime):
    rt = auth_runtime
    started = rt.service.start("microsoft", now=NOW)
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    with rt.factory() as db:
        flow = db.scalar(select(ExternalLoginFlow))
        assert state not in flow.material_ciphertext
        assert "server-only-pkce-verifier" not in flow.material_ciphertext
        assert started.raw_flow_token != flow.token_hash
        assert started.raw_csrf_token != flow.csrf_token_hash

    def check_claim():
        with rt.factory() as db:
            flow = db.scalar(select(ExternalLoginFlow))
            assert flow.phase == "exchanging"
            assert flow.material_ciphertext is None

    rt.provider.on_complete = check_claim
    rt.service.callback("microsoft", raw_flow_token=started.raw_flow_token, state=state, code="code", now=NOW)
    with pytest.raises(ExternalLoginError, match="flow_used"):
        rt.service.callback("microsoft", raw_flow_token=started.raw_flow_token, state=state, code="code", now=NOW)
    assert rt.provider.complete_calls == 1
    assert rt.service.pending(started.raw_flow_token, now=NOW).status == "email_required"


@pytest.mark.parametrize("fault", ["wrong_browser", "wrong_state", "wrong_provider", "expired", "cancelled", "configuration"])
def test_invalid_callback_never_exchanges_or_creates_account(auth_runtime, fault):
    rt = auth_runtime
    started = rt.service.start("microsoft", now=NOW)
    token = started.raw_flow_token
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    when, provider = NOW, "microsoft"
    if fault == "wrong_browser": token = "foreign-browser-token"
    if fault == "wrong_state": state = "wrong-state"
    if fault == "wrong_provider": provider = "qq"
    if fault == "expired": when += timedelta(minutes=11)
    if fault == "cancelled": rt.service.cancel(token, now=NOW)
    if fault == "configuration": rt.provider.configuration_id = "new-config"
    with pytest.raises(ExternalLoginError):
        rt.service.callback(provider, raw_flow_token=token, state=state, code="code", now=when)
    assert rt.provider.complete_calls == 0
    with rt.factory() as db:
        assert db.scalar(select(func.count()).select_from(AppUser)) == 0


def test_new_start_cancels_old_context_and_flow_csrf_is_bound(auth_runtime):
    rt = auth_runtime
    first = rt.service.start("microsoft", now=NOW)
    second = rt.service.start("microsoft", previous_flow_token=first.raw_flow_token, now=NOW)
    assert rt.service.pending(first.raw_flow_token, now=NOW).status == "cancelled"
    with pytest.raises(ExternalLoginError, match="flow_csrf_invalid"):
        rt.service.validate_flow_csrf(second.raw_flow_token, first.raw_csrf_token, now=NOW)


def test_ordinary_or_other_flow_email_code_cannot_complete_binding(auth_runtime):
    rt = auth_runtime
    first, _, _ = authorize(rt)
    rt.email.request_challenge("person@example.test", "ip", now=NOW)
    ordinary_code = rt.sender.sent[-1].code
    with pytest.raises(InvalidVerification):
        rt.service.verify_email(first.raw_flow_token, "person@example.test", ordinary_code, confirm_binding=True, now=NOW)
    second, _, _ = authorize(rt)
    rt.service.request_email_challenge(first.raw_flow_token, "person@example.test", "ip", now=NOW)
    first_code = rt.sender.sent[-1].code
    with pytest.raises(InvalidVerification):
        rt.service.verify_email(second.raw_flow_token, "person@example.test", first_code, confirm_binding=True, now=NOW)
    login = rt.email.verify("person@example.test", ordinary_code, now=NOW)
    assert login.session.login_channel == "email"
    with rt.factory() as db:
        assert db.scalar(select(func.count()).select_from(ExternalLoginIdentity)) == 0


def test_wrong_code_counts_attempt_and_explicit_confirmation_is_required(auth_runtime):
    rt = auth_runtime
    started, _, _ = authorize(rt)
    rt.service.request_email_challenge(started.raw_flow_token, "person@example.test", "ip", now=NOW)
    code = rt.sender.sent[-1].code
    with pytest.raises(ExternalLoginError, match="verification_failed"):
        rt.service.verify_email(started.raw_flow_token, "person@example.test", code, confirm_binding=False, now=NOW)
    wrong = "000000" if code != "000000" else "111111"
    with pytest.raises(InvalidVerification):
        rt.service.verify_email(started.raw_flow_token, "person@example.test", wrong, confirm_binding=True, now=NOW)
    with rt.factory() as db:
        proof = db.scalar(select(WebAuthChallenge))
        assert proof.attempt_count == 1 and proof.consumed_at is None
        assert db.scalar(select(func.count()).select_from(AppUser)) == 0
    login = rt.service.verify_email(started.raw_flow_token, "person@example.test", code, confirm_binding=True, now=NOW)
    assert login.session.login_channel == "microsoft"


def test_changing_binding_mailbox_invalidates_previous_flow_code(auth_runtime):
    rt = auth_runtime
    started, _, _ = authorize(rt)
    rt.service.request_email_challenge(started.raw_flow_token, "first@example.test", "ip", now=NOW)
    old_code = rt.sender.sent[-1].code
    rt.service.request_email_challenge(started.raw_flow_token, "second@example.test", "ip", now=NOW)
    current_code = rt.sender.sent[-1].code
    with pytest.raises(InvalidVerification):
        rt.service.verify_email(started.raw_flow_token, "first@example.test", old_code, confirm_binding=True, now=NOW)
    login = rt.service.verify_email(started.raw_flow_token, "second@example.test", current_code,
                                   confirm_binding=True, now=NOW)
    assert login.session.tenant.external_user_id == "second@example.test"


def test_binding_conflict_does_not_create_orphan_or_reassign_owner(auth_runtime):
    rt = auth_runtime
    first, _, _ = authorize(rt)
    second, _, _ = authorize(rt)
    original = bind(rt, first, "original@example.test")
    rt.service.request_email_challenge(second.raw_flow_token, "different@example.test", "ip", now=NOW)
    with pytest.raises(ExternalLoginError, match="binding_conflict"):
        rt.service.verify_email(second.raw_flow_token, "different@example.test", rt.sender.sent[-1].code,
                                confirm_binding=True, now=NOW)
    with rt.factory() as db:
        assert db.scalar(select(func.count()).select_from(AppUser)) == 1
        assert db.scalar(select(func.count()).select_from(ChannelIdentity)) == 1
        assert db.scalar(select(ExternalLoginIdentity)).app_user_id == original.session.tenant.app_user_id
        assert db.scalar(select(func.count()).select_from(WebSession)) == 1


def test_transaction_failure_rolls_back_binding_account_session_and_proof(auth_runtime, monkeypatch):
    rt = auth_runtime
    started, _, _ = authorize(rt)
    rt.service.request_email_challenge(started.raw_flow_token, "new@example.test", "ip", now=NOW)
    code = rt.sender.sent[-1].code
    issue = rt.email._issue_session

    def fail_after_issue(*args, **kwargs):
        issue(*args, **kwargs)
        raise RuntimeError("injected pre-commit failure")

    monkeypatch.setattr(rt.email, "_issue_session", fail_after_issue)
    with pytest.raises(RuntimeError):
        rt.service.verify_email(started.raw_flow_token, "new@example.test", code, confirm_binding=True, now=NOW)
    with rt.factory() as db:
        assert db.scalar(select(func.count()).select_from(AppUser)) == 0
        assert db.scalar(select(func.count()).select_from(ExternalLoginIdentity)) == 0
        assert db.scalar(select(func.count()).select_from(WebSession)) == 0
        assert db.scalar(select(WebAuthChallenge)).consumed_at is None
        assert db.scalar(select(ExternalLoginFlow)).phase == "email_required"


def test_failed_ordinary_email_proof_keeps_browser_flow_and_provider_session(auth_runtime):
    rt = auth_runtime
    started, _, _ = authorize(rt)
    original = bind(rt, started)
    rt.email.request_challenge("other@example.test", "ip", now=NOW)
    code = rt.sender.sent[-1].code
    wrong = "000000" if code != "000000" else "111111"
    with pytest.raises(InvalidVerification):
        rt.email.verify("other@example.test", wrong, now=NOW,
                        external_login_flow_token=started.raw_flow_token)
    assert rt.email.resolve_session(original.raw_session_token, now=NOW) == original.session
    assert rt.service.pending(started.raw_flow_token, now=NOW).status == "completed"
    with rt.factory() as db:
        proof = db.scalar(select(WebAuthChallenge).where(WebAuthChallenge.external_login_flow_id.is_(None)))
        assert proof.attempt_count == 1 and proof.consumed_at is None


def test_email_switch_revokes_completed_flow_session_before_a_late_response_can_restore_it(auth_runtime):
    rt = auth_runtime
    started, _, _ = authorize(rt)
    original = bind(rt, started)
    rt.email.request_challenge("other@example.test", "ip", now=NOW)
    login = rt.email.verify("other@example.test", rt.sender.sent[-1].code, now=NOW,
                            external_login_flow_token=started.raw_flow_token)
    assert rt.email.resolve_session(login.raw_session_token, now=NOW).login_channel == "email"
    with pytest.raises(InvalidSession):
        rt.email.resolve_session(original.raw_session_token, now=NOW)
    pending = rt.service.pending(started.raw_flow_token, now=NOW)
    assert (pending.status, pending.error_code) == ("failed", "session_changed")
    with rt.factory() as db:
        flow = db.scalar(select(ExternalLoginFlow))
        assert flow.completed_session_hash is None
        assert flow.material_ciphertext is flow.namespace is flow.subject is None
        assert db.scalar(select(func.count()).select_from(ExternalLoginIdentity)) == 1


def test_email_switch_commit_failure_keeps_prior_flow_session_and_email_proof(auth_runtime):
    rt = auth_runtime
    started, _, _ = authorize(rt)
    original = bind(rt, started)
    rt.email.request_challenge("person@example.test", "ip", now=NOW)
    code = rt.sender.sent[-1].code

    def reject_commit(db):
        if not db.in_nested_transaction():
            raise RuntimeError("injected outer commit failure")

    event.listen(rt.factory, "before_commit", reject_commit)
    try:
        with pytest.raises(RuntimeError, match="injected outer commit failure"):
            rt.email.verify("person@example.test", code, now=NOW,
                            external_login_flow_token=started.raw_flow_token)
    finally:
        event.remove(rt.factory, "before_commit", reject_commit)
    assert rt.email.resolve_session(original.raw_session_token, now=NOW) == original.session
    assert rt.service.pending(started.raw_flow_token, now=NOW).status == "completed"
    with rt.factory() as db:
        proof = db.scalar(select(WebAuthChallenge).where(WebAuthChallenge.external_login_flow_id.is_(None)))
        assert proof.consumed_at is None and proof.attempt_count == 0
        assert db.scalar(select(func.count()).select_from(WebSession)) == 1


def test_provider_error_cannot_overwrite_completed_email_switch_failure(auth_runtime):
    rt = auth_runtime
    started = rt.service.start("microsoft", now=NOW)
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    rt.email.request_challenge("email@example.test", "ip", now=NOW)
    code = rt.sender.sent[-1].code
    rt.provider.on_complete = lambda: rt.email.verify(
        "email@example.test", code, now=NOW, external_login_flow_token=started.raw_flow_token,
    )
    rt.provider.fail = True
    with pytest.raises(ExternalLoginError, match="provider_failed"):
        rt.service.callback("microsoft", raw_flow_token=started.raw_flow_token, state=state, code="code", now=NOW)
    pending = rt.service.pending(started.raw_flow_token, now=NOW)
    assert (pending.status, pending.error_code) == ("failed", "session_changed")


@pytest.mark.parametrize("mutation", ["owner", "anchor", "mapping", "provider", "application"])
def test_provider_session_uses_one_db_join_and_rejects_revocation_without_cache_broadcast(auth_runtime, mutation):
    rt = auth_runtime
    started, _, _ = authorize(rt)
    login = bind(rt, started)

    class CacheSpy:
        def lookup(self, *args, **kwargs): return (None, None)
        def put(self, *args, **kwargs): raise AssertionError("provider projection entered Redis")

    rt.email._session_cache = CacheSpy()
    statements = []

    @event.listens_for(rt.engine, "before_cursor_execute")
    def capture(_connection, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    resolved = rt.email.resolve_session(login.raw_session_token, now=NOW)
    assert resolved == login.session
    assert len(statements) == 1
    with rt.factory() as db:
        if mutation == "owner": db.get(AppUser, login.session.tenant.app_user_id).disabled_at = NOW
        if mutation == "anchor": db.get(ChannelIdentity, login.session.tenant.channel_identity_id).disabled_at = NOW
        if mutation == "mapping": db.get(ExternalLoginIdentity, login.session.external_login_identity_id).disabled_at = NOW
        db.commit()
    if mutation == "provider": rt.providers.clear()
    if mutation == "application": rt.provider.application_id = "different-app"
    statements.clear()
    with pytest.raises(InvalidSession):
        rt.email.resolve_session(login.raw_session_token, now=NOW)
    assert len(statements) == 1


def test_disable_mapping_revokes_provenance_sessions_but_email_fallback_survives(auth_runtime):
    rt = auth_runtime
    started, _, _ = authorize(rt)
    login = bind(rt, started)
    rt.email.request_challenge("person@example.test", "ip", now=NOW)
    email_login = rt.email.verify("person@example.test", rt.sender.sent[-1].code, now=NOW)
    rt.service.disable_identity(login.session.external_login_identity_id, now=NOW)
    with pytest.raises(InvalidSession):
        rt.email.resolve_session(login.raw_session_token, now=NOW)
    assert rt.email.resolve_session(email_login.raw_session_token, now=NOW).login_channel == "email"
    with rt.factory() as db:
        db.get(ExternalLoginIdentity, login.session.external_login_identity_id).disabled_at = None
        db.commit()
    with pytest.raises(InvalidSession):
        rt.email.resolve_session(login.raw_session_token, now=NOW)


def test_existing_or_changed_session_is_preserved_and_completed_flow_matches_its_session(auth_runtime):
    rt = auth_runtime
    started, state, _ = authorize(rt)
    rt.email.request_challenge("other@example.test", "ip", now=NOW)
    unrelated = rt.email.verify("other@example.test", rt.sender.sent[-1].code, now=NOW)
    with pytest.raises(ExternalLoginError, match="session_present"):
        rt.service.start("microsoft", session_token=unrelated.raw_session_token, now=NOW)
    with pytest.raises(ExternalLoginError, match="session_changed"):
        rt.service.verify_email(started.raw_flow_token, "person@example.test", "000000", confirm_binding=True,
                                session_token=unrelated.raw_session_token, now=NOW)
    assert rt.email.resolve_session(unrelated.raw_session_token, now=NOW).tenant == unrelated.session.tenant
    another, _, _ = authorize(rt)
    bound = bind(rt, another)
    assert rt.service.pending(another.raw_flow_token, session_token=bound.raw_session_token, now=NOW).status == "completed"
    outcome = rt.service.pending(another.raw_flow_token, session_token=unrelated.raw_session_token, now=NOW)
    assert outcome.status == "failed" and outcome.error_code == "session_changed"


def test_provider_failure_is_safe_and_not_retryable_with_consumed_code(auth_runtime):
    rt = auth_runtime
    rt.provider.fail = True
    started = rt.service.start("microsoft", now=NOW)
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    with pytest.raises(ExternalLoginError, match="provider_failed"):
        rt.service.callback("microsoft", raw_flow_token=started.raw_flow_token, state=state, code="code", now=NOW)
    assert rt.service.pending(started.raw_flow_token, now=NOW).error_code == "provider_failed"
    with pytest.raises(ExternalLoginError, match="flow_used"):
        rt.service.callback("microsoft", raw_flow_token=started.raw_flow_token, state=state, code="code", now=NOW)
    assert rt.provider.complete_calls == 1


def test_legacy_resolver_cannot_omit_external_provenance_validation(auth_runtime):
    rt = auth_runtime
    started, _, _ = authorize(rt)
    login = bind(rt, started)
    legacy = LegacyAuth(rt.factory, secret=rt.settings.web_auth_secret)
    with pytest.raises(LegacyError) as rejected:
        legacy.resolve_session(login.raw_session_token, now=NOW)
    assert rejected.value.code == "session_invalid"


def test_email_cache_refuses_external_projection_even_when_otherwise_well_formed(auth_runtime):
    rt = auth_runtime
    started, _, _ = authorize(rt)
    login = bind(rt, started)

    class ForbiddenRedis:
        def __getattr__(self, name):
            raise AssertionError("provider session cannot be stored in Redis")

    RedisSessionCache(ForbiddenRedis()).put(_token_hash(login.raw_session_token), login.session, now=NOW)
