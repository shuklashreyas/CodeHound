"""Container-only public-API observations; no assertions or expected answers."""

import base64
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


def attempt(operation):
    """Retain feature exceptions while import/dependency failures remain adapter errors."""
    try:
        return operation()
    except (
        ValueError,
        TypeError,
        AttributeError,
        AssertionError,
        RuntimeError,
    ) as error:
        return {"exception": type(error).__module__ + "." + type(error).__qualname__}


def sympy_observations(task):
    import sympy

    if not Path(sympy.__file__).resolve().is_relative_to(Path("/workspace")):
        raise RuntimeError("SymPy target did not originate in the mounted workspace.")
    if task == "sympy__sympy-11618":
        from sympy import Point

        def distance(first, second):
            return round(float(Point(*first).distance(Point(*second)).evalf()), 10)

        return {
            "mixed_dimensions": attempt(lambda: distance((2, 0), (1, 0, 2))),
            "mixed_dimensions_reverse": attempt(lambda: distance((1, 0, 2), (2, 0))),
            "vertical_axis": attempt(lambda: distance((0, 0), (0, 0, 3))),
            "two_dimensions_control": attempt(lambda: distance((1, 2), (4, 6))),
            "three_dimensions_control": attempt(
                lambda: distance((1, 2, 3), (4, 6, 15))
            ),
        }
    if task == "sympy__sympy-12096":
        from sympy.utilities.lambdify import implemented_function

        square = implemented_function("probe_square", lambda value: value**2)
        double = implemented_function("probe_double", lambda value: 2 * value)
        return {
            "direct_square": attempt(lambda: float(square(2).evalf())),
            "direct_double": attempt(lambda: float(double(2).evalf())),
            "square_of_double": attempt(lambda: float(square(double(2)).evalf())),
            "double_of_square": attempt(lambda: float(double(square(2)).evalf())),
            "three_levels": attempt(lambda: float(double(square(double(3))).evalf())),
        }
    if task == "sympy__sympy-12419":
        from sympy import (
            Identity,
            MatrixSymbol,
            Q,
            Sum,
            Symbol,
            assuming,
            refine,
            symbols,
        )

        n = Symbol("n", integer=True, positive=True)
        i, j = symbols("i j", integer=True)
        identity = Identity(n)

        def nested_total(matrix, size):
            return str(
                Sum(Sum(matrix[i, j], (i, 0, size - 1)), (j, 0, size - 1)).doit()
            )

        def orthogonal_total():
            matrix = MatrixSymbol("M", n, n)
            with assuming(Q.orthogonal(matrix)):
                refined = refine((matrix.T * matrix).doit())
            return nested_total(refined, n)

        return {
            "identity_total_symbolic": attempt(lambda: nested_total(identity, n)),
            "orthogonal_total_symbolic": attempt(orthogonal_total),
            "diagonal_control": attempt(
                lambda: str(Sum(identity[i, i], (i, 0, n - 1)).doit())
            ),
            "finite_identity_total": attempt(lambda: nested_total(Identity(3), 3)),
        }
    if task == "sympy__sympy-12481":
        from sympy.combinatorics import Permutation

        return {
            "repeated_transposition": attempt(
                lambda: Permutation([[0, 1], [0, 1]]).array_form
            ),
            "overlap_left_to_right": attempt(
                lambda: Permutation([[0, 1], [1, 2]]).array_form
            ),
            "longer_cycle_overlap": attempt(
                lambda: Permutation([[0, 1, 2], [1, 2]]).array_form
            ),
            "padded_identity": attempt(
                lambda: Permutation([[0, 1], [0, 1]], size=4).array_form
            ),
            "disjoint_control": attempt(
                lambda: Permutation([[0, 1], [2, 3]]).array_form
            ),
        }
    if task == "sympy__sympy-12489":
        from sympy.combinatorics import Cycle, Permutation

        class ProbePermutation(Permutation):
            pass

        def describe(value):
            return {"class": type(value).__name__, "array": value.array_form}

        return {
            "empty": attempt(lambda: describe(ProbePermutation())),
            "integer_identity": attempt(lambda: describe(ProbePermutation(2))),
            "cycle_arguments": attempt(lambda: describe(ProbePermutation(0, 1, 2))),
            "cycle_object": attempt(lambda: describe(ProbePermutation(Cycle(0, 1)))),
            "array_control": attempt(lambda: describe(ProbePermutation([1, 0]))),
        }
    raise ValueError("Unsupported SymPy task.")


