"""graph schema：节点 / 边 / insights。"""

from typing import Literal

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    id: str  # `{kind}:{slug}` 形式,允许同 slug 不同 kind 共存
    slug: str | None = None  # wiki 路由用的 slug;前端跳 wiki 走这个字段
    title: str
    kind: Literal["entity", "topic", "source"]
    tags: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    book: str | None = None
    community_id: int | None = None
    degree: int = 0


class GraphEdge(BaseModel):
    source: str
    target: str
    type: str
    weight: float = 1.0
    confidence: Literal["EXTRACTED", "INFERRED", "AMBIGUOUS"] = "EXTRACTED"


class GraphInsight(BaseModel):
    kind: Literal["surprising_link", "bridge_node", "knowledge_gap", "sparse_community"]
    title: str
    detail: str
    related_slugs: list[str] = Field(default_factory=list)


class GraphData(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    communities: dict[int, list[str]] = Field(default_factory=dict)
    insights: list[GraphInsight] = Field(default_factory=list)
    generated_at: str | None = None


class GraphRebuildResponse(BaseModel):
    job_id: str
    total_nodes: int
    total_edges: int
