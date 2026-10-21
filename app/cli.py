"""Operator and local Agent command-line entry points."""

import argparse
import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import inspect, select

from app.bootstrap import build_channel_service, build_embedding_provider
from app.channels.http_gateway import serve as serve_channel_gateway
from app.channels.types import ChannelEnvelope
from app.config import get_settings
from app.diagnostics import configure_runtime_logging
from app.db import get_session_factory, session
from app.ingest.asr_backfill import (
    DEFAULT_BACKFILL_LIMIT,
    MAX_BACKFILL_LIMIT,
    backfill_needs_asr,
)
from app.ingest.reindex import reindex
from app.ingest.tasks import ingest_url
from app.models import AppUser, ChannelIdentity, ExternalLoginIdentity
from app.retrieval.search import bm25_search, vector_search
from app.mcp_grants import McpGrantError, McpGrantService
from app.web_auth import revoke_web_sessions


def _print(name, hits):
    print(f"\n{name}")
    for rank, hit in enumerate(hits, 1):
        print(f"{rank:>2}. {hit.title or hit.platform_id} | {hit.url} | {hit.score:.4f}\n    {hit.text[:240]}")


def _backfill_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None
    if not 1 <= limit <= MAX_BACKFILL_LIMIT:
        raise argparse.ArgumentTypeError(
            f"must be between 1 and {MAX_BACKFILL_LIMIT}"
        )
    return limit


def main() -> None:
    parser = argparse.ArgumentParser(prog="stashseek")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest")
    ingest.add_argument("url")
    ingest.add_argument("--user-id", type=int, required=True)
    ingest.add_argument("--why-saved")
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--user-id", type=int, required=True)
    search.add_argument("-k", type=int, default=10)
    ask = commands.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--user-id", type=int, required=True)
    ask.add_argument("--thread", default="default")
    users = commands.add_parser("users")
    user_commands = users.add_subparsers(dest="user_command", required=True)
    user_commands.add_parser("create")
    for command in ("show", "disable", "enable"):
        child = user_commands.add_parser(command)
        child.add_argument("--user-id", type=int, required=True)
    rebind = user_commands.add_parser("rebind-identity")
    rebind.add_argument("--identity-id", type=int, required=True)
    rebind.add_argument("--user-id", type=int, required=True)
    asr_backfill = commands.add_parser(
        "asr-backfill",
        help="re-dispatch public needs_asr items through ASR (ASR_ENABLED=true)",
    )
    asr_backfill.add_argument(
        "--limit", type=_backfill_limit, default=DEFAULT_BACKFILL_LIMIT
    )
    asr_backfill.add_argument("--user-id", type=int)
    asr_backfill.add_argument("--dry-run", action="store_true")
    reindex_parser = commands.add_parser(
        "reindex",
        help="backfill Chinese fts and per-item title rows (omit both flags to run both)",
    )
    reindex_parser.add_argument("--user-id", type=int)
    reindex_parser.add_argument("--dry-run", action="store_true")
    reindex_parser.add_argument("--zh-fts", action="store_true")
    reindex_parser.add_argument("--title-rows", action="store_true")
    commands.add_parser("gateway-server")
    mcp_server = commands.add_parser("mcp-server")
    mcp_server.add_argument(
        "--transport", choices=("stdio", "streamable-http"), default="stdio"
    )
    grants = commands.add_parser("mcp-grant", aliases=["mcp-grants"])
    grant_commands = grants.add_subparsers(dest="grant_command", required=True)
    issue = grant_commands.add_parser("issue")
    issue.add_argument("--user-id", type=int, required=True)
    issue.add_argument("--scope", choices=("read", "full"), default="read")
    issue.add_argument("--expires-at")
    issue.add_argument("--label")
    issue.add_argument("--created-by")
    list_grants = grant_commands.add_parser("list")
    list_grants.add_argument("--user-id", type=int)
    list_grants.add_argument("--limit", type=int, default=100)
    list_grants.add_argument("--offset", type=int, default=0)
    show_grant = grant_commands.add_parser("show")
    show_grant.add_argument("grant_id")
    rotate = grant_commands.add_parser("rotate")
    rotate.add_argument("grant_id")
    rotate.add_argument("--expires-at")
    revoke = grant_commands.add_parser("revoke")
    revoke.add_argument("grant_id")
    disable = grant_commands.add_parser("disable")
    disable.add_argument("grant_id")
    commands.add_parser("web-server")
    args = parser.parse_args()
    if args.command == "users":
        _users(args)
        return
    if args.command == "ingest":
        item_id, state = ingest_url(
            args.url, user_id=args.user_id, why_saved=args.why_saved
        )
        print(f"item={item_id} state={state}")
        return
    if args.command in {"mcp-grant", "mcp-grants"}:
        _mcp_grants(args)
        return
    if args.command == "asr-backfill":
        _asr_backfill(args)
        return
    if args.command == "reindex":
        _reindex(args)
        return
    settings = get_settings()
    logging_options = dict(
        log_dir=getattr(
            settings,
            "stashseek_log_dir",
            getattr(settings, "notebook_agent_log_dir", ".runtime/logs"),
        ),
        max_bytes=getattr(
            settings,
            "stashseek_log_max_bytes",
            getattr(settings, "notebook_agent_log_max_bytes", 10 * 1024 * 1024),
        ),
        backup_count=getattr(
            settings,
            "stashseek_log_backup_count",
            getattr(settings, "notebook_agent_log_backup_count", 5),
        ),
    )
    if args.command == "mcp-server" and args.transport == "stdio":
        logging_options["console_stream"] = "stderr"
    configure_runtime_logging(**logging_options)
    if args.command == "mcp-server":
        from app.mcp_server import run_stdio, run_streamable_http

        if args.transport == "stdio":
            run_stdio(settings=settings)
        else:
            run_streamable_http(settings=settings)
        return
    if args.command == "gateway-server":
        serve_channel_gateway(settings)
        return
    if args.command == "web-server":
        import uvicorn

        from app.api.runtime import build_api_app

        uvicorn.run(
            build_api_app(settings),
            host=settings.web_host,
            port=settings.web_port,
            proxy_headers=True,
            forwarded_allow_ips=settings.web_forwarded_allow_ips,
            access_log=False,
        )
        return
    if args.command == "ask":
        asyncio.run(_ask(args, settings))
        return
    embedder = build_embedding_provider(settings)
    if embedder is None:
        raise SystemExit("embedding provider is not configured")
    with session() as db:
        lexical = bm25_search(db, args.query, user_id=args.user_id, k=args.k)
        vector = vector_search(
            db, embedder.embed([args.query])[0], user_id=args.user_id, k=args.k
        )
    _print("BM25 / trigram", lexical)
    _print("Vector", vector)


