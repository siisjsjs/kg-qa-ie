"""slug_redirects:节点重命名 / 删除孤立页时的旧 → 新映射。

用途:
- 用户重命名一个 wiki 节点(slug 变化),旧的 [[旧名]] 仍能解析
- 删除一份 source 后,某些"仅由该 source 支撑"的实体页变孤立 → 整页删除时
  写一条 redirect(old_slug, new_slug=None, status='gone')以便前端 GET 拿 410

字段:
- old_slug:旧 slug
- new_slug:新 slug;为 None 时表示页面已永久删除(查询时返 410 Gone)
- kind:页面类型(entity/topic/source/...)
- reason:rename / source_deleted / merge / manual
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db import Base


class SlugRedirect(Base):
    __tablename__ = "slug_redirects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    old_slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    new_slug: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), default="rename", nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("uq_slug_redirect_old_kind", "old_slug", "kind", unique=True),
    )
