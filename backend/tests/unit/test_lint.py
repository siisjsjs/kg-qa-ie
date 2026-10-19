"""lint 模块单元测试 — P5。"""

from __future__ import annotations

import pytest

from app.services.lint.fix import regenerate_index
from app.services.lint.runner import run_lint


def _seed(root, files: dict[str, str]):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


@pytest.mark.asyncio
async def test_lint_orphan_detection(tmp_path):
    _seed(tmp_path, {
        "wiki/entities/孤立实体.md": (
            "---\ntitle: 孤立\nslug: 孤立实体\nkind: entity\n---\n\n## 简介\n\n孤立。\n"
        ),
        "wiki/entities/连接实体.md": (
            "---\ntitle: 连接\nslug: 连接实体\nkind: entity\n---\n\n## 简介\n见 [[孤立实体]]\n"
        ),
    })
    out = await run_lint(wiki_root=tmp_path)
    issues = out["issues"]
    # 孤立实体有入边(被连接实体引用),不算 orphan
    # 连接实体没入边 → orphan?但它有出边,所以不算
    # 验证逻辑:两页都不是 orphan
    orphans = [i for i in issues if i["kind"] == "orphan"]
    assert all(o["slug"] != "孤立实体" for o in orphans)
    assert all(o["slug"] != "连接实体" for o in orphans)


@pytest.mark.asyncio
async def test_lint_orphan_real(tmp_path):
    _seed(tmp_path, {
        "wiki/entities/真孤立.md": "---\ntitle: 真\nslug: 真孤立\nkind: entity\n---\n\n孤立。\n",
        "wiki/entities/A.md": "---\ntitle: A\nslug: A\nkind: entity\n---\n\n[[B]]\n",
        "wiki/entities/B.md": "---\ntitle: B\nslug: B\nkind: entity\n---\n\n[[A]]\n",
    })
    out = await run_lint(wiki_root=tmp_path)
    orphans = [i["slug"] for i in out["issues"] if i["kind"] == "orphan"]
    assert "真孤立" in orphans


@pytest.mark.asyncio
async def test_lint_broken_link(tmp_path):
    _seed(tmp_path, {
        "wiki/entities/A.md": "---\ntitle: A\nslug: A\nkind: entity\n---\n\n[[不存在的页]]\n",
    })
    out = await run_lint(wiki_root=tmp_path)
    broken = [i for i in out["issues"] if i["kind"] == "broken_link"]
    assert any("不存在的页" in i["detail"] for i in broken)


@pytest.mark.asyncio
async def test_lint_duplicate_titles(tmp_path):
    _seed(tmp_path, {
        "wiki/entities/A1.md": (
            "---\ntitle: 工效学\nslug: A1\nkind: entity\n---\n[[B]]\n"
        ),
        "wiki/entities/A2.md": (
            "---\ntitle: 工效学\nslug: A2\nkind: entity\n---\n[[B]]\n"
        ),
        "wiki/entities/B.md": "---\ntitle: B\nslug: B\nkind: entity\n---\n[[A1]]\n[[A2]]\n",
    })
    out = await run_lint(wiki_root=tmp_path)
    duplicates = [i for i in out["issues"] if i["kind"] == "duplicate"]
    assert len(duplicates) >= 2


@pytest.mark.asyncio
async def test_lint_low_confidence(tmp_path):
    _seed(tmp_path, {
        "wiki/entities/X.md": (
            "---\ntitle: X\nslug: X\nkind: entity\nconfidence: AMBIGUOUS\n---\n[[Y]]\n"
        ),
        "wiki/entities/Y.md": "---\ntitle: Y\nslug: Y\nkind: entity\n---\n[[X]]\n",
    })
    out = await run_lint(wiki_root=tmp_path)
    lc = [i for i in out["issues"] if i["kind"] == "low_confidence"]
    assert any(i["slug"] == "X" for i in lc)


@pytest.mark.asyncio
async def test_lint_summary(tmp_path):
    _seed(tmp_path, {
        "wiki/entities/A.md": "---\ntitle: A\nslug: A\nkind: entity\n---\n[[X]]\n",
    })
    out = await run_lint(wiki_root=tmp_path)
    assert "broken_link" in out["summary"]
    assert isinstance(out["summary"]["broken_link"], int)


def test_regenerate_index_writes_index(tmp_path):
    _seed(tmp_path, {
        "wiki/entities/工效学.md": (
            "---\ntitle: 工效学\nslug: 工效学\nkind: entity\n---\n\n内容\n"
        ),
        "wiki/topics/精益.md": (
            "---\ntitle: 精益生产路径\nslug: 精益\nkind: topic\n---\n\n内容\n"
        ),
    })
    out_path = regenerate_index(tmp_path)
    txt = out_path.read_text(encoding="utf-8")
    assert "知识库索引" in txt
    assert "[[工效学]]" in txt
    assert "[[精益|精益生产路径]]" in txt
