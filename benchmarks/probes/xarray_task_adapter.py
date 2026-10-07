"""Issue-derived xarray public API observations; no expected outputs/assertions."""

import base64
import copy
import json
import math
import sys
import traceback
from pathlib import Path


def emit(token, response):
    raw = json.dumps(response, allow_nan=False, separators=(",", ":")).encode()
    print(
        "\nCODEHOUND_CALL_V1:" + token + ":" + base64.b64encode(raw).decode(),
        flush=True,
    )


def attempt(function):
    try:
        return function()
    except Exception as exc:  # noqa: BLE001 - public API exceptions are raw observations
        return {"exception": type(exc).__module__ + "." + type(exc).__qualname__}


def coordinate_copy(xr, operation):
    ds = xr.Dataset(
        coords={"x": ["foo"], "y": ("x", ["bar"])}, data_vars={"z": ("x", ["baz"])}
    )
    targets = {
        "dataset_shallow": lambda: ds.copy(deep=False)["z"],
        "dataset_deep": lambda: ds.copy(deep=True)["z"],
        "dataarray_shallow": lambda: ds["z"].copy(deep=False),
        "dataarray_deep": lambda: ds["z"].copy(deep=True),
        "copy_module": lambda: copy.copy(ds["z"]),
        "deepcopy_module": lambda: copy.deepcopy(ds["z"]),
        "numeric_control": lambda: xr.DataArray(
            [1, 2], dims="x", coords={"x": [0, 1]}
        ).copy(deep=True),
    }
    result = targets[operation]()
    return {
        "index_kind": result.coords["x"].dtype.kind,
        "index_values": result.coords["x"].values.tolist(),
        "values": result.values.tolist(),
    }


def combine(xr, np, scenario):
    y = ["a", "c", "b"]
    if scenario == "monotonic_control":
        y = ["a", "b", "c"]
    elif scenario == "numeric_identical_nonmonotonic":
        y = [3, 1, 2]
    if scenario == "varying_nonmonotonic_rejected":
        first = xr.Dataset(
            {"data": (("x", "y"), np.zeros((2, 2)))},
            coords={"x": [1, 2], "y": ["a", "c"]},
        )
        second = xr.Dataset(
            {"data": (("x", "y"), np.ones((2, 2)))},
            coords={"x": [1, 2], "y": ["b", "d"]},
        )
    else:
        first = xr.Dataset(
            {"data": (("x", "y"), np.arange(6).reshape(2, 3))},
            coords={"x": [1, 2], "y": y},
        )
        second = xr.Dataset(
            {"data": (("x", "y"), np.arange(6, 12).reshape(2, 3))},
            coords={"x": [3, 4], "y": y},
        )
    inputs = [second, first] if scenario == "reversed_inputs" else [first, second]
    result = xr.combine_by_coords(inputs)
    return {
        "x": result["x"].values.tolist(),
        "y": result["y"].values.tolist(),
        "values": result["data"].values.tolist(),
    }


def quantile(xr, scenario):
    da = xr.DataArray([0, 2], dims="x", attrs={"units": "K"})
    vector = scenario == "keep_attrs_true_vector"
    kwargs = (
        {}
        if scenario == "keep_attrs_default"
        else {"keep_attrs": scenario != "keep_attrs_false"}
    )
    result = da.quantile([0.25, 0.75] if vector else 0.5, dim="x", **kwargs)
    return {
        "attrs": dict(result.attrs),
        "values": result.values.tolist(),
        "source_attrs": dict(da.attrs),
    }


def merge(xr, scenario):
    ds = xr.Dataset({"a": 0})
    da = xr.DataArray(1, name="b")
    if scenario == "top_level_control":
        result = xr.merge([ds, da])
    elif scenario == "dataset_control":
        result = ds.merge(xr.Dataset({"b": 1}))
    elif scenario == "named_vector":
        ds = xr.Dataset({"a": ("x", [0, 1])}, coords={"x": [0, 1]})
        result = ds.merge(
            xr.DataArray([2, 3], dims="x", coords={"x": [0, 1]}, name="b")
        )
    elif scenario == "compatible_same_name":
        result = ds.merge(xr.DataArray(0, name="a"))
    else:
        result = ds.merge(da)
    return {name: result[name].values.tolist() for name in sorted(result.data_vars)}


