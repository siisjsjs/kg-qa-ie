"""wiki_aliases：从 .wiki-schema.md 别名词表同步而来，便于 SQL 展开同义词。"""

from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class WikiAlias(Base):
    __tablename__ = "wiki_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    alias: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    group_key: Mapped[str] = mapped_column(String(255), index=True, nullable=False)

    __table_args__ = (
        Index("uq_alias_canonical_alias", "canonical", "alias", unique=True),
    )
