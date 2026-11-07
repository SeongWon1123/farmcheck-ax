# Copyright (c) 2026 FarmCheck contributors.
"""Run classifier-only adaptation: python -m training.train_growth_week --help."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import sys
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, ClassVar, Final, Literal, Protocol, assert_never

import numpy as np
import torch
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field
from torch import Tensor, nn
from torch.nn import functional

from farmcheck.training_manifest import ManifestRecord, TrainingManifest
from training.metrics import (
    NUM_CLASSES,
    SUPPORTED,
    EvaluationError,
    Metrics,
    class_weights,
    constant_baselines,
    evaluate,
    improves,
)
from training.platform_model import export_platform_weights, load_platform_model

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

SEED: Final = 20261107
BATCH_SIZE: Final = 64
MAX_EPOCHS: Final = 20
PATIENCE: Final = 5
FEATURE_WIDTH: Final = 1280
FEATURE_DIMENSIONS: Final = 2
LIMITS: Final = (
    "Same-source capture-day diagnostic, not independent accuracy or farm effectiveness. "
    "Checkpoint pretraining overlap is UNKNOWN (same AI Hub 71853 source). "
    "Plant/pot/cycle identifiers are unavailable; background/view/photographer may correlate "
    "with week. Weeks 9-11 are absent. Only seven labels are evaluated; the 14-head "
    "architecture is preserved. Small early-week counts and six test capture days limit "
    "uncertainty claims; image count is not independent sample size. "
    "Verified serving preprocessing parity concerns original 380x380 images only; "
    "interpolation was not identified and parity is not claimed for resized/non380 inputs. "
    "No improvement claim is justified without these measurements."
)


class Preprocessing(BaseModel):
    """Require an explicit receipt-matched pixel transform, with no defaults."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid", strict=True)
    size: Annotated[int, Field(gt=0)]
    interpolation: Literal["nearest", "bilinear", "bicubic", "lanczos"]
    order: Literal["rgb", "bgr"]
    normalization: Literal["raw", "unit", "signed", "imagenet"]


class Arguments(BaseModel):
    """Parse argparse's external namespace into concrete immutable values."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    manifest: Path
    data_root: Path
    weights: Path
    preprocessing_config: Path
    output: Path
    device: Literal["cpu", "cuda"]


@dataclass(frozen=True, slots=True)
class FeatureSplit:
    """CPU float32 features retain provenance outside the model inputs."""

    features: Tensor
    labels: tuple[int, ...]
    records: tuple[ManifestRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class Epoch:
    """A trained epoch's validation result, never the original checkpoint."""

    epoch: int
    loss: float
    validation: Metrics


@dataclass(frozen=True, slots=True)
class FitResult:
    """Selected linear head plus the complete trained-epoch history."""

    head: nn.Linear
    history: tuple[Epoch, ...]
    selected_epoch: int
    head_changed: bool


class TensorCall(Protocol):
    """The tensor-only call contract of the verified feature body and head."""

    def __call__(self, inputs: Tensor, /) -> Tensor:
        """Return the module's tensor output."""
        ...


class Backward(Protocol):
    """The no-argument scalar backpropagation operation used here."""

    def backward(self) -> None:
        """Backpropagate a scalar with its implicit unit gradient."""
        ...


class OptimizerStep(Protocol):
    """The no-closure AdamW operation used by classifier fitting."""

    def step(self) -> Tensor | None:
        """Update parameters without a closure."""
        ...


def tensor_forward(module: TensorCall, inputs: Tensor) -> Tensor:
    """Keep torch's dynamic Module call behind the verified tensor contract."""
    return module(inputs)


def backpropagate(loss: Backward) -> None:
    """Differentiate a scalar loss using torch's default gradient."""
    loss.backward()


def optimizer_step(optimizer: OptimizerStep) -> None:
    """Run AdamW without its optional dynamic closure."""
    _ = optimizer.step()


