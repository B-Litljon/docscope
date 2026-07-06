"""Extract structured facts from a Python buffer using tree-sitter.

Strategy: tree-sitter gives us reliable *structure* — the import table and the
enclosing call expression / argument position. For the dotted symbol *under or
before the cursor* we combine that with a small backward text scan, which
degrades gracefully on the half-typed expressions the daemon sees constantly
(``pl.DataFrame().join_as``) where a strict parse would yield an ERROR node.

The extractor resolves the scanned chain against the import table so
``pl.DataFrame.join_asof`` becomes the fully-qualified ``polars.DataFrame.
join_asof``, and it collapses constructor calls (``pl.DataFrame().join_asof``)
to the class symbol so method lookups resolve without type inference.
"""

from __future__ import annotations

import re
from functools import lru_cache

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

from .models import BufferContext, ExtractedContext, ImportBinding


@lru_cache(maxsize=1)
def _parser() -> Parser:
    return Parser(Language(tspython.language()))


def _cursor_byte(text: str, line: int, col: int) -> int:
    """Translate a (0-based line, 0-based column) into a utf-8 byte offset.

    ``col`` is treated as a character index into the line (editor convention);
    for non-ASCII lines this is converted to a byte offset.
    """
    lines = text.splitlines(keepends=True)
    if line >= len(lines):
        return len(text.encode("utf-8"))
    prefix_bytes = sum(len(lines[i].encode("utf-8")) for i in range(line))
    line_text = lines[line]
    char_prefix = line_text[: min(col, len(line_text))]
    return prefix_bytes + len(char_prefix.encode("utf-8"))


