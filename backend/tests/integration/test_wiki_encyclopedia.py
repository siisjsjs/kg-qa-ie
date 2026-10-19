"""百科改造端点集成测试:/wiki/search + /wiki/pages/{slug}/relations。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core import config as cfg_module
from app.core.db import session_scope
from app.models.wiki_alias import WikiAlias
from app.models.wiki_link import WikiLink
from app.models.wiki_page import WikiPage


@pytest.fixture
def wiki_root(tmp_path, monkeypatch):
    from app.services.wiki.init import init_wiki

    root = tmp_path / "wiki_root"
    init_wiki(root)

    # source(章节,带 book)
    (root / "wiki" / "sources" / "s1.md").write_text(
        "---\ntitle: 基础工业工程\nslug: s1\nkind: source\nbook: 基础工业工程\n---\n素材正文。\n",
        encoding="utf-8",
    )
    # entity 工效学,引用 人因工程
    (root / "wiki" / "entities" / "工效学.md").write_text(
        "---\ntitle: 工效学\nslug: 工效学\nkind: entity\nsources: [s1]\n"
        "aliases: [ergonomics, 人因工程学]\n---\n相关 [[人因工程]]。\n",
        encoding="utf-8",
    )
    (root / "wiki" / "entities" / "人因工程.md").write_text(
        "---\ntitle: 人因工程\nslug: 人因工程\nkind: entity\nsources: [s1]\n---\n[[工效学]]。\n",
        encoding="utf-8",
    )
    # topic 提及 工效学
    (root / "wiki" / "topics" / "t1.md").write_text(
        "---\ntitle: 人因主题\nslug: t1\nkind: topic\nsources: [s1]\n"
        "related_entities: [工效学]\n---\n讨论 [[工效学]]。\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg_module.settings, "wiki_root", str(root))
    return root


def _seed_db():
    """灌入 wiki_pages / wiki_links / wiki_aliases 索引,模拟 ingest 后状态。"""
    with session_scope() as s:
        s.add_all([
            WikiPage(slug="s1", title="基础工业工程", kind="source",
                     file_path="wiki/sources/s1.md",
                     frontmatter_json=json.dumps({"book": "基础工业工程"})),
            WikiPage(slug="工效学", title="工效学", kind="entity",
                     file_path="wiki/entities/工效学.md",
                     frontmatter_json=json.dumps({"sources": ["s1"]})),
            WikiPage(slug="人因工程", title="人因工程", kind="entity",
                     file_path="wiki/entities/人因工程.md",
                     frontmatter_json=json.dumps({"sources": ["s1"]})),
            WikiPage(slug="t1", title="人因主题", kind="topic",
                     file_path="wiki/topics/t1.md",
                     frontmatter_json=json.dumps({"sources": ["s1"]})),
        ])
        s.add_all([
            WikiLink(src_slug="工效学", dst_slug="人因工程", link_type="相关", occurrences=3),
            WikiLink(src_slug="t1", dst_slug="工效学", link_type="related", occurrences=1),
        ])
        s.add(WikiAlias(canonical="工效学", alias="ergonomics", group_key="工效学"))


# ===== /wiki/search =====
def test_search_by_title(wiki_root):
    from app.main import app

    _seed_db()
    with TestClient(app) as client:
        resp = client.get("/api/v1/wiki/search", params={"q": "工效"})
        assert resp.status_code == 200
        data = resp.json()
        slugs = {it["slug"] for it in data["items"]}
        assert "工效学" in slugs
        hit = next(it for it in data["items"] if it["slug"] == "工效学")
        assert hit["kind"] == "entity"


def test_search_by_alias(wiki_root):
    from app.main import app

    _seed_db()
    with TestClient(app) as client:
        resp = client.get("/api/v1/wiki/search", params={"q": "ergonomics"})
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert any(it["slug"] == "工效学" and it["matched_via"] == "alias" for it in items)


def test_search_kind_filter(wiki_root):
    from app.main import app

    _seed_db()
    with TestClient(app) as client:
        resp = client.get("/api/v1/wiki/search", params={"q": "工", "kind": "topic"})
        assert resp.status_code == 200
        kinds = {it["kind"] for it in resp.json()["items"]}
        assert kinds <= {"topic"}


def test_search_fallback_scan_when_db_empty(wiki_root):
    """wiki_pages 未同步 → 降级全盘扫描标题仍能命中。"""
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/v1/wiki/search", params={"q": "人因工程"})
        assert resp.status_code == 200
        slugs = {it["slug"] for it in resp.json()["items"]}
        assert "人因工程" in slugs


# ===== /wiki/pages/{slug}/relations =====
def test_relations_related_entities_and_topics(wiki_root):
    from app.main import app

    _seed_db()
    with TestClient(app) as client:
        resp = client.get("/api/v1/wiki/pages/工效学/relations")
        assert resp.status_code == 200
        data = resp.json()
        # 相关实体:人因工程(出边)
        rel_slugs = {r["slug"] for r in data["related_entities"]}
        assert "人因工程" in rel_slugs
        ce = next(r for r in data["related_entities"] if r["slug"] == "人因工程")
        assert ce["rel_type"] == "相关"
        assert ce["edge_kind"] == "semantic"
        # 提及它的主题:t1,且 t1 的 sources 含 s1(带 book)
        topic_slugs = {t["slug"] for t in data["mentioned_by_topics"]}
        assert "t1" in topic_slugs
        t1 = next(t for t in data["mentioned_by_topics"] if t["slug"] == "t1")
        assert any(src["slug"] == "s1" and src["book"] == "基础工业工程" for src in t1["sources"])
        # 自身 sources
        assert any(src["slug"] == "s1" for src in data["sources"])


def test_relations_entity_graph_matches_related_entities(wiki_root):
    """实体关系图节点 = 中心 + related_entities,不读 graph-data.json。"""
    from app.main import app

    _seed_db()
    with TestClient(app) as client:
        resp = client.get("/api/v1/wiki/pages/工效学/relations")
        assert resp.status_code == 200
        data = resp.json()
        nodes = data["mini_graph"]["nodes"]
        assert nodes, "expected a non-empty ego graph"
        assert all(n["kind"] == "entity" for n in nodes)
        rel = {r["slug"] for r in data["related_entities"]}
        neighbors = {n["slug"] for n in nodes if not n["is_center"]}
        assert rel == neighbors


def test_relations_404(wiki_root):
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/v1/wiki/pages/不存在/relations")
        assert resp.status_code == 404


def test_relations_fallback_backlinks_when_links_empty(wiki_root):
    """wiki_links 未同步 → mentioned_by_topics 走 find_backlinks 文件扫描。"""
    from app.main import app

    # 只灌页面索引,不灌 links
    with session_scope() as s:
        s.add_all([
            WikiPage(slug="工效学", title="工效学", kind="entity",
                     file_path="wiki/entities/工效学.md", frontmatter_json="{}"),
            WikiPage(slug="t1", title="人因主题", kind="topic",
                     file_path="wiki/topics/t1.md", frontmatter_json="{}"),
        ])

    with TestClient(app) as client:
        resp = client.get("/api/v1/wiki/pages/工效学/relations")
        assert resp.status_code == 200
        topic_slugs = {t["slug"] for t in resp.json()["mentioned_by_topics"]}
        assert "t1" in topic_slugs


def test_relations_topic_members_containment(wiki_root):
    """主题页:related_entities = 成员实体(containment),mini_graph 只有中心→成员包含边,不叠加语义边。"""
    from app.main import app

    _seed_db()
    with TestClient(app) as client:
        resp = client.get("/api/v1/wiki/pages/t1/relations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["kind"] == "topic"
        # 成员实体来自 frontmatter.related_entities
        member_slugs = {r["slug"] for r in data["related_entities"]}
        assert "工效学" in member_slugs
        member = next(r for r in data["related_entities"] if r["slug"] == "工效学")
        assert member["edge_kind"] == "containment"
        # mini_graph 中心是 topic:t1,且有一条到成员的 containment 边
        center = next(n for n in data["mini_graph"]["nodes"] if n["is_center"])
        assert center["kind"] == "topic"
        assert any(
            e["edge_kind"] == "containment" and e["source"] == center["id"]
            for e in data["mini_graph"]["edges"]
        )
        # 主题图谱不叠加成员间语义边:所有边都是 containment
        assert all(e["edge_kind"] == "containment" for e in data["mini_graph"]["edges"])


def test_relations_source_derived_topics(wiki_root):
    """素材页:related_entities = 衍生主题(kind=topic, containment)。"""
    from app.main import app

    _seed_db()
    with TestClient(app) as client:
        resp = client.get("/api/v1/wiki/pages/s1/relations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["kind"] == "source"
        # t1 的 sources 含 s1 → s1 衍生出 t1
        derived = {r["slug"] for r in data["related_entities"]}
        assert "t1" in derived
        t = next(r for r in data["related_entities"] if r["slug"] == "t1")
        assert t["kind"] == "topic"
        assert t["edge_kind"] == "containment"
        # mini_graph 中心是 source:s1
        center = next(n for n in data["mini_graph"]["nodes"] if n["is_center"])
        assert center["kind"] == "source"