def tensor_state(model: nn.Module) -> Mapping[str, Tensor]:
    """Request the typed tensor destination supported by torch state_dict."""
    destination: dict[str, Tensor] = {}
    _ = model.state_dict(destination=destination)
    return destination


def seed_everything() -> None:
    """Fix CPU/CUDA RNGs and reject nondeterministic torch operations."""
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    random.seed(SEED)
    torch.set_rng_state(torch.Generator().manual_seed(SEED).get_state())
    torch.cuda.manual_seed_all(SEED)
    torch.use_deterministic_algorithms(mode=True)
    torch.backends.cudnn.benchmark = False
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False


def progress(event: str, **fields: str | float) -> None:
    """Flush one JSON event so preparation cannot be mistaken for training."""
    _ = sys.stdout.write(json.dumps({"event": event, **fields}) + "\n")
    _ = sys.stdout.flush()


def image_tensor(path: Path, config: Preprocessing) -> Tensor:
    """Read unchanged bytes, RGB-resize, reorder channels and normalize."""
    filters = {
        "nearest": Image.Resampling.NEAREST,
        "bilinear": Image.Resampling.BILINEAR,
        "bicubic": Image.Resampling.BICUBIC,
        "lanczos": Image.Resampling.LANCZOS,
    }
    with Image.open(path) as image:
        pixels = np.array(
            image.convert("RGB").resize(
                (config.size, config.size),
                resample=filters[config.interpolation],
            ),
            dtype=np.float32,
        )
    tensor = torch.tensor(pixels).permute(2, 0, 1)
    match config.order:
        case "rgb":
            pass
        case "bgr":
            tensor = tensor.flip(0)
        case _:
            assert_never(config.order)
    match config.normalization:
        case "raw":
            return tensor
        case "unit":
            return tensor / 255.0
        case "signed":
            return tensor / 127.5 - 1.0
        case "imagenet":
            mean = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
            std = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
            if config.order == "bgr":
                mean, std = mean.flip(0), std.flip(0)
            return (tensor / 255.0 - mean) / std
        case _:
            assert_never(config.normalization)


def split_model(model: nn.Module) -> tuple[nn.Sequential, nn.Linear]:
    """Freeze the explicit pooled-feature path, including eval-mode BN."""
    children = dict(model.named_children())
    head = children["classifier"]
    if not isinstance(head, nn.Linear) or head.out_features != NUM_CLASSES:
        raise EvaluationError(reason="head")
    body = nn.Sequential(
        *(
            children[name]
            for name in (
                "conv_stem",
                "bn1",
                "blocks",
                "conv_head",
                "bn2",
                "global_pool",
            )
        )
    )
    _ = model.eval()
    _ = body.eval()
    for parameter in body.parameters():
        parameter.requires_grad = False
    return body, head


def extract_features(
    model: nn.Module,
    records: tuple[ManifestRecord, ...],
    root: Path,
    config: Preprocessing,
    device: str,
) -> FeatureSplit:
    """Extract deterministic no-grad features and verify decomposition parity."""
    body, head = split_model(model)
    features: list[Tensor] = []
    progress("feature_extraction_started", split=records[0].split, total=len(records))
    with torch.no_grad():
        for start in range(0, len(records), BATCH_SIZE):
            batch = records[start : start + BATCH_SIZE]
            images: list[Tensor] = []
            for record in batch:
                path = (root / record.image_file).resolve()
                if not path.is_relative_to(root.resolve()) or sha256(path) != record.image_sha256:
                    raise EvaluationError(reason="image")
                images.append(image_tensor(path, config))
            pixels = torch.stack(images).to(device)
            pooled = tensor_forward(body, pixels)
            if pooled.ndim != FEATURE_DIMENSIONS or pooled.shape[1] != FEATURE_WIDTH:
                raise EvaluationError(reason="features")
            if start == 0:
                torch.testing.assert_close(
                    tensor_forward(head, pooled),
                    tensor_forward(model, pixels),
                    rtol=1e-5,
                    atol=1e-6,
                )
            features.append(pooled.cpu())
            progress(
                "feature_batch_completed",
                split=records[0].split,
                completed=start + len(batch),
                total=len(records),
            )
    progress("feature_extraction_completed", split=records[0].split, total=len(records))
    return FeatureSplit(torch.cat(features), tuple(r.week for r in records), records)


