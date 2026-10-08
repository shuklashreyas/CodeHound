"""Post-hoc public collection diagnostic for metaclass-provided pytest marks."""

import base64
import json
import os
import sys
import types
from pathlib import Path
from tempfile import TemporaryDirectory

SOURCE = """import itertools
import pytest

class BaseMeta(type):
    @property
    def pytestmark(self):
        return (
            getattr(self, "_pytestmark", []) +
            list(itertools.chain.from_iterable(
                getattr(base, "_pytestmark", []) for base in self.__mro__
            ))
        )

    @pytestmark.setter
    def pytestmark(self, value):
        self._pytestmark = value

class MetaBase(metaclass=BaseMeta):
    pass

@pytest.mark.foo
class MetaFoo(MetaBase):
    pass

@pytest.mark.bar
class MetaBar(MetaBase):
    pass

class TestMetaUnion(MetaFoo, MetaBar):
    def test_marks(self):
        pass

class TestMetaSingle(MetaFoo):
    def test_marks(self):
        pass

@pytest.mark.foo
class OrdinaryFoo:
    pass

@pytest.mark.bar
class OrdinaryBar:
    pass

class TestOrdinaryUnion(OrdinaryFoo, OrdinaryBar):
    def test_marks(self):
        pass

class TestOrdinarySingle(OrdinaryFoo):
    def test_marks(self):
        pass
"""


def main():
    token, task = sys.argv[1:3]
    sys.dont_write_bytecode = True
    sys.path[:0] = ["/workspace/src", "/workspace"]
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    # Source checkouts omit setuptools-scm's generated version metadata.
    # This metadata shim does not change the retained source or collection logic.
    if not Path("/workspace/src/_pytest/_version.py").is_file():
        metadata = types.ModuleType("_pytest._version")
        metadata.version = "0.0.0"
        metadata.version_tuple = (0, 0, 0)
        sys.modules["_pytest._version"] = metadata
    try:
        if task != "pytest-dev__pytest-10356":
            raise ValueError("This post-hoc diagnostic supports one task.")
        import pytest

        if not Path(pytest.__file__).resolve().is_relative_to(Path("/workspace")):
            raise RuntimeError(
                "pytest target did not originate in the pinned workspace."
            )
        observations = {}

        class CollectionObserver:
            def pytest_collection_modifyitems(self, items):
                for item in items:
                    observations[item.parent.name] = sorted(
                        {mark.name for mark in item.iter_markers()}
                    )

        with TemporaryDirectory(prefix="pytest-posthoc-marks-") as temporary:
            folder = Path(temporary)
            source = folder / "test_public_marks.py"
            source.write_text(SOURCE)
            config = folder / "pytest.ini"
            config.write_text(
                "[pytest]\nmarkers =\n    foo: control\n    bar: control\n"
            )
            status = pytest.main(
                [
                    "--collect-only",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "-c",
                    str(config),
                    "--rootdir=" + str(folder),
                    "--confcutdir=" + str(folder),
                    str(source),
                ],
                plugins=[CollectionObserver()],
            )
            observations["pytest_exit"] = int(status)
        response = {"kind": "returned", "value": observations}
    except Exception as error:  # noqa: BLE001 - unavailable collection must abstain
        response = {
            "kind": "adapter_error",
            "message": "Public post-hoc collection unavailable: "
            + type(error).__name__,
        }
    payload = json.dumps(response, allow_nan=False, separators=(",", ":")).encode()
    print(
        "\nCODEHOUND_CALL_V1:" + token + ":" + base64.b64encode(payload).decode(),
        flush=True,
    )


if __name__ == "__main__":
    main()
