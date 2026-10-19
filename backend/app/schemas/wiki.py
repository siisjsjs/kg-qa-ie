"""wiki page schema:读 / 写 / 索引 / 反链 / 删除 / 版本历史。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class FrontMatter(BaseModel):
    """通用 frontmatter(不同 kind 字段差异由 services/wiki 解析时处理)。"""

    title: str
    slug: str
    kind: Literal["entity", "topic", "source", "query", "synthesis", "comparison"]
    tags: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    version: int = 1
    confidence: float | None = None
    created: datetime | None = None
    updated: datetime | None = None
    book: str | None = None
    extra: dict = Field(default_factory=dict)


class WikiPageDetail(BaseModel):
    slug: str
    title: str
    kind: str
    file_path: str
    frontmatter: dict
    body: str
    # 前端 MarkdownViewer 读 page.content,兼容沿用
    content: str = ""
    backlinks: list["WikiIndexEntry"] = Field(default_factory=list)
    outlinks: list["WikiIndexEntry"] = Field(default_factory=list)
    version: int


class WikiPageUpdate(BaseModel):
    body: str
    frontmatter: dict | None = None
    bump_version: bool = True


class WikiIndexEntry(BaseModel):
    """索引/反链/引用项:slug + title(用于前端 chip 展示)。"""

    slug: str
    title: str = ""
    kind: str = "entity"


class WikiIndex(BaseModel):
    by_kind: dict[str, list[WikiIndexEntry]] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)
    last_updated: datetime | None = None


# ---------- P3 新增 ----------

class BacklinkItem(BaseModel):
    src_slug: str
    src_title: str
    src_kind: str
    link_type: str = "related"
    occurrences: int = 1


class BacklinksResponse(BaseModel):
    slug: str
    backlinks: list[BacklinkItem] = Field(default_factory=list)
    total: int = 0


class DeleteSourceResponse(BaseModel):
    ok: bool = True
    source_slug: str
    removed_pages: list[str] = Field(default_factory=list)
    updated_pages: list[str] = Field(default_factory=list)
    broken_links: list[dict] = Field(default_factory=list)
    cache_evicted: int = 0
    redirects_written: int = 0


class DeletePageResponse(BaseModel):
    ok: bool = True
    slug: str
    kind: str
    removed_path: str = ""
    broken_links: list[dict] = Field(default_factory=list)
    redirects_written: int = 0
    # 当 slug 实际是 source kind 时,后端走级联,这两个字段填 source-style 结果
    cascaded: bool = False
    removed_pages: list[str] = Field(default_factory=list)
    updated_pages: list[str] = Field(default_factory=list)


class NodeVersionListItem(BaseModel):
    version: int
    triggered_by: str
    actor_user_id: int | None = None
    title_snapshot: str | None = None
    document_id: int | None = None
    created_at: datetime
    diff_stat: dict = Field(default_factory=dict)  # {added_lines, removed_lines, ...}


class NodeVersionList(BaseModel):
    slug: str
    total: int
    versions: list[NodeVersionListItem] = Field(default_factory=list)


class NodeVersionDetail(BaseModel):
    slug: str
    version: int
    triggered_by: str
    actor_user_id: int | None = None
    title_snapshot: str | None = None
    document_id: int | None = None
    created_at: datetime
    diff: dict


# ---------- 结构化 Wiki 浏览(按 book 切片)----------


class BookSummary(BaseModel):
    book: str
    chapter_count: int = 0
    topic_count: int = 0
    entity_count: int = 0


class BookTotals(BaseModel):
    books: int = 0
    chapters: int = 0
    topics: int = 0
    entities: int = 0


class BookIndex(BaseModel):
    books: list[BookSummary] = Field(default_factory=list)
    totals: BookTotals = Field(default_factory=BookTotals)


class BookEntityNode(BaseModel):
    slug: str
    title: str
    kind: Literal["entity"] = "entity"
    exists: bool = True


class BookTopicNode(BaseModel):
    slug: str
    title: str
    kind: Literal["topic"] = "topic"
    entities: list[BookEntityNode] = Field(default_factory=list)


class BookChapterNode(BaseModel):
    slug: str
    title: str
    kind: Literal["source"] = "source"
    topics: list[BookTopicNode] = Field(default_factory=list)
    orphan_entities: list[BookEntityNode] = Field(default_factory=list)


class BookTree(BaseModel):
    book: str
    chapter_count: int = 0
    chapters: list[BookChapterNode] = Field(default_factory=list)


# ---------- 知识库管理(管理员)----------


class CreateBookRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class CreateBookResponse(BaseModel):
    ok: bool = True
    book: str
    created: bool = True  # False = 已存在


class ReorderChaptersRequest(BaseModel):
    # 章节 slug 按目标顺序排列;后端据此写各 source 的 frontmatter.order
    slugs: list[str] = Field(default_factory=list)


class ReorderChaptersResponse(BaseModel):
    ok: bool = True
    book: str
    updated: int = 0


class RenamePageRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class MoveChapterRequest(BaseModel):
    book: str = Field(min_length=1, max_length=80)


class SimpleOkResponse(BaseModel):
    ok: bool = True
    slug: str
    title: str = ""
    book: str = ""


# ---------- 问答数据(管理员页用)----------


class QaDataItem(BaseModel):
    slug: str
    kind: Literal["query", "synthesis", "comparison"]
    title: str
    user_question: str | None = None
    asked_by: int | None = None
    asked_by_username: str | None = None
    created: datetime | None = None
    updated: datetime | None = None
    citations: list[str] = Field(default_factory=list)
    citation_count: int = 0
    answer_quality: float | None = None
    tier: str | None = None
    gap_flag: bool | None = None
    session_id: int | None = None


class QaDataList(BaseModel):
    items: list[QaDataItem] = Field(default_factory=list)
    total: int = 0
    counts_by_kind: dict[str, int] = Field(default_factory=dict)


# ---------- 百科搜索 + 关系聚合 ----------


class SearchResultItem(BaseModel):
    slug: str
    title: str
    kind: str
    # title_exact / title_prefix / title_substr / alias —— 用于前端分组与排序提示
    matched_via: str = "title"
    alias_hit: str | None = None  # 命中别名时回填命中的别名词


class SearchResponse(BaseModel):
    query: str
    items: list[SearchResultItem] = Field(default_factory=list)
    total: int = 0


class RelatedEntity(BaseModel):
    slug: str
    title: str
    kind: str = "entity"
    rel_type: str = "related"
    direction: Literal["out", "in", "both"] = "out"
    occurrences: int = 1
    exists: bool = True
    # semantic = 节点间语义关系(实现/依赖/对比…);containment = 纵向归属(主题含实体 / 素材生成主题)
    edge_kind: Literal["semantic", "containment"] = "semantic"


class SourceRef(BaseModel):
    slug: str
    title: str
    book: str | None = None
    exists: bool = True


class MentioningTopic(BaseModel):
    slug: str
    title: str
    kind: Literal["topic"] = "topic"
    sources: list[SourceRef] = Field(default_factory=list)


class MiniGraphNode(BaseModel):
    id: str
    slug: str
    title: str
    kind: str
    is_center: bool = False


class MiniGraphEdge(BaseModel):
    source: str
    target: str
    rel_type: str = "related"
    weight: float = 0.0
    # semantic = 实线彩色(节点间语义);containment = 虚线灰(归属/生成)
    edge_kind: Literal["semantic", "containment"] = "semantic"


class MiniGraph(BaseModel):
    nodes: list[MiniGraphNode] = Field(default_factory=list)
    edges: list[MiniGraphEdge] = Field(default_factory=list)


class RelationsResponse(BaseModel):
    slug: str
    title: str
    kind: str
    related_entities: list[RelatedEntity] = Field(default_factory=list)
    mentioned_by_topics: list[MentioningTopic] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    mini_graph: MiniGraph = Field(default_factory=MiniGraph)
