"""End-to-end pipeline test, fully offline via the fixture inventory + HTML."""

from __future__ import annotations

from pathlib import Path

from docscope.config import Config, RegistryOverride
from docscope.models import BufferContext, DocCard
from docscope.pipeline import Pipeline

from .conftest import INVENTORY_URL


def _config(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.cache.dir = str(tmp_path)
    cfg.registry = {"testpkg": RegistryOverride(inv_url=INVENTORY_URL, versioned=True)}
    return cfg


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\ndependencies=["testpkg==1.0"]\n', "utf-8"
    )
    (ws / "main.py").write_text("import testpkg\n\ntestpkg.Thing.do_it\n", "utf-8")
    return ws


async def test_full_pipeline_produces_card(tmp_path: Path, mock_client):
    ws = _workspace(tmp_path)
    ctx = BufferContext(
        file_path=str(ws / "main.py"),
        language="python",
        text=(ws / "main.py").read_text(),
        cursor_line=2,
        cursor_col=len("testpkg.Thing.do"),  # inside do_it
        workspace_root=str(ws),
    )
    pipeline = Pipeline(_config(tmp_path))
    await pipeline.start(client=mock_client())
    try:
        card = await pipeline.lookup(ctx)
        assert isinstance(card, DocCard)
        assert card.symbol == "testpkg.Thing.do_it"
        assert card.package == "testpkg" and card.version == "1.0"
        assert card.exact_version is True
        assert card.cache_tier == "T2"
        assert card.signature and "do_it" in card.signature
        assert card.example_md and ">>>" in card.example_md
        assert "Docs may not be pinned" not in " ".join(card.warnings)

        # Warm: inventory + doc page both cached -> T1, fast.
        card2 = await pipeline.lookup(ctx)
        assert isinstance(card2, DocCard)
        assert card2.cache_tier == "T1"
    finally:
        await pipeline.close()


async def test_unresolved_symbol_returns_lookup_error(tmp_path: Path, mock_client):
    ws = _workspace(tmp_path)
    (ws / "main.py").write_text("x = 1\ny = x.bit_length\n", "utf-8")
    ctx = BufferContext(
        file_path=str(ws / "main.py"),
        language="python",
        text=(ws / "main.py").read_text(),
        cursor_line=1,
        cursor_col=len("y = x.bit_len"),
        workspace_root=str(ws),
    )
    pipeline = Pipeline(_config(tmp_path))
    await pipeline.start(client=mock_client())
    try:
        result = await pipeline.lookup(ctx)
        from docscope.models import LookupError

        assert isinstance(result, LookupError)
        assert result.reason == "unresolved"
    finally:
        await pipeline.close()
