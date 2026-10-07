"""Container-only public task API observations without expected answers."""

import base64
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path


def attempt(operation):
    try:
        return operation()
    except (
        ValueError,
        TypeError,
        AttributeError,
        RuntimeError,
        re.error,
        SystemExit,
    ) as error:
        return {"exception": type(error).__module__ + "." + type(error).__qualname__}


def verify_origin(module):
    if not Path(module.__file__).resolve().is_relative_to(Path("/workspace")):
        raise RuntimeError("Target module did not originate in the mounted repository.")


def flask_observations():
    import flask
    from flask import Blueprint, Flask

    verify_origin(flask)

    def empty_blueprint(**options):
        Blueprint("", __name__, **options)
        return {"accepted": True}

    def registered_blueprint():
        blueprint = Blueprint("public_probe", __name__, url_prefix="/probe")
        blueprint.add_url_rule("/hello", "hello", lambda: "public blueprint response")
        app = Flask("public_probe_app", root_path="/tmp")
        app.register_blueprint(blueprint)
        response = app.test_client().get("/probe/hello")
        return {
            "registered_names": sorted(app.blueprints),
            "status": response.status_code,
            "body": response.get_data(as_text=True),
        }

    return {
        "empty_name": attempt(empty_blueprint),
        "empty_name_with_url_prefix": attempt(
            lambda: empty_blueprint(url_prefix="/probe")
        ),
        "named_blueprint_control": attempt(registered_blueprint),
    }


def seaborn_observations():
    os.environ["MPLCONFIGDIR"] = "/tmp/mplconfig"
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn
    import seaborn.objects as so

    verify_origin(seaborn)

    def observe(axis, labels, explicit):
        with plt.rc_context({"axes.grid": True}):
            figure, axes = plt.subplots()
        try:
            positions = list(range(len(labels)))
            data = {axis: labels, "y" if axis == "x" else "x": positions}
            plot = so.Plot(**data).add(so.Dot())
            if explicit:
                plot = plot.scale(**{axis: so.Nominal()})
            plot.on(axes).plot()
            observed_axis = getattr(axes, axis + "axis")
            return {
                "limits": [
                    round(float(value), 6)
                    for value in getattr(axes, "get_" + axis + "lim")()
                ],
                "grid_visible": any(
                    line.get_visible() for line in observed_axis.get_gridlines()
                ),
                "inverted": bool(getattr(axes, axis + "axis_inverted")()),
            }
        finally:
            plt.close(figure)

    return {
        "explicit_nominal_x": attempt(lambda: observe("x", ["a", "b", "c"], True)),
        "explicit_nominal_y": attempt(lambda: observe("y", ["a", "b", "c"], True)),
        "inferred_nominal_x": attempt(lambda: observe("x", ["first", "last"], False)),
        "inferred_nominal_y": attempt(lambda: observe("y", ["first", "last"], False)),
        "continuous_x_control": attempt(lambda: observe("x", [0, 1, 2], False)),
    }


def pylint_observations():
    import pylint
    from pylint.lint import Run
    from pylint.reporters.json_reporter import JSONReporter

    verify_origin(pylint)

    def lint(pattern):
        with tempfile.TemporaryDirectory(prefix="public-task-pylint-") as temporary:
            folder = Path(temporary)
            source = folder / "probe_names.py"
            source.write_text("foo = 1\nfooo = 2\nfoooo = 3\ngood_name = 4\nbar = 5\n")
            config = folder / "pylintrc"
            config.write_text("[MAIN]\npersistent=no\n")
            output = io.StringIO()
            Run(
                [
                    "--rcfile=" + str(config),
                    "--persistent=no",
                    "--disable=all",
                    "--enable=disallowed-name",
                    "--bad-names=",
                    "--good-names=",
                    "--bad-names-rgxs=" + pattern,
                    str(source),
                ],
                reporter=JSONReporter(output),
                exit=False,
            )
            diagnostics = json.loads(output.getvalue())
            return sorted(
                [
                    {"line": item["line"], "symbol": item["symbol"]}
                    for item in diagnostics
                ],
                key=lambda item: (item["line"], item["symbol"]),
            )

    return {
        "quantifier_comma": attempt(lambda: lint("(foo{1,3})$")),
        "quantifier_and_second_pattern": attempt(lambda: lint("(foo{1,3})$,(bar)$")),
        "character_class_comma": attempt(lambda: lint("^[fo,]+$")),
        "ordinary_pattern_control": attempt(lambda: lint("^good_name$")),
        "two_patterns_control": attempt(lambda: lint("^good_name$,^bar$")),
    }


def main():
    token, task = sys.argv[1:3]
    sys.dont_write_bytecode = True
    sys.path[:0] = ["/workspace/src", "/workspace"]
    operations = {
        "pallets__flask-5014": flask_observations,
        "mwaskom__seaborn-3069": seaborn_observations,
        "pylint-dev__pylint-8898": pylint_observations,
    }
    try:
        response = {"kind": "returned", "value": operations[task]()}
    except Exception as error:  # noqa: BLE001 - missing public API/dependencies must abstain
        response = {
            "kind": "adapter_error",
            "message": "Public API observation unavailable: " + type(error).__name__,
        }
    payload = json.dumps(response, allow_nan=False, separators=(",", ":")).encode()
    print(
        "\nCODEHOUND_CALL_V1:" + token + ":" + base64.b64encode(payload).decode(),
        flush=True,
    )


if __name__ == "__main__":
    main()