def predict(head: nn.Linear, split: FeatureSplit, device: str) -> tuple[int, ...]:
    """Predict all 14 classes, without filtering unsupported outputs."""
    predictions: list[int] = []
    with torch.no_grad():
        for start in range(0, len(split.labels), BATCH_SIZE):
            batch = split.features[start : start + BATCH_SIZE]
            indices = tensor_forward(head, batch.to(device)).argmax(dim=1).cpu()
            predictions.extend(int(index.item()) for index in indices)
    return tuple(predictions)


def fit_head(
    original: nn.Linear, train: FeatureSplit, validation: FeatureSplit, device: str
) -> FitResult:
    """Fit only a copied linear head and select among actual trained epochs."""
    seed_everything()
    head = nn.Linear(original.in_features, original.out_features).to(device)
    _ = head.load_state_dict(tensor_state(original))
    optimizer = torch.optim.AdamW(head.parameters(), lr=0.001, weight_decay=0.0001)
    weights = torch.tensor(class_weights(train.labels), device=device)
    labels = torch.tensor(train.labels, dtype=torch.long)
    generator = torch.Generator().manual_seed(SEED)
    history: list[Epoch] = []
    best: Metrics | None = None
    best_epoch = 0
    best_state = {name: value.detach().clone() for name, value in tensor_state(head).items()}
    stale = 0
    for epoch in range(1, MAX_EPOCHS + 1):
        total_loss = 0.0
        permutation = torch.randperm(len(train.labels), generator=generator)
        for start in range(0, len(train.labels), BATCH_SIZE):
            indices = permutation[start : start + BATCH_SIZE]
            optimizer.zero_grad()
            logits = tensor_forward(head, train.features[indices].to(device))
            loss = functional.cross_entropy(logits, labels[indices].to(device), weight=weights)
            backpropagate(loss)
            optimizer_step(optimizer)
            total_loss += float(loss.item()) * indices.numel()
        metrics = evaluate(validation.labels, predict(head, validation, device))
        history.append(Epoch(epoch, total_loss / len(train.labels), metrics))
        if improves(metrics, best):
            best, best_epoch, stale = metrics, epoch, 0
            best_state = {
                name: value.detach().clone() for name, value in tensor_state(head).items()
            }
        else:
            stale += 1
        progress(
            "epoch_completed",
            epoch=epoch,
            loss=history[-1].loss,
            validation_accuracy=metrics.accuracy,
            validation_macro_f1=metrics.macro_f1,
            validation_balanced_accuracy=metrics.balanced_accuracy,
            validation_week_mae=metrics.week_mae,
            validation_unsupported_prediction_fraction=metrics.unsupported_prediction_fraction,
            selected_epoch=best_epoch,
            epochs_without_improvement=stale,
        )
        if stale >= PATIENCE:
            break
    _ = head.load_state_dict(best_state)
    changed = any(
        not torch.equal(value.cpu(), tensor_state(original)[name].cpu())
        for name, value in tensor_state(head).items()
    )
    return FitResult(head, tuple(history), best_epoch, changed)


