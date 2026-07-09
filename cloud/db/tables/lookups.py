from sqlalchemy import Column, String

from ..base import Base


class Category(Base):
    """Code->title lookup for domains.category_code / crawl_snapshots.category_code.

    Write-time seed target only this phase — domains/crawl_snapshots keep their
    own title columns (see plan.md Phase 1 §2 for why: the frontend dict-shape
    contract and crawl_snapshots' intentional freeze semantics make joining at
    read time a bigger change than this phase scopes)."""

    __tablename__ = "categories"
    code = Column(String, primary_key=True)
    title = Column(String, nullable=False)


class OrgType(Base):
    """Code->title lookup for domains.org_type / crawl_snapshots.org_type."""

    __tablename__ = "org_types"
    code = Column(String, primary_key=True)
    title = Column(String, nullable=False)
