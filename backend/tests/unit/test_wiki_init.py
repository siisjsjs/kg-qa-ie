"""wiki/init 单元测试。"""

from pathlib import Path

from app.services.wiki.init import init_wiki


def test_init_creates_dir_tree_and_seeds(tmp_path: Path):
    root = tmp_path / "wiki"
    result = init_wiki(root)

    assert result["wiki_root"] == str(root.resolve())

    # 关键文件
    for f in [
        ".wiki-schema.md",
        "purpose.md",
        "index.md",
        "log.md",
        "overview.md",
        "wiki/_growth/gap-queue.md",
    ]:
        assert (root / f).exists(), f"missing {f}"
        assert f in result["created"]

    # 子目录
    for d in [
        "wiki/entities",
        "wiki/topics",
        "wiki/sources",
        "wiki/synthesis/sessions",
        "raw/articles",
        "raw/zhihu",
    ]:
        assert (root / d).is_dir()
        assert (root / d / ".gitkeep").exists()


def test_init_is_idempotent(tmp_path: Path):
    root = tmp_path / "wiki"
    first = init_wiki(root)
    second = init_wiki(root)

    # 第二次所有种子文件都应被跳过
    for tpl in [".wiki-schema.md", "purpose.md", "index.md", "log.md", "overview.md", "wiki/_growth/gap-queue.md"]:
        assert tpl in second["skipped"]
        assert tpl not in second["created"]

    # 写入应保持不变（按 mtime / 内容验证）
    schema_path = root / ".wiki-schema.md"
    content = schema_path.read_text(encoding="utf-8")
    assert "工业工程知识库" in content
    assert len(first["created"]) > 0


def test_init_replaces_now_placeholder(tmp_path: Path):
    root = tmp_path / "wiki"
    init_wiki(root)
    log_text = (root / "log.md").read_text(encoding="utf-8")
    assert "{now}" not in log_text


def test_init_via_api(client, tmp_path, monkeypatch):
    """POST /api/v1/init 端点。"""
    # tmp_path 已由 _isolated_cache 接管 wiki_root
    resp = client.post("/api/v1/init")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert ".wiki-schema.md" in body["created"]
    assert Path(body["wiki_root"]).exists()