class ContextExtractor:
    """Language-specific context extraction. This implementation handles
    Python; other languages are added in M4."""

    supported_languages = {"python"}

    def extract(self, ctx: BufferContext) -> ExtractedContext:
        if ctx.language != "python":
            return ExtractedContext(language=ctx.language, incomplete=True)

        text = ctx.text
        # Translate absolute cursor -> window-relative line for the sent text.
        rel_line = ctx.cursor_line - ctx.window_start_line
        rel_line = max(0, rel_line)
        data = text.encode("utf-8")
        tree = _parser().parse(data)
        root = tree.root_node

        imports = self._imports(root, data)
        import_map = {b.local_name: b for b in imports}

        cur_byte = _cursor_byte(text, rel_line, ctx.cursor_col)
        raw_chain, incomplete = self._scan_chain(text, cur_byte)
        symbol, raw_token = self._resolve_chain(raw_chain, import_map)

        enclosing_call, arg_index = self._enclosing_call(root, data, cur_byte, import_map)

        return ExtractedContext(
            language="python",
            symbol=symbol,
            raw_token=raw_token,
            enclosing_call=enclosing_call,
            arg_index=arg_index,
            imports=imports,
            incomplete=incomplete,
        )

    # ---- imports ---------------------------------------------------------

    def _imports(self, root: Node, data: bytes) -> list[ImportBinding]:
        bindings: list[ImportBinding] = []

        def node_text(n: Node) -> str:
            return data[n.start_byte : n.end_byte].decode("utf-8", "replace")

        def top_package(qualified: str) -> str:
            return qualified.split(".", 1)[0]

        # Walk the whole tree; import statements can be nested (inside try, etc.)
        stack = [root]
        while stack:
            node = stack.pop()
            if node.type == "import_statement":
                for child in node.named_children:
                    if child.type == "dotted_name":
                        qualified = node_text(child)
                        local = top_package(qualified)
                        bindings.append(
                            ImportBinding(
                                local_name=local,
                                qualified_name=qualified,
                                package=top_package(qualified),
                            )
                        )
                    elif child.type == "aliased_import":
                        name_node = child.child_by_field_name("name")
                        alias_node = child.child_by_field_name("alias")
                        if name_node and alias_node:
                            qualified = node_text(name_node)
                            bindings.append(
                                ImportBinding(
                                    local_name=node_text(alias_node),
                                    qualified_name=qualified,
                                    package=top_package(qualified),
                                )
                            )
            elif node.type == "import_from_statement":
                mod_node = node.child_by_field_name("module_name")
                if mod_node is None or mod_node.type == "relative_import":
                    continue
                module = node_text(mod_node)
                pkg = top_package(module)
                # names imported: dotted_name / aliased_import children after module
                for child in node.named_children:
                    if child is mod_node:
                        continue
                    if child.type == "dotted_name":
                        name = node_text(child)
                        bindings.append(
                            ImportBinding(
                                local_name=name,
                                qualified_name=f"{module}.{name}",
                                package=pkg,
                            )
                        )
                    elif child.type == "aliased_import":
                        name_node = child.child_by_field_name("name")
                        alias_node = child.child_by_field_name("alias")
                        if name_node and alias_node:
                            name = node_text(name_node)
                            bindings.append(
                                ImportBinding(
                                    local_name=node_text(alias_node),
                                    qualified_name=f"{module}.{name}",
                                    package=pkg,
                                )
                            )
            else:
                stack.extend(node.children)
        return bindings

    # ---- symbol chain ----------------------------------------------------

    def _scan_chain(self, text: str, cur_byte: int) -> tuple[str | None, bool]:
        """Return (dotted_chain, incomplete) for the expression at the cursor.

        Scans backward from the cursor over a dotted attribute access that may
        contain balanced ``(...)`` / ``[...]`` groups. Also extends *forward*
        so a cursor sitting inside a token captures the whole token.
        """
        data = text.encode("utf-8")
        # Extend forward through the current identifier so we get the full token
        # even when the cursor is in its middle.
        end = cur_byte
        while end < len(data):
            ch = chr(data[end])
            if ch.isalnum() or ch == "_":
                end += 1
            else:
                break
        prefix = data[:end].decode("utf-8", "replace")

        i = len(prefix)
        incomplete = False
        # Detect a trailing dot: "foo." — object access with no attribute yet.
        stripped = prefix.rstrip()
        if stripped.endswith("."):
            incomplete = True

        chars: list[str] = []
        depth = 0
        while i > 0:
            c = prefix[i - 1]
            if c in ")]":
                depth += 1
                i -= 1
                continue
            if c in "([":
                if depth == 0:
                    break
                depth -= 1
                i -= 1
                continue
            if depth > 0:
                i -= 1
                continue
            if c.isalnum() or c in "_.":
                chars.append(c)
                i -= 1
                continue
            break
        chain = "".join(reversed(chars))
        # Collapse the balanced groups we walked over: "pl.DataFrame().join" was
        # scanned as "pl.DataFrame.join" already (group content skipped). Clean
        # up any doubled/leading/trailing dots left by the group removal.
        chain = re.sub(r"\.{2,}", ".", chain).strip(".")
        if not chain:
            return None, incomplete
        return chain, incomplete

    def _resolve_chain(
        self, chain: str | None, import_map: dict[str, ImportBinding]
    ) -> tuple[str | None, str | None]:
        if not chain:
            return None, None
        parts = chain.split(".")
        root = parts[0]
        raw_token = parts[-1]
        binding = import_map.get(root)
        if binding is None:
            # Cannot map to a package deterministically; hand the raw chain on.
            return chain, raw_token
        tail = parts[1:]
        qualified = ".".join([binding.qualified_name, *tail]) if tail else binding.qualified_name
        return qualified, raw_token

    # ---- enclosing call --------------------------------------------------

    def _enclosing_call(
        self,
        root: Node,
        data: bytes,
        cur_byte: int,
        import_map: dict[str, ImportBinding],
    ) -> tuple[str | None, int | None]:
        """Find the innermost call whose argument list contains the cursor."""
        node = root.descendant_for_byte_range(max(0, cur_byte - 1), cur_byte)
        best: Node | None = None
        while node is not None:
            if node.type == "call":
                args = node.child_by_field_name("arguments")
                if args and args.start_byte <= cur_byte <= args.end_byte:
                    best = node
                    break
            node = node.parent
        if best is None:
            return None, None

        func_node = best.child_by_field_name("function")
        args_node = best.child_by_field_name("arguments")
        callee = None
        if func_node is not None:
            raw = data[func_node.start_byte : func_node.end_byte].decode("utf-8", "replace")
            resolved, _ = self._resolve_chain(re.sub(r"\.{2,}", ".", raw).strip("."), import_map)
            callee = resolved

        arg_index = 0
        if args_node is not None:
            depth = 0
            for i in range(args_node.start_byte, min(cur_byte, args_node.end_byte)):
                ch = chr(data[i])
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                elif ch == "," and depth <= 1:
                    arg_index += 1
        return callee, arg_index