def sha256(path: Path) -> str:
    """Hash the exact bytes used by this run."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(args: Arguments) -> None:
    """Create a new private run; defer all test access until selection ends."""
    seed_everything()
    config = Preprocessing.model_validate_json(args.preprocessing_config.read_bytes())
    manifest = TrainingManifest.model_validate_json(args.manifest.read_bytes())
    if tuple(sorted(manifest.supported_weeks)) != SUPPORTED:
        raise EvaluationError(reason="manifest")
    partitions = {
        split: tuple(record for record in manifest.records if record.split == split)
        for split in ("train", "validation", "test", "quarantine")
    }
    if tuple(len(partitions[s]) for s in partitions) != (1971, 805, 1033, 63):
        raise EvaluationError(reason="counts")
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    model = load_platform_model(args.weights, device=args.device)
    _, original = split_model(model)
    train = extract_features(model, partitions["train"], args.data_root, config, args.device)
    validation = extract_features(
        model,
        partitions["validation"],
        args.data_root,
        config,
        args.device,
    )
    baseline_validation = evaluate(validation.labels, predict(original, validation, args.device))
    fitted = fit_head(original, train, validation, args.device)
    history = [asdict(epoch) for epoch in fitted.history]
    _ = (args.output / "history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
    _ = (args.output / "history.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in history),
        encoding="utf-8",
    )
    test = extract_features(model, partitions["test"], args.data_root, config, args.device)
    majority, median = constant_baselines(train.labels)
    predictions = {
        "original": predict(original, test, args.device),
        "adapted": predict(fitted.head, test, args.device),
        "majority": (majority,) * len(test.labels),
        "median": (median,) * len(test.labels),
    }
    report = {
        "limits": LIMITS,
        "evaluation_scope": manifest.evaluation_scope,
        "checkpoint_pretraining_overlap": "UNKNOWN",
        "selected_epoch": fitted.selected_epoch,
        "head_changed": fitted.head_changed,
        "baseline_validation": asdict(baseline_validation),
        "test": {
            name: asdict(evaluate(test.labels, values)) for name, values in predictions.items()
        },
        "representative_errors": [
            {
                "record_id": record.record_id,
                "week": record.week,
                "prediction": predicted,
                "capture_day": str(record.capture_day),
            }
            for record, predicted in zip(test.records, predictions["adapted"], strict=True)
            if record.week != predicted
        ][:20],
        "config": {
            "seed": SEED,
            "batch_size": BATCH_SIZE,
            "max_epochs": MAX_EPOCHS,
            "patience": PATIENCE,
            "optimizer": "AdamW",
            "learning_rate": 0.001,
            "weight_decay": 0.0001,
            "device": args.device,
            "tf32": False,
            "class_weights": class_weights(train.labels),
            "preprocessing": config.model_dump(),
            "preprocessing_sha256": sha256(args.preprocessing_config),
            "manifest_sha256": sha256(args.manifest),
            "model_sha256": sha256(args.weights),
            "runner_sha256": sha256(Path(__file__)),
            "runtime_versions": {
                name: version(name)
                for name in ("torch", "timm", "h5py", "numpy", "pillow", "pydantic")
            },
            "partition_counts": {name: len(rows) for name, rows in partitions.items()},
        },
    }
    config_path = args.output / "config.json"
    _ = config_path.write_text(json.dumps(report["config"], indent=2), encoding="utf-8")
    report["config_sha256"] = sha256(config_path)
    with (args.output / "predictions.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("record_id", "capture_day", "week", *predictions))
        for i, record in enumerate(test.records):
            writer.writerow(
                (
                    record.record_id,
                    record.capture_day,
                    record.week,
                    *(values[i] for values in predictions.values()),
                )
            )
    _ = original.load_state_dict(tensor_state(fitted.head))
    destination = args.output / "05.green_tea.h5"
    export_platform_weights(model, args.weights, destination)
    reloaded = load_platform_model(destination, device=args.device)
    for name, value in tensor_state(model).items():
        if not name.endswith("num_batches_tracked"):
            torch.testing.assert_close(tensor_state(reloaded)[name], value, rtol=0, atol=0)
    report["export_sha256"] = sha256(destination)
    _ = (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    progress("report_written", path=str(args.output / "report.json"))


def main(argv: Sequence[str] | None = None) -> None:
    """Parse required paths, including the explicit verified preprocessing receipt."""
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("manifest", "data-root", "weights", "preprocessing-config", "output"):
        _ = parser.add_argument(f"--{flag}", required=True, type=Path)
    _ = parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    args = Arguments.model_validate(vars(parser.parse_args(argv)))
    run(args)


if __name__ == "__main__":
    main()