def _asr_backfill(args) -> None:
    """Queue existing ``needs_asr`` items; print only counts and item IDs."""

    if not get_settings().asr_enabled:
        raise SystemExit("ASR is disabled; set ASR_ENABLED=true to backfill")
    result = backfill_needs_asr(
        limit=args.limit, user_id=args.user_id, dry_run=args.dry_run
    )
    print(
        f"selected={len(result.selected)} queued={len(result.queued)} "
        f"skipped={len(result.skipped)} failed={len(result.failed)} "
        f"dry_run={str(result.dry_run).lower()}"
    )
    labels = {
        **{item_id: "selected" for item_id in result.selected},
        **{item_id: "queued" for item_id in result.queued},
        **{item_id: "skipped" for item_id in result.skipped},
        **{item_id: "failed" for item_id in result.failed},
    }
    for item_id in result.selected:
        print(f"item={item_id} {labels[item_id]}")
    if result.failed:
        raise SystemExit(2)


def _reindex(args) -> None:
    """Run the reindex jobs; print only counts and failed item IDs."""

    # Neither flag selects everything; either flag narrows to just that job.
    both = not args.zh_fts and not args.title_rows
    result = reindex(
        user_id=args.user_id,
        dry_run=args.dry_run,
        zh_fts=args.zh_fts or both,
        title_rows=args.title_rows or both,
    )
    print(
        f"zh_segments_updated={result.zh_segments_updated} "
        f"title_rows_inserted={result.title_rows_inserted} "
        f"title_rows_updated={result.title_rows_updated} "
        f"title_rows_skipped={result.title_rows_skipped} "
        f"failed={len(result.failed_item_ids)} "
        f"dry_run={str(result.dry_run).lower()}"
    )
    for item_id in result.failed_item_ids:
        print(f"item={item_id} failed")
    if result.failed_item_ids:
        raise SystemExit(2)


