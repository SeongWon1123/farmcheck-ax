"""Deterministic regression tests without private weights or network downloads."""

from __future__ import annotations

import hashlib
import shutil
from typing import TYPE_CHECKING, Final, assert_never

import h5py
import numpy as np
import pytest
import torch
from torch import nn

from training.platform_model import (
    NUM_CLASSES,
    export_platform_weights,
    load_platform_model,
    platform_weight_mapping,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from numpy.typing import NDArray

_SOURCE_ARRAYS: Final = 311
_STAGES: Final = (
    (3, 1, 16, 1),
    (3, 2, 24, 6),
    (5, 2, 40, 6),
    (3, 3, 80, 6),
    (5, 3, 112, 6),
    (5, 4, 192, 6),
    (3, 1, 320, 6),
)


def dataset_at(group: h5py.Group, name: str) -> h5py.Dataset:
    """Read a fixture dataset after checking the actual HDF5 object kind."""
    item = group[name]
    match item:
        case h5py.Dataset() as dataset:
            return dataset
        case h5py.Group() | h5py.Datatype():
            pytest.fail(f"Fixture path is not a dataset: {name}")
    assert_never(item)


def array_at(dataset: h5py.Dataset) -> NDArray[np.float32]:
    """Read into a concrete float32 buffer instead of asserting an untyped result."""
    array: NDArray[np.float32] = np.empty(dataset.shape, dtype=np.float32)
    dataset.read_direct(array)
    return array


def logits_for(
    predict: Callable[[torch.Tensor], torch.Tensor],
    image: torch.Tensor,
) -> torch.Tensor:
    """Invoke the validated tensor-to-tensor model contract."""
    return predict(image)


@pytest.fixture(scope="module")
def source_path(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Build Keras-shaped arrays independently from the converter's mapping."""
    path = tmp_path_factory.mktemp("platform") / "source.h5"
    original_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    rng = np.random.default_rng(1947)
    with h5py.File(str(path), "w") as source:
        source.attrs["backend"] = "tensorflow"
        source.attrs["keras_version"] = "2.4.0"
        source.attrs["private_fixture"] = "metadata must survive export"

        def add(layer: str, variable: str, shape: tuple[int, ...]) -> None:
            values = rng.uniform(-0.05, 0.05, shape).astype(np.float32)
            if variable in {"gamma", "moving_variance"}:
                values += 1.0
            dataset = source.create_dataset(f"{layer}/{layer}/{variable}:0", data=values)
            dataset.attrs["sentinel"] = layer

        def conv(layer: str, kernel: int, inputs: int, outputs: int, *, bias: bool = False) -> None:
            add(layer, "kernel", (kernel, kernel, inputs, outputs))
            if bias:
                add(layer, "bias", (outputs,))

        def bn(layer: str, channels: int) -> None:
            for variable in ("gamma", "beta", "moving_mean", "moving_variance"):
                add(layer, variable, (channels,))

        conv("stem_conv", 3, 3, 32)
        bn("stem_bn", 32)
        channels = 32
        for stage, (kernel, repeats, outputs, expansion) in enumerate(_STAGES, 1):
            for block in range(repeats):
                prefix = f"block{stage}{chr(ord('a') + block)}"
                expanded = channels * expansion
                if expansion != 1:
                    conv(f"{prefix}_expand_conv", 1, channels, expanded)
                    bn(f"{prefix}_expand_bn", expanded)
                add(f"{prefix}_dwconv", "depthwise_kernel", (kernel, kernel, expanded, 1))
                bn(f"{prefix}_bn", expanded)
                squeezed = max(1, channels // 4)
                conv(f"{prefix}_se_reduce", 1, expanded, squeezed, bias=True)
                conv(f"{prefix}_se_expand", 1, squeezed, expanded, bias=True)
                conv(f"{prefix}_project_conv", 1, expanded, outputs)
                bn(f"{prefix}_project_bn", outputs)
                channels = outputs
        conv("top_conv", 1, channels, 1280)
        bn("top_bn", 1280)
        add("probs", "kernel", (1280, NUM_CLASSES))
        add("probs", "bias", (NUM_CLASSES,))
    try:
        yield path
    finally:
        torch.set_num_threads(original_threads)


@pytest.fixture
def mutable_source(source_path: Path, tmp_path: Path) -> Path:
    """Copy the source so corruption tests cannot affect other tests."""
    destination = tmp_path / "mutable.h5"
    _ = shutil.copyfile(source_path, destination)
    return destination


def test_complete_weight_reuse(source_path: Path) -> None:
    """Every source array replaces its target, including the classifier."""
    model = load_platform_model(source_path)
    mapping = platform_weight_mapping()
    np.testing.assert_equal(len(mapping), _SOURCE_ARRAYS)
    np.testing.assert_equal(len({entry.source for entry in mapping}), _SOURCE_ARRAYS)
    np.testing.assert_equal(len({entry.target for entry in mapping}), _SOURCE_ARRAYS)
    np.testing.assert_equal(model.training, desired=False)
    state: dict[str, torch.Tensor] = dict(model.state_dict())
    with h5py.File(str(source_path), "r") as source:
        for entry in mapping:
            dataset = dataset_at(source, entry.source)
            array = array_at(dataset)
            expected = array if entry.axes is None else array.transpose(entry.axes)
            actual = np.asarray(state[entry.target].numpy(), dtype=np.float32)
            np.testing.assert_array_equal(actual, expected)
    for module in model.modules():
        match module:
            case nn.BatchNorm2d():
                np.testing.assert_equal(module.eps, 0.001)
                np.testing.assert_equal(module.momentum, 0.01)
                continue
            case nn.Module():
                continue
        assert_never(module)


def test_axis_conversion_independent_examples(source_path: Path) -> None:
    """Spatial, channel, depthwise and classifier indices retain meaning."""
    state: dict[str, torch.Tensor] = dict(load_platform_model(source_path).state_dict())
    with h5py.File(str(source_path), "r") as source:
        stem = dataset_at(source, "stem_conv/stem_conv/kernel:0")
        depthwise = dataset_at(source, "block3a_dwconv/block3a_dwconv/depthwise_kernel:0")
        dense = dataset_at(source, "probs/probs/kernel:0")
        np.testing.assert_array_equal(
            state["conv_stem.weight"][7, 2, 1, 0].numpy(),
            array_at(stem)[1:2, 0:1, 2:3, 7:8].reshape(()),
        )
        np.testing.assert_array_equal(
            state["blocks.2.0.conv_dw.weight"][13, 0, 3, 2].numpy(),
            array_at(depthwise)[3:4, 2:3, 13:14, 0:1].reshape(()),
        )
        np.testing.assert_array_equal(
            state["classifier.weight"][9, 117].numpy(),
            array_at(dense)[117:118, 9:10].reshape(()),
        )


def test_output_and_backward(source_path: Path) -> None:
    """Odd-sized inputs yield 14 finite logits and gradients without a step."""
    model = load_platform_model(source_path)
    image = torch.linspace(-1, 1, 2 * 3 * 65 * 67).reshape(2, 3, 65, 67)
    logits = logits_for(model, image)
    np.testing.assert_equal(tuple(logits.shape), (2, NUM_CLASSES))
    np.testing.assert_equal(bool(torch.isfinite(logits).all()), desired=True)
    parameters = tuple(model.parameters())
    gradients = torch.autograd.grad(logits.square().mean(), parameters)
    np.testing.assert_equal(len(gradients), len(parameters))
    for parameter, gradient in zip(parameters, gradients, strict=True):
        np.testing.assert_equal(parameter.requires_grad, desired=True)
        np.testing.assert_equal(bool(torch.isfinite(gradient).all()), desired=True)


def test_round_trip_preserves_all_arrays_and_metadata(source_path: Path, tmp_path: Path) -> None:
    """An unchanged model exports bit-exact arrays and metadata."""
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    model = load_platform_model(source_path)
    destination = tmp_path / "roundtrip.h5"
    export_platform_weights(model, source_path, destination)
    with h5py.File(str(source_path), "r") as original, h5py.File(str(destination), "r") as exported:
        np.testing.assert_equal(dict(original.attrs), dict(exported.attrs))
        for entry in platform_weight_mapping():
            before = dataset_at(original, entry.source)
            after = dataset_at(exported, entry.source)
            np.testing.assert_array_equal(
                array_at(before),
                array_at(after),
            )
            np.testing.assert_equal(dict(before.attrs), dict(after.attrs))
            np.testing.assert_equal(before.dtype, after.dtype)
            np.testing.assert_equal(before.chunks, after.chunks)
    np.testing.assert_equal(hashlib.sha256(destination.read_bytes()).hexdigest(), source_hash)
    np.testing.assert_equal(hashlib.sha256(source_path.read_bytes()).hexdigest(), source_hash)
    reloaded: dict[str, torch.Tensor] = dict(load_platform_model(destination).state_dict())
    state: dict[str, torch.Tensor] = dict(model.state_dict())
    for key, value in state.items():
        np.testing.assert_equal(torch.equal(value, reloaded[key]), desired=True)


def test_export_updated_values(source_path: Path, tmp_path: Path) -> None:
    """Updated convolution, BN and head arrays survive inverse conversion."""
    model = load_platform_model(source_path)
    state: dict[str, torch.Tensor] = dict(model.state_dict())
    changed = (
        "conv_stem.weight",
        "blocks.2.0.conv_dw.weight",
        "bn1.running_mean",
        "bn1.running_var",
        "classifier.weight",
        "classifier.bias",
    )
    for key in changed:
        state[key] = state[key] + 0.125
    _ = model.load_state_dict(state)
    destination = tmp_path / "updated.h5"
    export_platform_weights(model, source_path, destination)
    reloaded: dict[str, torch.Tensor] = dict(load_platform_model(destination).state_dict())
    for key, value in state.items():
        np.testing.assert_equal(torch.equal(value, reloaded[key]), desired=True)
    with h5py.File(str(source_path), "r") as before, h5py.File(str(destination), "r") as after:
        for entry in platform_weight_mapping():
            original = dataset_at(before, entry.source)
            exported = dataset_at(after, entry.source)
            expected = array_at(original)
            if entry.target in changed:
                expected = expected + np.float32(0.125)
            np.testing.assert_array_equal(array_at(exported), expected)


def test_missing_array_rejected(mutable_source: Path) -> None:
    """A source with one missing learned tensor cannot silently initialize."""
    with h5py.File(str(mutable_source), "r+") as weights:
        del weights["probs/probs/bias:0"]
    with pytest.raises(ValueError, match="coverage mismatch"):
        _ = load_platform_model(mutable_source)


def test_extra_array_rejected(mutable_source: Path) -> None:
    """Unknown arrays cannot be silently ignored."""
    with h5py.File(str(mutable_source), "r+") as weights:
        _ = weights.create_dataset("unexpected", data=np.zeros(1, dtype=np.float32))
    with pytest.raises(ValueError, match="coverage mismatch"):
        _ = load_platform_model(mutable_source)


def test_wrong_shape_rejected(mutable_source: Path) -> None:
    """A seven-class head cannot be substituted for the original head."""
    with h5py.File(str(mutable_source), "r+") as weights:
        del weights["probs/probs/bias:0"]
        _ = weights.create_dataset("probs/probs/bias:0", data=np.zeros(7, dtype=np.float32))
    with pytest.raises(ValueError, match="Shape mismatch"):
        _ = load_platform_model(mutable_source)


def test_nonfinite_array_rejected(mutable_source: Path) -> None:
    """Source NaNs fail before a model is returned."""
    with h5py.File(str(mutable_source), "r+") as weights:
        dataset = dataset_at(weights, "probs/probs/bias:0")
        value = np.full(NUM_CLASSES, np.nan, dtype=np.float32)
        dataset[()] = value
    with pytest.raises(ValueError, match="Non-finite"):
        _ = load_platform_model(mutable_source)


def test_wrong_dtype_rejected(mutable_source: Path) -> None:
    """Implicit source dtype conversions are not allowed."""
    with h5py.File(str(mutable_source), "r+") as weights:
        del weights["probs/probs/bias:0"]
        _ = weights.create_dataset(
            "probs/probs/bias:0",
            data=np.zeros(NUM_CLASSES, dtype=np.float64),
        )
    with pytest.raises(ValueError, match="float32"):
        _ = load_platform_model(mutable_source)


def test_export_refuses_overwrite(source_path: Path, mutable_source: Path) -> None:
    """The original or an existing destination is never overwritten."""
    model = load_platform_model(source_path)
    with pytest.raises(FileExistsError, match="overwrite"):
        export_platform_weights(model, source_path, source_path)
    with pytest.raises(FileExistsError, match="overwrite"):
        export_platform_weights(model, source_path, mutable_source)


def test_export_rejects_invalid_model_without_destination(
    source_path: Path, tmp_path: Path
) -> None:
    """Invalid updated state cannot create a seemingly usable H5."""
    model = load_platform_model(source_path)
    state: dict[str, torch.Tensor] = dict(model.state_dict())
    state["classifier.bias"][0] = float("nan")
    _ = model.load_state_dict(state)
    destination = tmp_path / "invalid.h5"
    with pytest.raises(ValueError, match="finite float32"):
        export_platform_weights(model, source_path, destination)
    np.testing.assert_equal(destination.exists(), desired=False)


def test_export_rejects_changed_architecture(source_path: Path, tmp_path: Path) -> None:
    """Export cannot silently drop a replacement model's parameters."""
    destination = tmp_path / "wrong-model.h5"
    with pytest.raises(ValueError, match="Model state"):
        export_platform_weights(nn.Linear(3, 7), source_path, destination)
    np.testing.assert_equal(destination.exists(), desired=False)