def django_setup():
    import django
    from django.conf import settings

    if not Path(django.__file__).resolve().is_relative_to(Path("/workspace")):
        raise RuntimeError("Django target did not originate in the mounted workspace.")
    if not settings.configured:
        settings.configure(
            SECRET_KEY="public-probe-key",
            USE_I18N=False,
            USE_TZ=False,
            INSTALLED_APPS=[],
            DATABASES={
                "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
            },
        )
    django.setup()


def django_observations(task):
    django_setup()
    if task == "django__django-10097":
        from django.core.exceptions import ValidationError
        from django.core.validators import URLValidator

        validator = URLValidator()

        def accepts(url):
            try:
                validator(url)
                return True
            except ValidationError:
                return False

        urls = {
            "slash_username": "http://foo/bar@example.com",
            "slash_password": "http://user:p/word@example.com",
            "colon_password": "http://user:p:word@example.com",
            "at_username": "http://u@name:pass@example.com",
            "query_does_not_rescue_invalid_url": "http://foo/bar@example.com?m=foo@example.com",
            "valid_credentials": "http://user:pass@example.com",
            "encoded_credentials": "http://u%40name:p%2Fword%3Aok@example.com",
            "ordinary_control": "https://example.com/a?x=1",
        }
        return {
            name: attempt(lambda url=url: accepts(url)) for name, url in urls.items()
        }
    if task == "django__django-10999":
        from django.utils.dateparse import parse_duration

        def seconds(value):
            parsed = parse_duration(value)
            return parsed.total_seconds() if parsed is not None else None

        durations = {
            "all_negative": "-1:-1:-1",
            "negative_minutes": "1:-2:3",
            "negative_hours_and_seconds": "-1:2:-3",
            "negative_fraction": "2:-3:-4.500000",
            "days_and_negative_components": "2 days -1:-2:-3",
            "positive_control": "1:02:03",
            "invalid_control": "not-duration",
        }
        return {
            name: attempt(lambda value=value: seconds(value))
            for name, value in durations.items()
        }
    if task == "django__django-10914":
        from django.conf import settings
        from django.core.files.storage import FileSystemStorage
        from django.core.files.uploadedfile import (
            SimpleUploadedFile,
            TemporaryUploadedFile,
        )

        def save_memory(location):
            storage = FileSystemStorage(location=location)
            saved = storage.save(
                "memory.txt", SimpleUploadedFile("memory.txt", b"probe")
            )
            return stat.S_IMODE(os.stat(storage.path(saved)).st_mode)

        def save_temporary(location):
            storage = FileSystemStorage(location=location)
            uploaded = TemporaryUploadedFile("temporary.txt", "text/plain", 5, "utf-8")
            try:
                uploaded.write(b"probe")
                uploaded.seek(0)
                saved = storage.save("temporary.txt", uploaded)
                return stat.S_IMODE(os.stat(storage.path(saved)).st_mode)
            finally:
                uploaded.close()

        with tempfile.TemporaryDirectory(dir="/tmp") as location:
            return {
                "default_permission_setting": settings.FILE_UPLOAD_PERMISSIONS,
                "memory_upload_permissions": attempt(lambda: save_memory(location)),
                "temporary_upload_permissions": attempt(
                    lambda: save_temporary(location)
                ),
            }
    if task == "django__django-10973":
        from django.db.backends.postgresql.client import DatabaseClient

        def shell_observation(password):
            observed = []

            def spy(api):
                def record(arguments, **kwargs):
                    environment = kwargs.get("env") or os.environ
                    observed.append(
                        {
                            "api": api,
                            "argv": list(arguments),
                            "password": environment.get("PGPASSWORD"),
                            "parent_password_during_call": os.environ.get("PGPASSWORD"),
                            "pgpassfile_set": bool(environment.get("PGPASSFILE")),
                        }
                    )
                    return subprocess.CompletedProcess(arguments, 0)

                return record

            original = {
                name: getattr(subprocess, name)
                for name in ("run", "check_call", "call")
            }
            previous = {
                name: os.environ.get(name) for name in ("PGPASSWORD", "PGPASSFILE")
            }
            os.environ["PGPASSWORD"] = "existing-probe-value"
            os.environ.pop("PGPASSFILE", None)
            try:
                for name in original:
                    setattr(subprocess, name, spy(name))
                DatabaseClient.runshell_db(
                    {
                        "host": "localhost",
                        "port": 5432,
                        "database": "probe_db",
                        "user": "probe_user",
                        "password": password,
                    }
                )
                return {
                    "calls": observed,
                    "parent_password_after": os.environ.get("PGPASSWORD"),
                }
            finally:
                for name, value in original.items():
                    setattr(subprocess, name, value)
                for name, value in previous.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value

        return {
            "password_environment": attempt(
                lambda: shell_observation("probe-pass:word")
            ),
            "unicode_password_environment": attempt(
                lambda: shell_observation("päss\\word")
            ),
        }
    if task in ("django__django-10554", "django__django-10880"):
        from django.db import DatabaseError, connection, models

        class ProbeRow(models.Model):
            value = models.IntegerField()

            class Meta:
                app_label = "public_task_probe"

        def database_attempt(operation):
            try:
                return operation()
            except DatabaseError as error:
                return {
                    "exception": type(error).__module__ + "." + type(error).__qualname__
                }

        with connection.schema_editor() as editor:
            editor.create_model(ProbeRow)
        if task == "django__django-10880":
            for value in (1, 1, 2, 0):
                ProbeRow.objects.create(value=value)
            from django.db.models import Case, Count, F, When

            return {
                "conditional_distinct_count": database_attempt(
                    lambda: ProbeRow.objects.aggregate(
                        count=Count(
                            Case(When(value__gte=1, then=F("value"))), distinct=True
                        )
                    )["count"]
                ),
                "distinct_control": database_attempt(
                    lambda: ProbeRow.objects.aggregate(
                        count=Count("value", distinct=True)
                    )["count"]
                ),
            }
        for identifier, order in ((10, 2), (11, 3), (16, 1), (17, 4)):
            ProbeRow.objects.create(id=identifier, value=order)

        def union_observation():
            query = (
                ProbeRow.objects.filter(pk__in=[10, 11])
                .union(ProbeRow.objects.filter(pk__in=[16, 17]))
                .order_by("value")
            )
            repr(query)
            derived = sorted(query.order_by().values_list("pk", flat=True))
            original = [item.pk for item in query]
            repeated = [item.pk for item in query.all()]
            return {
                "derived_ids": derived,
                "original_ids": original,
                "repeated_ids": repeated,
            }

        return {"derived_union_preserves_original": database_attempt(union_observation)}
    raise ValueError("Unsupported Django task.")


def main():
    token, task = sys.argv[1:3]
    sys.dont_write_bytecode = True
    sys.path.insert(0, "/workspace")
    try:
        value = (
            sympy_observations(task)
            if task.startswith("sympy__")
            else django_observations(task)
        )
        response = {"kind": "returned", "value": value}
    except Exception as error:  # noqa: BLE001 - transport unavailable operations as abstentions
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
