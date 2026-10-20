"""Export the complete browser contract without touching DB, queues, or secrets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.api.app import WebApiServices, create_app
from app.api.conversation_routes import ConversationStreamEvent
from app.api.auth_schemas import (
    ChallengeCreateRequest,
    ChallengeCreateResponse,
    ChallengeStatusResponse,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "extension" / "src" / "api" / "openapi.json"


def rendered_schema() -> str:
    # OpenAPI generation must use the production email composition while
    # remaining completely inert: no database/session factory, broker,
    # object-store, email provider, or network is touched during export.
    placeholder = object()
    app = create_app(
        services=WebApiServices(
            web_auth=placeholder,
            library=placeholder,
            submission=placeholder,
            transcript=placeholder,
            email_auth=placeholder,
            external_auth=placeholder,
            browser_companion=placeholder,
            browser_capture_submission=placeholder,
            extension_sessions=placeholder,
            bilibili=placeholder,
        ),
        expected_origin="https://contract.invalid",
        cookie_secure=True,
        publish_budget_seconds=1.0,
        web_login_channels=("email",),
    )
    document = app.openapi()
    # Python's standard HTTP 422 phrase differs across supported versions.
    # Keep the existing generated contract stable without changing custom text.
    for path in document["paths"].values():
        for operation in path.values():
            if not isinstance(operation, dict):
                continue
            response = operation.get("responses", {}).get("422")
            if isinstance(response, dict) and response.get("description") in {
                "Unprocessable Entity", "Unprocessable Content",
            }:
                response["description"] = "Unprocessable Entity"
    # Keep compatibility request schemas available for channel integrations
    # without treating them as the extension workspace's login contract.  The
    # legacy channel UI remains an explicitly non-production compatibility path
    # for embedders that still advertise Telegram/WeChat; keep its generated
    # request aliases without mounting those routes in the canonical document.
    schemas = document["components"]["schemas"]
    for model in (
        ChallengeCreateRequest,
        ChallengeCreateResponse,
        ChallengeStatusResponse,
        ConversationStreamEvent,
    ):
        schema = model.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
        schemas.update(schema.pop("$defs", {}))
        schemas[model.__name__] = schema
    return json.dumps(
        document,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = rendered_schema()
    if args.check:
        actual = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if actual != expected:
            raise SystemExit("extension/src/api/openapi.json is stale; regenerate it")
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(expected, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
