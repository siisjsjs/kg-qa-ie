"""Compare backup vs live wiki bodies and wiki_links for hub pages."""
from __future__ import annotations

import json
import re
import tarfile
from collections import Counter
from pathlib import Path

LIVE = Path("/app/data/wiki")
BACKUP = Path("/app/data/wiki_backup_20260822.tar.gz")
LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")

SAMPLES = [
    "wiki/entities/工业工程.md",
    "wiki/entities/精益生产.md",
    "wiki/entities/方法研究.md",
    "wiki/entities/动作分析.md",
    "wiki/topics/方法研究体系.md",
]


def body_of(text: str) -> str:
    if text.startswith("---"):
        rest = text[3:]
        end = rest.find("\n---")
        if end >= 0:
            return rest[end + 4 :]
    return text


def slugs(text: str) -> list[str]:
    return [m.strip() for m in LINK_RE.findall(text or "")]


def main():
    report = json.loads(Path("/app/data/wiki/_cleanup_report.json").read_text(encoding="utf-8"))
    summary = report.get("summary") or {}
    print("cleanup_summary")
    for k, v in summary.items():
        if k != "rows":
            print(" ", k, v)

    with tarfile.open(BACKUP, "r:gz") as tf:
        members = {n.replace("\\", "/"): n for n in tf.getnames()}

        def backup_text(rel: str) -> str | None:
            key = "wiki/" + rel if not rel.startswith("wiki/") else rel
            # backup stores wiki/wiki/entities/...
            for cand in (key, "wiki/" + key, key.replace("wiki/", "wiki/wiki/", 1)):
                raw = members.get(cand)
                if raw:
                    f = tf.extractfile(raw)
                    return f.read().decode("utf-8", errors="replace") if f else None
            return None

        print("\nSAMPLE backup vs live [[links]]")
        for rel in SAMPLES:
            live_p = LIVE / "wiki" / rel.replace("wiki/", "")
            if not live_p.exists():
                live_p = LIVE / rel.replace("wiki/", "")
            live = live_p.read_text(encoding="utf-8") if live_p.exists() else ""
            old = backup_text("wiki/" + rel.replace("wiki/", "")) or backup_text(rel)
            lb, ob = body_of(live), body_of(old or "")
            ls, os_ = slugs(lb), slugs(ob)
            new = [s for s in dict.fromkeys(ls) if s not in set(os_)]
            gone = [s for s in dict.fromkeys(os_) if s not in set(ls)]
            print(
                f"  {rel} live_exists={live_p.exists()} backup={'yes' if old else 'NO'}"
                f" backup_n={len(os_)} live_n={len(ls)}"
                f" unique {len(set(os_))}->{len(set(ls))}"
                f" new={new[:10]} gone={gone[:6]}"
            )
            # find one wrapping: term present unlinked in backup body, linked now
            for term in new:
                if f"[[{term}]]" in lb and term in ob and f"[[{term}]]" not in ob:
                    i = lb.find(f"[[{term}]]")
                    print("    WRAP", term, repr(lb[max(0, i - 20) : i + len(term) + 24].replace("\n", " ")))
                    break

        # corpus: entities+topics body link counts
        print("\nCORPUS entities+topics [[ count]]")
        for kind in ("entities", "topics"):
            live_dir = LIVE / "wiki" / kind
            if not live_dir.exists():
                live_dir = LIVE / kind
            live_n = 0
            bak_n = 0
            files = 0
            more = 0
            for p in live_dir.glob("*.md"):
                files += 1
                ln = len(slugs(body_of(p.read_text(encoding="utf-8"))))
                live_n += ln
                rel = f"wiki/wiki/{kind}/{p.name}"
                old = backup_text(rel) or backup_text(f"wiki/{kind}/{p.name}")
                bn = len(slugs(body_of(old or "")))
                bak_n += bn
                if ln > bn:
                    more += 1
            print(f"  {kind} files={files} backup_links={bak_n} live_links={live_n} pages_with_more={more}")

    # inbound vs outbound for hubs from live files
    print("\nWho links TO 工业工程 in live entities?")
    hits = []
    ent = LIVE / "wiki" / "entities"
    if not ent.exists():
        ent = LIVE / "entities"
    for p in ent.glob("*.md"):
        if p.stem == "工业工程":
            continue
        text = p.read_text(encoding="utf-8")
        n = slugs(body_of(text)).count("工业工程")
        if n:
            hits.append((n, p.stem))
    hits.sort(reverse=True)
    print("  inbound_pages", len(hits), "inbound_mentions", sum(n for n, _ in hits), "top", hits[:8])


if __name__ == "__main__":
    main()
