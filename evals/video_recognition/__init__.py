"""Isolated, provider-neutral video recognition benchmark harness.

The benchmark deliberately lives outside :mod:`app`.  It owns frozen sample
contracts, protocol adapters, deterministic scoring, and a no-provider dry run
so that benchmark work cannot mutate production ingestion or search state.
"""

from .schema import (
    BENCHMARK_REVISION,
    BenchmarkCatalog,
    BenchmarkGateError,
    CatalogValidationError,
    load_catalog,
    load_probe_snapshot,
)
from .events import (
    EvaluationEvent,
    EvaluationEventBatch,
    EvaluationScore,
    EvaluationTrace,
    EventInputSummary,
    EventPredictionSummary,
    EventTimeRange,
    build_evaluation_event,
    build_evaluation_event_batch,
)
from .quality_report import (
    AutomaticQualityReport,
    InputBounds,
    LatencySummary,
    ModalityQuality,
    UsageSummary,
    WeakReference,
    WeakReferenceCue,
    automatic_quality_report,
    build_auto_quality_report,
    build_quality_report,
)
from .sampling import (
    RiskAssessment,
    RiskSignals,
    ReviewCandidate,
    ReviewFeedback,
    SamplingPlan,
    SamplingPolicy,
    apply_review_feedback,
    assess_risk,
    compute_risk_score,
    select_review_samples,
)

__all__ = [
    "BENCHMARK_REVISION",
    "BenchmarkCatalog",
    "BenchmarkGateError",
    "CatalogValidationError",
    "load_catalog",
    "load_probe_snapshot",
    "AutomaticQualityReport",
    "EvaluationEvent",
    "EvaluationEventBatch",
    "EvaluationScore",
    "EvaluationTrace",
    "EventInputSummary",
    "EventPredictionSummary",
    "EventTimeRange",
    "InputBounds",
    "LatencySummary",
    "ModalityQuality",
    "RiskAssessment",
    "RiskSignals",
    "ReviewCandidate",
    "ReviewFeedback",
    "SamplingPlan",
    "SamplingPolicy",
    "UsageSummary",
    "WeakReference",
    "WeakReferenceCue",
    "apply_review_feedback",
    "assess_risk",
    "automatic_quality_report",
    "build_auto_quality_report",
    "build_evaluation_event",
    "build_evaluation_event_batch",
    "build_quality_report",
    "compute_risk_score",
    "select_review_samples",
]
