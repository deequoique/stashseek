"""The browser contract must be reproducible across supported Python versions."""

import http.client
import json

from scripts import export_web_openapi


def test_export_is_identical_across_standard_http_422_phrases(monkeypatch):
    monkeypatch.setitem(http.client.responses, 422, "Unprocessable Entity")
    original = export_web_openapi.rendered_schema()
    monkeypatch.setitem(http.client.responses, 422, "Unprocessable Content")
    assert export_web_openapi.rendered_schema() == original


def test_export_preserves_custom_validation_description(monkeypatch):
    create_app = export_web_openapi.create_app

    def with_custom_description(**kwargs):
        app = create_app(**kwargs)
        app.get("/export-probe", responses={422: {"description": "Keep this custom hint"}})(lambda: None)
        return app

    monkeypatch.setattr(export_web_openapi, "create_app", with_custom_description)
    document = json.loads(export_web_openapi.rendered_schema())
    assert document["paths"]["/export-probe"]["get"]["responses"]["422"]["description"] == "Keep this custom hint"
