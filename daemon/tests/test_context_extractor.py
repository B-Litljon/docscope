"""ContextExtractor: import table, symbol chain, enclosing call."""

from __future__ import annotations

from docscope.context_extractor import ContextExtractor
from docscope.models import BufferContext


def _ctx(text: str, token: str, *, occurrence: int = 1) -> BufferContext:
    """Build a BufferContext with the cursor in the middle of ``token``."""
    idx = -1
    for _ in range(occurrence):
        idx = text.index(token, idx + 1)
    mid = idx + len(token) // 2
    line = text.count("\n", 0, mid)
    col = mid - (text.rfind("\n", 0, mid) + 1)
    return BufferContext(
        file_path="/x/f.py",
        language="python",
        text=text,
        cursor_line=line,
        cursor_col=col,
    )


EX = ContextExtractor()


def test_aliased_import_constructor_chain_collapses():
    text = 'import polars as pl\n\nx = pl.DataFrame({"a": [1]}).join_asof(y, on="ts")\n'
    out = EX.extract(_ctx(text, "join_asof"))
    assert out.symbol == "polars.DataFrame.join_asof"
    assert out.raw_token == "join_asof"


def test_from_import_binds_qualified_name():
    text = "from polars import DataFrame\n\nDataFrame.join_asof\n"
    out = EX.extract(_ctx(text, "join_asof"))
    assert out.symbol == "polars.DataFrame.join_asof"


def test_from_import_alias():
    text = "from numpy import ndarray as NDArray\n\nNDArray.flatten\n"
    out = EX.extract(_ctx(text, "flatten"))
    assert out.symbol == "numpy.ndarray.flatten"


def test_plain_dotted_import():
    text = "import numpy\n\nnumpy.array\n"
    out = EX.extract(_ctx(text, "array"))
    assert out.symbol == "numpy.array"


def test_trailing_dot_is_incomplete():
    text = "import polars as pl\n\npl.DataFrame.\n"
    # cursor just after the trailing dot
    idx = text.index("pl.DataFrame.") + len("pl.DataFrame.")
    line = text.count("\n", 0, idx)
    col = idx - (text.rfind("\n", 0, idx) + 1)
    out = EX.extract(
        BufferContext(
            file_path="/x/f.py", language="python", text=text, cursor_line=line, cursor_col=col
        )
    )
    assert out.incomplete is True
    assert out.symbol == "polars.DataFrame"


def test_half_typed_attribute_resolves_partial_symbol():
    text = "import polars as pl\n\npl.DataFrame().join_as\n"
    out = EX.extract(_ctx(text, "join_as"))
    assert out.symbol == "polars.DataFrame.join_as"


def test_enclosing_call_and_arg_index():
    text = 'import polars as pl\n\npl.read_csv("a.csv", separator=",")\n'
    out = EX.extract(_ctx(text, '","', occurrence=1))
    assert out.enclosing_call == "polars.read_csv"
    assert out.arg_index == 1


def test_local_variable_root_is_not_resolved():
    text = "def f(df):\n    return df.join_asof(other)\n"
    out = EX.extract(_ctx(text, "join_asof"))
    # `df` is a local param, not an import, so we cannot map it to a package.
    assert out.symbol == "df.join_asof"


def test_imports_collected():
    text = "import os\nimport polars as pl\nfrom numpy import array\n"
    out = EX.extract(_ctx(text, "array"))
    locals_ = {b.local_name: b.qualified_name for b in out.imports}
    assert locals_["os"] == "os"
    assert locals_["pl"] == "polars"
    assert locals_["array"] == "numpy.array"
