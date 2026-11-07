"""Strict, reversible conversion of the platform's 14-class green-tea weights.

Runtime versions validated: torch 2.10.0, torchvision 0.25.0, timm 1.0.22,
h5py 3.15.1. The architecture is qubvel EfficientNetB0, not torchvision B0:
TensorFlow SAME padding, swish, and batch-normalization epsilon 0.001 matter.
No ImageNet weights are requested or loaded.

``load_platform_model`` returns an eval-mode float32 PyTorch module producing
14 logits from NCHW float32 inputs. Apply softmax for Keras-style probabilities.
Call ``model.train()`` before fine-tuning. Image resizing, channel order, and
normalization belong to the caller; the weights file cannot establish them.
The default qubvel dropout/drop-connect rates are used for training, but parity
claims concern inference, not cross-framework optimizer or BN-update equality.

``export_platform_weights`` writes learned weights and BN running statistics
into a new copy of the original weights-only H5, retaining its metadata and
class order. PyTorch-only BN batch counters have no Keras counterpart.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, assert_never

import h5py
import numpy as np
import torch
from timm import create_model
from torch import nn

if TYPE_CHECKING:
    from collections.abc import Mapping

    from numpy.typing import NDArray

NUM_CLASSES: Final = 14
_BLOCK_REPEATS: Final = (1, 2, 2, 3, 3, 4, 1)
_BN_WEIGHTS: Final = (
    ("gamma", "weight"),
    ("beta", "bias"),
    ("moving_mean", "running_mean"),
    ("moving_variance", "running_var"),
)


@dataclass(frozen=True, slots=True)
class WeightMapping:
    """One H5 dataset, its PyTorch state key, and source-to-target axis order."""

    source: str
    target: str
    axes: tuple[int, ...] | None = None


def platform_weight_mapping() -> tuple[WeightMapping, ...]:
    """Return the complete, one-to-one 311-array conversion specification."""
    entries: list[WeightMapping] = []

    def add(layer: str, variable: str, target: str, axes: tuple[int, ...] | None = None) -> None:
        entries.append(WeightMapping(f"{layer}/{layer}/{variable}:0", target, axes))

    def conv(layer: str, target: str) -> None:
        add(layer, "kernel", f"{target}.weight", (3, 2, 0, 1))

    def bn(layer: str, target: str) -> None:
        for source_name, target_name in _BN_WEIGHTS:
            add(layer, source_name, f"{target}.{target_name}")

    conv("stem_conv", "conv_stem")
    bn("stem_bn", "bn1")
    for stage, repeats in enumerate(_BLOCK_REPEATS):
        for block in range(repeats):
            source = f"block{stage + 1}{chr(ord('a') + block)}"
            target = f"blocks.{stage}.{block}"
            expanded = stage != 0
            if expanded:
                conv(f"{source}_expand_conv", f"{target}.conv_pw")
                bn(f"{source}_expand_bn", f"{target}.bn1")
            add(
                f"{source}_dwconv",
                "depthwise_kernel",
                f"{target}.conv_dw.weight",
                (2, 3, 0, 1),
            )
            bn(f"{source}_bn", f"{target}.bn2" if expanded else f"{target}.bn1")
            for operation in ("reduce", "expand"):
                conv(f"{source}_se_{operation}", f"{target}.se.conv_{operation}")
                add(f"{source}_se_{operation}", "bias", f"{target}.se.conv_{operation}.bias")
            conv(
                f"{source}_project_conv",
                f"{target}.conv_pwl" if expanded else f"{target}.conv_pw",
            )
            bn(f"{source}_project_bn", f"{target}.bn3" if expanded else f"{target}.bn2")
    conv("top_conv", "conv_head")
    bn("top_bn", "bn2")
    add("probs", "kernel", "classifier.weight", (1, 0))
    add("probs", "bias", "classifier.bias")
    return tuple(entries)


def _check_state(state: Mapping[str, torch.Tensor]) -> tuple[WeightMapping, ...]:
    entries = platform_weight_mapping()
    targets = {entry.target for entry in entries}
    counters = {
        key.removesuffix("running_mean") + "num_batches_tracked"
        for key in targets
        if key.endswith(".running_mean")
    }
    if set(state) != targets | counters:
        message = (
            "Model state does not match the platform architecture: "
            f"missing={sorted((targets | counters) - set(state))}, "
            f"unexpected={sorted(set(state) - targets - counters)}"
        )
        raise ValueError(message)
    return entries


def _read_weights(
    weights_path: str | Path,
    state: Mapping[str, torch.Tensor],
) -> dict[str, NDArray[np.float32]]:
    entries = _check_state(state)
    arrays: dict[str, NDArray[np.float32]] = {}
    with h5py.File(str(weights_path), "r") as source:
        dataset_names: set[str] = set()

        def collect(name: str, item: h5py.Group | h5py.Dataset) -> None:
            match item:
                case h5py.Dataset():
                    dataset_names.add(name)
                    return
                case h5py.Group():
                    return
            assert_never(item)

        source.visititems(collect)
        expected = {entry.source for entry in entries}
        if dataset_names != expected:
            message = (
                "H5 dataset coverage mismatch: "
                f"missing={sorted(expected - dataset_names)}, "
                f"unexpected={sorted(dataset_names - expected)}"
            )
            raise ValueError(message)
        for entry in entries:
            dataset = _dataset(source, entry.source)
            if dataset.dtype != np.dtype("float32"):
                message = f"Expected float32 dataset: {entry.source}"
                raise ValueError(message)
            array: NDArray[np.float32] = np.empty(dataset.shape, dtype=np.float32)
            dataset.read_direct(array)
            converted = array if entry.axes is None else array.transpose(entry.axes)
            if converted.shape != tuple(state[entry.target].shape):
                message = f"Shape mismatch for {entry.source} -> {entry.target}"
                raise ValueError(message)
            if not np.isfinite(array).all():
                message = f"Non-finite source weights: {entry.source}"
                raise ValueError(message)
            arrays[entry.source] = array
    return arrays


def _dataset(group: h5py.Group, name: str) -> h5py.Dataset:
    """Narrow an HDF5 object by its actual runtime kind, not a type assertion."""
    item = group[name]
    match item:
        case h5py.Dataset() as dataset:
            return dataset
        case h5py.Group() | h5py.Datatype():
            message = f"Expected an HDF5 dataset: {name}"
            raise ValueError(message)
    assert_never(item)


def load_platform_model(
    weights_path: str | Path,
    device: str | torch.device = "cpu",
) -> nn.Module:
    """Load every platform weight strictly and return an eval-mode logit model.

    Raises:
        ValueError: Any source dataset is missing, extra, non-finite, or has an
            incompatible dtype/shape, or the installed timm architecture changed.
        OSError: The source H5 cannot be opened.

    """
    model = create_model(
        "tf_efficientnet_b0",
        pretrained=False,
        num_classes=NUM_CLASSES,
        bn_eps=0.001,
        bn_momentum=0.01,
        drop_rate=0.2,
        drop_path_rate=0.2,
    )
    state: dict[str, torch.Tensor] = dict(model.state_dict())
    arrays = _read_weights(weights_path, state)
    for entry in platform_weight_mapping():
        array = arrays[entry.source]
        converted = array if entry.axes is None else array.transpose(entry.axes)
        state[entry.target] = torch.as_tensor(np.ascontiguousarray(converted))
    _ = model.load_state_dict(state, strict=True)
    return model.to(device=device, dtype=torch.float32).eval()


def export_platform_weights(
    model: nn.Module,
    source_h5: str | Path,
    destination_h5: str | Path,
) -> None:
    """Export all learned arrays to a new H5 copy, without altering the source.

    The destination must not already exist. This retains the original H5 groups,
    attributes, dtype and dataset layout, including the 14-class ``probs`` head.
    It does not package or modify any of the other nine crop models.

    Raises:
        ValueError: The source, model state, shape, dtype or values are incompatible.
        FileExistsError: The destination exists (including the source itself).
        OSError: The source cannot be read or the destination cannot be written.

    """
    source_path = Path(source_h5)
    destination = Path(destination_h5)
    if destination.exists() or destination.resolve() == source_path.resolve():
        message = f"Refusing to overwrite an existing/source H5: {destination}"
        raise FileExistsError(message)
    state: dict[str, torch.Tensor] = dict(model.state_dict())
    arrays = _read_weights(source_path, state)
    exported: dict[str, NDArray[np.float32]] = {}
    for entry in platform_weight_mapping():
        tensor = state[entry.target].detach().cpu()
        if tensor.dtype != torch.float32 or not bool(torch.isfinite(tensor).all()):
            message = f"Expected finite float32 model state: {entry.target}"
            raise ValueError(message)
        array = np.asarray(tensor.numpy(), dtype=np.float32)
        axes = entry.axes
        inverse = (
            array
            if axes is None
            else array.transpose(tuple(axes.index(axis) for axis in range(len(axes))))
        )
        if inverse.shape != arrays[entry.source].shape:
            message = f"Export shape mismatch for {entry.target} -> {entry.source}"
            raise ValueError(message)
        exported[entry.source] = inverse
    with source_path.open("rb") as original, destination.open("xb") as output:
        _ = shutil.copyfileobj(original, output)
    with h5py.File(str(destination), "r+") as output_h5:
        for name, array in exported.items():
            dataset = _dataset(output_h5, name)
            dataset[()] = array
