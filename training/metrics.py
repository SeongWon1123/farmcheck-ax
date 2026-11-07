# Copyright (c) 2026 FarmCheck contributors.
"""Pure, full-head metrics for the seven-label same-source diagnostic."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal, override

SUPPORTED: Final = (1, 3, 4, 7, 8, 12, 13)
NUM_CLASSES: Final = 14
type EvaluationIssue = Literal[
    "length",
    "truth",
    "prediction",
    "train",
    "baseline",
    "head",
    "image",
    "features",
    "manifest",
    "counts",
]


@dataclass(frozen=True, slots=True)
class EvaluationError(ValueError):
    """Reject undefined metrics rather than silently changing denominators."""

    reason: EvaluationIssue

    @override
    def __str__(self) -> str:
        messages: dict[EvaluationIssue, str] = {
            "length": "Nonempty equally sized truth/predictions required",
            "truth": "True evaluation labels must be supported",
            "prediction": "Predictions must be 14-head class indices",
            "train": "Training must contain exactly all seven supported labels",
            "baseline": "Baseline training labels must be nonempty and supported",
            "head": "Expected the preserved 14-output linear classifier",
            "image": "Image path/hash differs from the private manifest",
            "features": "Expected 1280 pooled features",
            "manifest": "Manifest labels differ from the declared protocol",
            "counts": "Manifest counts differ from the audited protocol",
        }
        return messages[self.reason]


@dataclass(frozen=True, slots=True)
class WeekMetrics:
    """One supported week's true count and full-population precision/recall."""

    week: int
    count: int
    precision: float
    recall: float


@dataclass(frozen=True, slots=True)
class Metrics:
    """All denominators retain unsupported predictions as errors."""

    count: int
    accuracy: float
    macro_f1: float
    balanced_accuracy: float
    week_mae: float
    unsupported_prediction_fraction: float
    per_week: tuple[WeekMetrics, ...]
    confusion: tuple[tuple[int, ...], ...]


def evaluate(truth: Sequence[int], predictions: Sequence[int]) -> Metrics:
    """Score all 14 output indices against supported true labels."""
    if not truth or len(truth) != len(predictions):
        raise EvaluationError(reason="length")
    if any(week not in SUPPORTED for week in truth):
        raise EvaluationError(reason="truth")
    if any(not 0 <= week < NUM_CLASSES for week in predictions):
        raise EvaluationError(reason="prediction")
    confusion = [[0] * NUM_CLASSES for _ in range(NUM_CLASSES)]
    for actual, predicted in zip(truth, predictions, strict=True):
        confusion[actual][predicted] += 1
    rows: list[WeekMetrics] = []
    f1: list[float] = []
    for week in SUPPORTED:
        count = sum(confusion[week])
        predicted_count = sum(row[week] for row in confusion)
        correct = confusion[week][week]
        precision = correct / predicted_count if predicted_count else 0.0
        recall = correct / count if count else 0.0
        rows.append(WeekMetrics(week, count, precision, recall))
        f1.append(2 * correct / (count + predicted_count) if count + predicted_count else 0.0)
    size = len(truth)
    return Metrics(
        count=size,
        accuracy=sum(confusion[i][i] for i in range(NUM_CLASSES)) / size,
        macro_f1=sum(f1) / len(SUPPORTED),
        balanced_accuracy=sum(row.recall for row in rows) / len(SUPPORTED),
        week_mae=sum(abs(a - p) for a, p in zip(truth, predictions, strict=True)) / size,
        unsupported_prediction_fraction=sum(p not in SUPPORTED for p in predictions) / size,
        per_week=tuple(rows),
        confusion=tuple(tuple(row) for row in confusion),
    )


def class_weights(labels: Sequence[int]) -> tuple[float, ...]:
    """Fit inverse-square-root weights, mean one over the seven train classes."""
    counts = Counter(labels)
    if set(counts) != set(SUPPORTED):
        raise EvaluationError(reason="train")
    inverse = {week: counts[week] ** -0.5 for week in SUPPORTED}
    mean = sum(inverse.values()) / len(SUPPORTED)
    return tuple(inverse[week] / mean if week in inverse else 0.0 for week in range(NUM_CLASSES))


def constant_baselines(labels: Sequence[int]) -> tuple[int, int]:
    """Fit majority (lower-week tie) and lower median using training only."""
    if not labels or any(week not in SUPPORTED for week in labels):
        raise EvaluationError(reason="baseline")
    counts = Counter(labels)
    majority = min(counts, key=lambda week: (-counts[week], week))
    return majority, sorted(labels)[(len(labels) - 1) // 2]


def improves(candidate: Metrics, incumbent: Metrics | None) -> bool:
    """Select trained epochs by macro-F1, then strictly lower ordinal MAE."""
    return incumbent is None or (candidate.macro_f1, -candidate.week_mae) > (
        incumbent.macro_f1,
        -incumbent.week_mae,
    )
