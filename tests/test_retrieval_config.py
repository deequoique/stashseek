"""Retrieval fusion settings: defaults, bounds and the FusionConfig projection."""

import pytest

from app.config import Settings
from app.retrieval.fusion import FusionConfig

RETRIEVAL_ENV_NAMES = (
    "RETRIEVAL_FUSION",
    "RETRIEVAL_RRF_K",
    "RETRIEVAL_LEXICAL_WEIGHT",
    "RETRIEVAL_VECTOR_WEIGHT",
    "RETRIEVAL_CANDIDATE_POOL_LIMIT",
    "RETRIEVAL_PER_ITEM_CAP",
)


@pytest.fixture(autouse=True)
def clean_retrieval_env(monkeypatch):
    for name in RETRIEVAL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    # Settings() only builds a URL string; nothing here connects to a database.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/unused")


def test_defaults_match_the_documented_table():
    settings = Settings()

    assert settings.retrieval_fusion == "rrf"
    assert settings.retrieval_rrf_k == 60
    assert settings.retrieval_lexical_weight == 1.0
    assert settings.retrieval_vector_weight == 1.0
    assert settings.retrieval_candidate_pool_limit == 50
    assert settings.retrieval_per_item_cap == 3
    assert settings.retrieval_fusion_config() == FusionConfig()


def test_every_variable_is_read_and_projected_one_to_one(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_FUSION", "raw")
    monkeypatch.setenv("RETRIEVAL_RRF_K", "30")
    monkeypatch.setenv("RETRIEVAL_LEXICAL_WEIGHT", "0.5")
    monkeypatch.setenv("RETRIEVAL_VECTOR_WEIGHT", "2")
    monkeypatch.setenv("RETRIEVAL_CANDIDATE_POOL_LIMIT", "80")
    monkeypatch.setenv("RETRIEVAL_PER_ITEM_CAP", "5")

    settings = Settings()
    config = settings.retrieval_fusion_config()

    assert (
        settings.retrieval_fusion,
        settings.retrieval_rrf_k,
        settings.retrieval_lexical_weight,
        settings.retrieval_vector_weight,
        settings.retrieval_candidate_pool_limit,
        settings.retrieval_per_item_cap,
    ) == ("raw", 30, 0.5, 2.0, 80, 5)
    assert config == FusionConfig(
        method="raw",
        rrf_k=30,
        lexical_weight=0.5,
        vector_weight=2.0,
        candidate_pool_limit=80,
        per_item_cap=5,
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("RETRIEVAL_RRF_K", "1"),
        ("RETRIEVAL_RRF_K", "1000"),
        ("RETRIEVAL_LEXICAL_WEIGHT", "0"),
        ("RETRIEVAL_VECTOR_WEIGHT", "0"),
        ("RETRIEVAL_LEXICAL_WEIGHT", "10"),
        ("RETRIEVAL_CANDIDATE_POOL_LIMIT", "10"),
        ("RETRIEVAL_CANDIDATE_POOL_LIMIT", "200"),
        ("RETRIEVAL_PER_ITEM_CAP", "1"),
        ("RETRIEVAL_PER_ITEM_CAP", "10"),
    ],
)
def test_range_edges_are_accepted(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    assert Settings().retrieval_fusion_config() is not None


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("RETRIEVAL_FUSION", "bm25"),
        ("RETRIEVAL_FUSION", "RRF "),
        ("RETRIEVAL_RRF_K", "0"),
        ("RETRIEVAL_RRF_K", "-5"),
        ("RETRIEVAL_RRF_K", "1001"),
        ("RETRIEVAL_LEXICAL_WEIGHT", "-0.1"),
        ("RETRIEVAL_LEXICAL_WEIGHT", "10.5"),
        ("RETRIEVAL_LEXICAL_WEIGHT", "nan"),
        ("RETRIEVAL_VECTOR_WEIGHT", "-1"),
        ("RETRIEVAL_VECTOR_WEIGHT", "11"),
        ("RETRIEVAL_VECTOR_WEIGHT", "inf"),
        ("RETRIEVAL_CANDIDATE_POOL_LIMIT", "9"),
        ("RETRIEVAL_CANDIDATE_POOL_LIMIT", "201"),
        ("RETRIEVAL_PER_ITEM_CAP", "0"),
        ("RETRIEVAL_PER_ITEM_CAP", "11"),
    ],
)
def test_out_of_range_values_fail_settings_construction(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match="RETRIEVAL_"):
        Settings()


def test_method_is_trimmed_and_an_empty_value_means_the_default(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_FUSION", " raw ")
    assert Settings().retrieval_fusion == "raw"

    monkeypatch.setenv("RETRIEVAL_FUSION", "")
    assert Settings().retrieval_fusion == "rrf"


def test_both_weights_zero_fails_settings_construction(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_LEXICAL_WEIGHT", "0")
    monkeypatch.setenv("RETRIEVAL_VECTOR_WEIGHT", "0")

    with pytest.raises(ValueError, match="must not both be 0"):
        Settings()


def test_non_numeric_values_fail_settings_construction(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_RRF_K", "sixty")

    with pytest.raises(ValueError):
        Settings()