def _users(args) -> None:
    factory = get_session_factory()
    with factory() as db:
        if args.user_command == "create":
            user = AppUser()
            db.add(user)
            db.commit()
            print(f"user={user.id}")
            return
        if args.user_command == "rebind-identity":
            user = db.scalar(select(AppUser).where(AppUser.id == args.user_id).with_for_update())
            identity = db.scalar(select(ChannelIdentity).where(ChannelIdentity.id == args.identity_id).with_for_update())
            if user is None:
                raise SystemExit(f"app user {args.user_id} not found")
            if identity is None:
                raise SystemExit(f"channel identity {args.identity_id} not found")
            if (
                identity.app_user_id != user.id
                and inspect(db.connection()).has_table("external_login_identity")
                and db.scalar(select(ExternalLoginIdentity.id).where(
                    ExternalLoginIdentity.channel_identity_id == identity.id,
                ).limit(1)) is not None
            ):
                raise SystemExit("identity has external login bindings; use the supported account-linking flow")
            identity.app_user_id = user.id
            db.commit()
            print(f"identity={identity.id} user={user.id}")
            return
        user = db.get(AppUser, args.user_id)
        if user is None:
            raise SystemExit(f"app user {args.user_id} not found")
        if args.user_command == "disable":
            user.disabled_at = datetime.now(UTC)
            revoke_web_sessions(db, user.id)
            db.commit()
        elif args.user_command == "enable":
            user.disabled_at = None
            db.commit()
        state = "disabled" if user.disabled_at else "active"
        print(f"user={user.id} state={state}")


def _parse_expiry(value: str | None):
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit("expiry must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise SystemExit("expiry must include a timezone")
    return parsed


def _mcp_grants(args) -> None:
    service = McpGrantService(get_session_factory())
    try:
        if args.grant_command == "issue":
            issued = service.issue(
                args.user_id,
                scope=args.scope,
                expires_at=_parse_expiry(args.expires_at),
                label=args.label,
                created_by=args.created_by,
            )
            # Raw bearer material is intentionally printed only on issue and
            # rotate.  It is never included by list/show metadata commands.
            print(f"grant_id={issued.grant_id}")
            print(f"scope={issued.metadata.scope}")
            print(f"token={issued.raw_token}")
            return
        if args.grant_command == "list":
            for grant in service.list(
                app_user_id=args.user_id,
                limit=getattr(args, "limit", 100),
                offset=getattr(args, "offset", 0),
            ):
                values = grant.model_dump()
                values = {
                    key: value.isoformat() if isinstance(value, datetime) else value
                    for key, value in values.items()
                    if key not in {"last_used_at"}
                }
                print(" ".join(f"{key}={value}" for key, value in values.items()))
            return
        if args.grant_command == "show":
            values = service.get(args.grant_id).model_dump()
            print(" ".join(
                f"{key}={value.isoformat() if isinstance(value, datetime) else value}"
                for key, value in values.items()
                if key != "last_used_at"
            ))
            return
        if args.grant_command == "rotate":
            issued = service.rotate(args.grant_id, expires_at=_parse_expiry(args.expires_at))
            print(f"grant_id={issued.grant_id}")
            print(f"scope={issued.metadata.scope}")
            print(f"token={issued.raw_token}")
            return
        if args.grant_command == "revoke":
            metadata = service.revoke(args.grant_id)
            print(f"grant_id={metadata.grant_id} revoked_at={metadata.revoked_at}")
            return
        if args.grant_command == "disable":
            metadata = service.disable(args.grant_id)
            print(f"grant_id={metadata.grant_id} disabled_at={metadata.disabled_at}")
            return
    except McpGrantError as exc:
        raise SystemExit(exc.error_code) from None


async def _ask(args, settings) -> None:
    factory = get_session_factory()
    _ensure_cli_identity(factory, args.user_id)
    service = build_channel_service(settings)
    envelope = ChannelEnvelope(
        channel="cli",
        account_id="local",
        external_user_id=str(args.user_id),
        conversation_id=args.thread,
        message_id=str(uuid.uuid4()),
        text=args.question,
    )
    answer = await service.handle(envelope)
    print(answer.text)
    if answer.status == "failed":
        raise SystemExit(2)


def _ensure_cli_identity(factory, user_id: int) -> None:
    with factory() as db:
        user = db.get(AppUser, user_id)
        if user is None:
            raise SystemExit(f"app user {user_id} not found")
        if user.disabled_at is not None:
            raise SystemExit(f"app user {user_id} is disabled")
        identity = db.scalar(
            select(ChannelIdentity).where(
                ChannelIdentity.channel == "cli",
                ChannelIdentity.account_id == "local",
                ChannelIdentity.external_user_id == str(user_id),
            )
        )
        if identity is None:
            db.add(
                ChannelIdentity(
                    app_user_id=user_id,
                    channel="cli",
                    account_id="local",
                    external_user_id=str(user_id),
                )
            )
            db.commit()
        elif identity.app_user_id != user_id:
            raise SystemExit("CLI identity is already bound to another user")


if __name__ == "__main__":
    main()
