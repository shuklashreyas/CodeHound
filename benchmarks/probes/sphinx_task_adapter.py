"""Issue-derived Sphinx builder observations; no correctness assertions or labels."""

import base64
import html
import io
import json
import re
import sys
import tempfile
import traceback
from pathlib import Path


def emit(token, response):
    raw = json.dumps(response, allow_nan=False, separators=(",", ":")).encode()
    print(
        "\nCODEHOUND_CALL_V1:" + token + ":" + base64.b64encode(raw).decode(),
        flush=True,
    )


def build(sphinx_class, builder, index, conf, module=None):
    temporary = tempfile.TemporaryDirectory(prefix="sphinx-task-")
    root = Path(temporary.name)
    source, output, trees = root / "source", root / "output", root / "trees"
    source.mkdir()
    configuration = (
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent))\n"
        "project='Probe'\nmaster_doc='index'\n"
        "html_theme='alabaster'\n" + conf
    )
    (source / "conf.py").write_text(configuration, encoding="utf-8")
    (source / "index.rst").write_text(index, encoding="utf-8")
    if module is not None:
        (source / "sample.py").write_text(module, encoding="utf-8")
    status, warning = io.StringIO(), io.StringIO()
    app = sphinx_class(
        str(source),
        str(source),
        str(output),
        str(trees),
        builder,
        status=status,
        warning=warning,
        freshenv=True,
        warningiserror=False,
    )
    app.build(force_all=True)
    if app.statuscode:
        raise RuntimeError("Sphinx builder status " + str(app.statuscode))
    return temporary, output


def inline_latex(sphinx_class):
    temporary, output = build(
        sphinx_class,
        "latex",
        "Probe\n=====\n\n.. role:: python(code)\n   :language: python\n"
        "   :class: highlight\n\nInline :python:`x + 1` example.\n\n"
        ".. code-block:: python\n\n   x + 1\n",
        "latex_documents=[('index','probe.tex','Probe','Observer','manual')]\n",
    )
    try:
        text = (output / "probe.tex").read_text(encoding="utf-8")
        prefix = r"\sphinxcode{\sphinxupquote{"
        start = text.index(prefix) + len(prefix)
        depth, end = 1, start
        while depth:
            char = text[end]
            # The generated Pygments payload uses ordinary TeX grouping braces.
            if char == "{" and (end == 0 or text[end - 1] != "\\"):
                depth += 1
            elif char == "}" and (end == 0 or text[end - 1] != "\\"):
                depth -= 1
            end += 1
        payload = text[start : end - 1]
        semantic = re.sub(r"(?<!\\)%[^\n]*\n", "", payload)
        block = text[text.index(r"\begin{sphinxVerbatim}") :]
        return {
            "inline_leading_whitespace": bool(semantic and semantic[0].isspace()),
            "inline_trailing_whitespace": bool(semantic and semantic[-1].isspace()),
            "inline_tokens_preserved": all(
                value in payload for value in ("{x}", "{+}", "{1}")
            ),
            "block_tokens_preserved": all(
                value in block for value in ("{x}", "{+}", "{1}")
            ),
        }
    finally:
        temporary.cleanup()


def autodoc(sphinx_class):
    temporary, output = build(
        sphinx_class,
        "html",
        "Probe\n=====\n\n.. autoclass:: sample.Square\n\n.. autofunction:: sample.double\n",
        "extensions=['sphinx.ext.autodoc']\nautodoc_typehints='description'\n",
        "class Square:\n"
        "    def __init__(self, width: int, height: int) -> None:\n"
        "        self.width, self.height = width, height\n\n"
        "def double(value: int) -> int:\n    return value * 2\n",
    )
    try:
        text = (output / "index.html").read_text(encoding="utf-8")
        class_start, function_start = (
            text.index('id="sample.Square"'),
            text.index('id="sample.double"'),
        )
        plain = lambda value: " ".join(
            html.unescape(re.sub(r"<[^>]+>", " ", value)).split()
        )
        class_text, function_text = (
            plain(text[class_start:function_start]),
            plain(text[function_start:]),
        )
        return {
            "class_return_type_present": "Return type" in class_text,
            "class_parameter_width_present": "width" in class_text
            and "int" in class_text,
            "class_parameter_height_present": "height" in class_text
            and "int" in class_text,
            "function_return_type_preserved": "Return type" in function_text
            and "int" in function_text,
        }
    finally:
        temporary.cleanup()


def gettext(sphinx_class):
    extension = """
from docutils import nodes
def inject(app, doctree):
    for text, line in [('Repeated probe message',42), ('Repeated probe message',42),
                       ('Repeated probe message',42), ('Distinct locations control',43),
                       ('Distinct locations control',44)]:
        paragraph = nodes.paragraph(text, text=text)
        paragraph.source = str(Path(__file__).parent / 'index.rst')
        paragraph.line = line
        doctree += paragraph
def setup(app):
    app.connect('doctree-read', inject)
"""
    temporary, output = build(
        sphinx_class,
        "gettext",
        "Probe\n=====\n\nOrdinary source message.\n",
        extension,
    )
    try:
        text = (output / "index.pot").read_text(encoding="utf-8")

        def references(message):
            for block in text.split("\n\n"):
                if 'msgid "' + message + '"' in block:
                    return [
                        location
                        for line in block.splitlines()
                        if line.startswith("#: ")
                        for location in line[3:].split()
                    ]
            return []

        repeated, distinct = (
            references("Repeated probe message"),
            references("Distinct locations control"),
        )
        return {
            "repeated_reference_count": len(repeated),
            "repeated_unique_reference_count": len(set(repeated)),
            "distinct_location_count": len(distinct),
            "ordinary_message_preserved": 'msgid "Ordinary source message."' in text,
        }
    finally:
        temporary.cleanup()


def main():
    token, task_id = sys.argv[1:3]
    sys.dont_write_bytecode = True
    sys.path.insert(0, "/workspace")
    try:
        import sphinx
        from sphinx.application import Sphinx

        if not Path(sphinx.__file__).resolve().is_relative_to(Path("/workspace")):
            raise RuntimeError("Sphinx did not load from retained source workspace")
    except Exception:  # noqa: BLE001 - dependency/layout failures are abstentions
        traceback.print_exc(file=sys.stderr)
        emit(
            token,
            {
                "kind": "adapter_error",
                "message": "Pinned Sphinx target could not load.",
            },
        )
        return
    functions = {
        "sphinx-doc__sphinx-10435": inline_latex,
        "sphinx-doc__sphinx-10449": autodoc,
        "sphinx-doc__sphinx-10466": gettext,
    }
    try:
        emit(token, {"kind": "returned", "value": functions[task_id](Sphinx)})
    except Exception as exc:  # noqa: BLE001 - builder exceptions are raw observations
        traceback.print_exc(file=sys.stderr)
        emit(
            token,
            {
                "kind": "raised",
                "exception": type(exc).__module__ + "." + type(exc).__qualname__,
            },
        )


if __name__ == "__main__":
    main()
