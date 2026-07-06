"""ContextExtractor for Rust and JS/TS."""

from __future__ import annotations

from docscope.context_extractor import ContextExtractor
from docscope.models import BufferContext

EX = ContextExtractor()


def _ctx(text: str, token: str, language: str, *, occurrence: int = 1) -> BufferContext:
    idx = -1
    for _ in range(occurrence):
        idx = text.index(token, idx + 1)
    mid = idx + len(token) // 2
    line = text.count("\n", 0, mid)
    col = mid - (text.rfind("\n", 0, mid) + 1)
    return BufferContext(
        file_path="/x/f", language=language, text=text, cursor_line=line, cursor_col=col
    )


# ---- Rust ----------------------------------------------------------------


def test_rust_use_expands_alias():
    text = "use std::collections::HashMap;\n\nfn main() {\n    let m = HashMap::new();\n}\n"
    out = EX.extract(_ctx(text, "new", "rust"))
    assert out.symbol == "std::collections::HashMap::new"


def test_rust_braced_use():
    text = "use std::collections::{HashMap, BTreeMap};\n\nlet t = BTreeMap::new();\n"
    out = EX.extract(_ctx(text, "new", "rust"))
    assert out.symbol == "std::collections::BTreeMap::new"


def test_rust_full_path_without_use():
    text = "fn f() {\n    let v: serde_json::Value = todo!();\n}\n"
    out = EX.extract(_ctx(text, "Value", "rust"))
    assert out.symbol == "serde_json::Value"


def test_rust_use_with_rename():
    text = "use serde_json::Value as JsonValue;\n\nlet v: JsonValue = x;\n"
    out = EX.extract(_ctx(text, "JsonValue", "rust", occurrence=2))
    assert out.symbol == "serde_json::Value"


# ---- JS / TS -------------------------------------------------------------


def test_js_builtin_member():
    text = "const data = JSON.parse(raw);\n"
    out = EX.extract(_ctx(text, "parse", "javascript"))
    assert out.symbol == "JSON.parse"


def test_js_prototype_chain():
    text = "const f = Array.prototype.map;\n"
    out = EX.extract(_ctx(text, "map", "javascript"))
    assert out.symbol == "Array.prototype.map"


def test_js_import_bindings_collected():
    text = "import _ from 'lodash';\nimport { debounce } from 'lodash';\n_.map(xs);\n"
    out = EX.extract(_ctx(text, "map", "javascript"))
    locals_ = {b.local_name: b.qualified_name for b in out.imports}
    assert locals_["_"] == "lodash"
    assert locals_["debounce"] == "lodash.debounce"
