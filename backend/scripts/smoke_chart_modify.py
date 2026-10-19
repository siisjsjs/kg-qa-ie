"""端到端冒烟:甘特图生成 → 同会话追问改图 → Excel 导出 → 智绘图记录校验。

用法: python scripts/smoke_chart_modify.py
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
Q1 = (
    "帮我画一个新产品导入项目的甘特图,分三个阶段:方案设计1月到2月,"
    "样机打样3月到4月,量产爬坡5月到6月"
)
Q2 = "把样机打样阶段延长到5月,再加一个可靠性测试阶段6月到7月"


async def sse_query(client: httpx.AsyncClient, token: str, question: str, session_id: int):
    events: list[tuple[str, dict]] = []
    async with client.stream(
        "POST",
        f"{BASE}/query",
        json={"question": question, "session_id": session_id},
        headers={"Authorization": f"Bearer {token}"},
        timeout=300,
    ) as resp:
        resp.raise_for_status()
        event_name = ""
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                try:
                    data = json.loads(line.split(":", 1)[1].strip())
                except Exception:
                    data = {}
                events.append((event_name, data))
    return events


def show(events, label):
    print(f"\n===== {label} =====")
    tools = [d.get("tool") for e, d in events if e == "tool_start"]
    print("tools called:", tools)
    for e, d in events:
        if e == "image_ready":
            chart = d.get("chart") or {}
            print("image_ready:", d.get("image_id"), "| editor_path:", chart.get("editor_path"),
                  "| chart_id:", chart.get("chart_id"), "| engine:", chart.get("engine"),
                  "| export_formats:", chart.get("export_formats"))
            print("  params:", json.dumps(chart.get("params"), ensure_ascii=False)[:300])
        if e == "image_failed":
            print("image_failed:", d)
    answer = "".join(d.get("text", "") for e, d in events if e == "token")
    print("answer head:", answer[:200].replace("\n", " "))
    return [d for e, d in events if e == "image_ready"]


async def main():
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{BASE}/auth/login", json={"account": "admin", "password": "CHANGE_ME"})
        token = r.json().get("access_token") or (r.json().get("data") or {}).get("token")
        if not token:
            print("login failed:", r.status_code, r.text[:200]); sys.exit(1)
        r = await client.post(f"{BASE}/conversations", json={"title": "冒烟-改图"},
                              headers={"Authorization": f"Bearer {token}"})
        session_id = r.json()["id"]
        print("session:", session_id)

        ev1 = await sse_query(client, token, Q1, session_id)
        imgs1 = show(ev1, "第一问:生成甘特图")
        if not imgs1:
            print("!! 第一问没有出图"); sys.exit(2)

        ev2 = await sse_query(client, token, Q2, session_id)
        imgs2 = show(ev2, "第二问:追问改图")
        if not imgs2:
            print("!! 追问没有重画图 —— 修改流程失败"); sys.exit(3)
        cid1 = (imgs1[-1].get("chart") or {}).get("chart_id")
        cid2 = (imgs2[-1].get("chart") or {}).get("chart_id")
        ok_new = bool(cid1) and bool(cid2) and cid1 != cid2
        print(f"\n版本保留校验: 第一问 chart_id={cid1} 第二问 chart_id={cid2} "
              f"=> {'各自独立记录 OK(旧版本可回看)' if ok_new else '!! 应每次新建记录'}")

        # 智绘图记录校验:新旧两条记录都应存在;chartData 应是原始参数(无 xml)
        zbase = (imgs2[-1].get("chart") or {}).get("base_url") or "https://draw.example.com"
        r = await client.post(f"{zbase}/api/v1/auth/sso-token", json={"token": token})
        ztoken = (r.json().get("data") or {}).get("token")
        for label, chart_id in (("旧图", cid1), ("新图", cid2)):
            if not chart_id:
                continue
            r = await client.get(f"{zbase}/api/v1/charts/{chart_id}",
                                 headers={"Authorization": f"Bearer {ztoken}"})
            data = (r.json().get("data") or {})
            cd = data.get("chartData") or {}
            print(f"zhihuitu {label}记录:", chart_id, "| 存在:", r.status_code == 200,
                  "| keys:", list(cd.keys())[:8], "| has xml:", bool(cd.get("xml")))

        # Excel 直接下载校验:用支持 xlsx 的人机作业分析图走 kgqa 代理端点
        ev3 = await sse_query(
            client, token,
            "帮我画一个钻床钻孔作业的人机作业分析图:取工件装夹15秒(攻丝机同时自动攻丝),"
            "开动钻床2秒,等待钻孔30秒,停机卸工件10秒,检验放置8秒",
            session_id,
        )
        imgs3 = show(ev3, "第三问:人机作业分析图(测 Excel 导出)")
        cid3 = (imgs3[-1].get("chart") or {}).get("chart_id") if imgs3 else None
        if cid3:
            r = await client.get(f"{BASE}/files/chart-excel/{cid3}",
                                 headers={"Authorization": f"Bearer {token}"})
            ok_xlsx = r.status_code == 200 and r.content[:2] == b"PK"
            print(f"\nExcel 直接下载校验: chart_id={cid3} HTTP {r.status_code} "
                  f"{len(r.content)} bytes | content-disposition: "
                  f"{r.headers.get('content-disposition')} => {'OK' if ok_xlsx else '!! 失败'}")
        else:
            print("\n!! 第三问没有出图,无法校验 Excel 导出")


if __name__ == "__main__":
    asyncio.run(main())
