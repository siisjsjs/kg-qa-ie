"""SQLAlchemy ORM 模型集合。

对应规划文档 §2.3.4,14 张表 + P3 新增 slug_redirects:
  users / documents / wiki_pages / wiki_links / wiki_aliases /
  qa_sessions / qa_messages / gap_queue / hot_topics / followup_queue /
  ingest_jobs / system_config / prompt_versions / wiki_node_versions /
  slug_redirects (P3)
"""

from app.models.announcement import Announcement
from app.models.document import Document
from app.models.feedback import Feedback
from app.models.followup_queue import FollowupQueue
from app.models.gap_queue import GapQueue
from app.models.hot_topic import HotTopic
from app.models.ingest_job import IngestJob
from app.models.prompt_version import PromptVersion
from app.models.qa import QaMessage, QaSession
from app.models.slug_redirect import SlugRedirect
from app.models.system_config import SystemConfig
from app.models.user import User
from app.models.wiki_alias import WikiAlias
from app.models.wiki_link import WikiLink
from app.models.wiki_node_version import WikiNodeVersion
from app.models.wiki_page import WikiPage

__all__ = [
    "Announcement",
    "Document",
    "Feedback",
    "FollowupQueue",
    "GapQueue",
    "HotTopic",
    "IngestJob",
    "PromptVersion",
    "QaMessage",
    "QaSession",
    "SlugRedirect",
    "SystemConfig",
    "User",
    "WikiAlias",
    "WikiLink",
    "WikiNodeVersion",
    "WikiPage",
]
