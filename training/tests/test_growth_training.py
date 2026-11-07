# Copyright (c) 2026 FarmCheck contributors.
"""Deterministic synthetic checks without private photos or platform weights."""

from collections import OrderedDict
from math import isclose
from pathlib import Path

import pytest
import torch
from PIL import Image
from torch import nn

from training.metrics import (
    SUPPORTED,
    EvaluationError,
    class_weights,
    constant_baselines,
    evaluate,
    improves,
)
from training.train_growth_week import (
    FeatureSplit,
    Preprocessing,
    backpropagate,
    fit_head,
    image_tensor,
    seed_everything,
    split_model,
    tensor_forward,
)


def test_backend_policy_when_tf32_was_enabled() -> None:
    """Restore the verified float32 policy before any feature or head computation."""
    # Given
    matmul = torch.backends.cuda.matmul.allow_tf32
    cudnn = torch.backends.cudnn.allow_tf32
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    try:
        # When
        seed_everything()
        # Then
        torch.testing.assert_close(torch.backends.cuda.matmul.allow_tf32, expected=False)
        torch.testing.assert_close(torch.backends.cudnn.allow_tf32, expected=False)
    finally:
        torch.backends.cuda.matmul.allow_tf32 = matmul
        torch.backends.cudnn.allow_tf32 = cudnn


def test_metrics_when_predictions_include_unsupported_classes() -> None:
    """Keep unsupported outputs in every denominator and all 14 confusion rows."""
    # Given
    truth = (1, 1, 3, 3)
    # When
    metrics = evaluate(truth, (1, 2, 1, 3))
    # Then
    torch.testing.assert_close(metrics.accuracy, 0.5)
    torch.testing.assert_close(metrics.week_mae, 0.75)
    torch.testing.assert_close(metrics.unsupported_prediction_fraction, 0.25)
    torch.testing.assert_close(isclose(metrics.balanced_accuracy, 1 / 7), expected=True)
    torch.testing.assert_close(isclose(metrics.macro_f1, (0.5 + 2 / 3) / 7), expected=True)
    torch.testing.assert_close(metrics.per_week[0].count, 2)
    torch.testing.assert_close(metrics.per_week[0].precision, 0.5)
    torch.testing.assert_close(metrics.per_week[0].recall, 0.5)
    torch.testing.assert_close(len(metrics.confusion), 14)
    torch.testing.assert_close(tuple(map(len, metrics.confusion)), (14,) * 14)
    torch.testing.assert_close(metrics.confusion[1][2], 1)
    torch.testing.assert_close(sum(map(sum, metrics.confusion)), len(truth))


def test_class_weights_when_training_frequencies_are_unequal() -> None:
    """Normalize over supported classes, with no unseen-label loss weight."""
    # Given
    labels = (1,) * 4 + SUPPORTED[1:]
    # When
    weights = class_weights(labels)
    # Then
    torch.testing.assert_close(isclose(sum(weights), 7), expected=True)
    torch.testing.assert_close(weights[1] * 2, weights[3])
    torch.testing.assert_close(weights[2], 0.0)


def test_class_weights_when_a_supported_class_is_missing() -> None:
    """Reject a malformed training partition rather than divide by zero."""
    # Given
    labels = SUPPORTED[:-1]
    # When / Then
    with pytest.raises(EvaluationError):
        _ = class_weights(labels)


def test_baselines_when_train_has_a_majority_tie() -> None:
    """Fit train-only deterministic majority and lower median."""
    # Given
    labels = (13, 1, 13, 1)
    # When
    fitted = constant_baselines(labels)
    # Then
    torch.testing.assert_close(fitted, (1, 1))


def test_selection_when_f1_ties_uses_lower_mae() -> None:
    """Never substitute an untrained baseline for the first trained epoch."""
    # Given
    worse = evaluate((1,), (13,))
    better = evaluate((1,), (3,))
    # When
    decisions = (
        improves(worse, None),
        improves(better, worse),
        improves(worse, better),
        improves(better, better),
    )
    # Then
    torch.testing.assert_close(decisions, (True, True, False, False))


def test_head_learning_when_cpu_features_are_fixed() -> None:
    """Reproduce actual learning, changed parameters and selected-epoch scores."""
    # Given
    old_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    features = torch.eye(7).repeat(8, 1) * 10
    split = FeatureSplit(features, SUPPORTED * 8)
    original = nn.Linear(7, 14)
    with torch.no_grad():
        _ = original.weight.zero_()
        for parameter in original.parameters():
            _ = parameter.zero_()
    # When
    try:
        first = fit_head(original, split, split, "cpu")
        second = fit_head(original, split, split, "cpu")
    finally:
        torch.set_num_threads(old_threads)
    # Then
    torch.testing.assert_close(first.head_changed, expected=True)
    torch.testing.assert_close(first.selected_epoch >= 1, expected=True)
    torch.testing.assert_close(first.history == second.history, expected=True)
    torch.testing.assert_close(first.head.weight, second.head.weight, rtol=0, atol=0)
    torch.testing.assert_close(first.history[first.selected_epoch - 1].validation.accuracy, 1.0)
    torch.testing.assert_close(first.history[-1].epoch, first.selected_epoch + 5)
    torch.testing.assert_close(torch.count_nonzero(original.weight).item(), 0)


def test_preprocessing_when_rgb_unit_pixels_are_known(tmp_path: Path) -> None:
    """Apply channel-first float32 unit input without changing original bytes."""
    # Given
    path = tmp_path / "pixel.png"
    Image.new("RGB", (2, 2), (255, 128, 0)).save(path)
    before = path.read_bytes()
    config = Preprocessing(size=2, interpolation="bilinear", order="rgb", normalization="unit")
    # When
    pixels = image_tensor(path, config)
    # Then
    torch.testing.assert_close(pixels.dtype == torch.float32, expected=True)
    torch.testing.assert_close(tuple(pixels.shape), (3, 2, 2))
    torch.testing.assert_close(pixels[:, 0, 0], torch.tensor([1.0, 128 / 255, 0.0]))
    torch.testing.assert_close(path.read_bytes() == before, expected=True)


def test_feature_path_when_model_has_explicit_platform_modules() -> None:
    """The frozen eval body plus linear head equals the full eval model."""
    # Given
    model = nn.Sequential(
        OrderedDict(
            conv_stem=nn.Conv2d(3, 4, 1),
            bn1=nn.BatchNorm2d(4),
            blocks=nn.Identity(),
            conv_head=nn.Conv2d(4, 1280, 1),
            bn2=nn.BatchNorm2d(1280),
            global_pool=nn.Sequential(nn.AdaptiveAvgPool2d(1), nn.Flatten()),
            classifier=nn.Linear(1280, 14),
        )
    )
    pixels = torch.ones(2, 3, 2, 2)
    # When
    body, head = split_model(model)
    with torch.no_grad():
        features = tensor_forward(body, pixels)
        expected = tensor_forward(model, pixels)
    actual = tensor_forward(head, features)
    backpropagate(actual.sum())
    # Then
    torch.testing.assert_close(actual, expected)
    torch.testing.assert_close(body.training, expected=False)
    torch.testing.assert_close(any(module.training for module in body.modules()), expected=False)
    torch.testing.assert_close(
        any(parameter.requires_grad for parameter in body.parameters()),
        expected=False,
    )
    torch.testing.assert_close(head.weight.grad is not None, expected=True)