def integrate(xr, scenario):
    da = xr.DataArray([1.0, 2.0, 4.0], dims="x", coords={"x": [0.0, 1.0, 3.0]})
    if scenario == "dataarray_positional":
        result = da.integrate("x")
    elif scenario == "dataset_keyword_control":
        result = xr.Dataset({"y": da}).integrate(coord="x")["y"]
    elif scenario == "non_dimension_coordinate":
        da = xr.DataArray(
            [1.0, 2.0, 4.0], dims="time", coords={"distance": ("time", [0.0, 1.0, 3.0])}
        )
        result = da.integrate(coord="distance")
    elif scenario == "differentiate_keyword_control":
        result = da.differentiate(coord="x")
    else:
        result = da.integrate(coord="x")
    return result.values.tolist()


def weighted(xr, np, scenario):
    data = [2.0, 6.0, 99.0]
    dtype, weights = bool, [1, 1, 0]
    if scenario == "issue_constant_boolean":
        data = [1.0, 1.0, 1.0]
    elif scenario == "float_weights_control":
        dtype = float
    elif scenario == "integer_weights_control":
        dtype = int
    elif scenario == "missing_data_boolean":
        data = [2.0, float("nan"), 99.0]
    elif scenario == "all_false_weights_control":
        weights = [0, 0, 0]
    value = float(
        xr.DataArray(data, dims="x")
        .weighted(xr.DataArray(np.array(weights, dtype=dtype), dims="x"))
        .mean()
        .values
    )
    return {"kind": "nan"} if math.isnan(value) else {"kind": "finite", "value": value}


def observe(xr, np, task_id):
    scenarios = {
        "pydata__xarray-3095": (
            coordinate_copy,
            [
                "dataset_shallow",
                "dataset_deep",
                "dataarray_shallow",
                "dataarray_deep",
                "copy_module",
                "deepcopy_module",
                "numeric_control",
            ],
        ),
        "pydata__xarray-3151": (
            lambda module, name: combine(module, np, name),
            [
                "identical_nonmonotonic",
                "reversed_inputs",
                "monotonic_control",
                "numeric_identical_nonmonotonic",
                "varying_nonmonotonic_rejected",
            ],
        ),
        "pydata__xarray-3305": (
            quantile,
            [
                "keep_attrs_true",
                "keep_attrs_false",
                "keep_attrs_default",
                "keep_attrs_true_vector",
            ],
        ),
        "pydata__xarray-3677": (
            merge,
            [
                "named_scalar",
                "top_level_control",
                "dataset_control",
                "named_vector",
                "compatible_same_name",
            ],
        ),
        "pydata__xarray-3993": (
            integrate,
            [
                "dataarray_keyword",
                "dataarray_positional",
                "dataset_keyword_control",
                "non_dimension_coordinate",
                "differentiate_keyword_control",
            ],
        ),
        "pydata__xarray-4075": (
            lambda module, name: weighted(module, np, name),
            [
                "issue_constant_boolean",
                "nonuniform_boolean",
                "float_weights_control",
                "integer_weights_control",
                "missing_data_boolean",
                "all_false_weights_control",
            ],
        ),
    }
    function, names = scenarios[task_id]
    return {name: attempt(lambda name=name: function(xr, name)) for name in names}


def main():
    token, task_id = sys.argv[1:3]
    sys.dont_write_bytecode = True
    sys.path[:0] = ["/workspace", "/workspace/src"]
    try:
        import numpy as np
        import xarray as xr

        if not Path(xr.__file__).resolve().is_relative_to(Path("/workspace")):
            raise RuntimeError(
                "xarray did not load from the retained source workspace."
            )
    except Exception:  # noqa: BLE001 - dependency/layout problems are abstentions
        traceback.print_exc(file=sys.stderr)
        emit(
            token,
            {
                "kind": "adapter_error",
                "message": "Pinned xarray target could not load.",
            },
        )
        return
    try:
        emit(token, {"kind": "returned", "value": observe(xr, np, task_id)})
    except Exception as exc:  # noqa: BLE001 - retain candidate/API failures as observations
        emit(
            token,
            {
                "kind": "raised",
                "exception": type(exc).__module__ + "." + type(exc).__qualname__,
            },
        )


if __name__ == "__main__":
    main()
