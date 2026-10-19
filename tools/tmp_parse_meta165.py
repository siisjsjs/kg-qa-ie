import json
from pathlib import Path

text = Path("/tmp/meta165.txt").read_text(encoding="utf-8", errors="replace")
for line in text.splitlines():
    if not line.strip():
        continue
    mid, js = line.split("\t", 1)
    meta = json.loads(js)
    images = meta.get("images") or []
    events = meta.get("tool_events") or []
    templates = meta.get("chart_templates")
    print("=" * 60)
    print("msg", mid)
    print(
        "images",
        [
            (
                i.get("image_id"),
                (i.get("chart") or {}).get("template_id"),
                i.get("status"),
            )
            for i in images
        ],
    )
    print("n_events", len(events))
    for ev in events:
        name = ev.get("event") or ev.get("name") or ev.get("tool")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else ev
        print(" ev", name, list(ev.keys())[:12])
        if isinstance(data, dict):
            tid = data.get("template_id") or (data.get("chart") or {}).get("template_id")
            err = data.get("error") or data.get("detail")
            print(
                "    tid",
                tid,
                "err",
                (str(err)[:200] if err else None),
                "image",
                data.get("image_id"),
            )
    if templates:
        tpls = templates.get("templates") if isinstance(templates, dict) else templates
        if isinstance(tpls, list):
            print("chart_templates", [t.get("id") or t.get("name") for t in tpls[:6]])
        else:
            print("chart_templates_type", type(templates).__name__)
    print("persist", meta.get("persist"))
