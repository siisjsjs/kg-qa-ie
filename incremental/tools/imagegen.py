"""generate_image 工具:OpenAI 兼容图片生成,异步不阻塞正文(改造计划 §5.3)。

run() 立即返回 {"image_id", "status": "pending"};实际生成用 asyncio.create_task
后台跑,完成后图片落盘 {cache_dir}/genimg/{image_id}.png,并向 ctx["image_queue"]
投递 image_ready / image_failed 事件。
"""

from __future__ import annotations

import asyncio
import base64
import re
import uuid
from pathlib import Path

import httpx
from loguru import logger

from app.core.config import settings
from app.services.admin import config_store
from app.services.tools.base import Tool, breaker

IMAGE_ID_RE = re.compile(r"^img_[a-f0-9]{12}$")
# gpt-image 系列出图经常超过 2 分钟,read 给足 300s;超时另有一次重试
_GEN_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)


def genimg_dir() -> Path:
    d = Path(settings.cache_dir) / "genimg"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _normalize_base_url(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return url


def _read_config() -> dict:
    return {
        "base_url": _normalize_base_url(config_store.get_sync("imagegen.base_url") or ""),
        "api_key": (config_store.get_sync("imagegen.api_key") or "").strip(),
        "model": (config_store.get_sync("imagegen.model") or "").strip(),
        "size": (config_store.get_sync("imagegen.size") or "").strip() or "1024x1024",
    }


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=_GEN_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


async def generate_image_file(image_id: str, prompt: str) -> Path:
    """真正调用生成接口并落盘;供后台任务使用,异常向上抛。"""
    cfg = _read_config()
    payload = {
        "model": cfg["model"],
        "prompt": prompt,
        "size": cfg["size"],
        "n": 1,
        "response_format": "b64_json",
    }
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    url = f"{cfg['base_url']}/v1/images/generations"
    async with httpx.AsyncClient(timeout=_GEN_TIMEOUT) as client:
        try:
            resp = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException:
            logger.warning("imagegen timeout, retrying once: id={}", image_id)
            resp = await client.post(url, headers=headers, json=payload)
    if resp.status_code >= 400:
        breaker.maybe_trip_for_status("generate_image", resp.status_code)
        raise RuntimeError(f"imagegen HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    items = data.get("data") or []
    if not items:
        raise RuntimeError(f"imagegen empty response: {str(data)[:200]}")
    item = items[0]
    if item.get("b64_json"):
        content = base64.b64decode(item["b64_json"])
    elif item.get("url"):
        # 兼容只返回 url 的服务:服务端拉回来落盘
        content = await _download(str(item["url"]))
    else:
        raise RuntimeError("imagegen response has neither b64_json nor url")
    out = genimg_dir() / f"{image_id}.png"
    out.write_bytes(content)
    return out


async def _background_generate(image_id: str, prompt: str, caption: str, ctx: dict) -> None:
    queue: asyncio.Queue | None = ctx.get("image_queue")
    images: dict = ctx.setdefault("images", {})
    try:
        await generate_image_file(image_id, prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("image generation failed: id={} err={!r}", image_id, exc)
        images[image_id] = {
            "image_id": image_id, "status": "failed", "prompt": prompt, "caption": caption,
        }
        if queue is not None:
            await queue.put(
                {"event": "image_failed", "data": {"image_id": image_id, "error": str(exc)[:200]}}
            )
        return
    images[image_id] = {
        "image_id": image_id, "status": "ready", "prompt": prompt, "caption": caption,
    }
    if queue is not None:
        await queue.put(
            {
                "event": "image_ready",
                "data": {
                    "image_id": image_id,
                    "url": f"/api/v1/files/genimg/{image_id}.png",
                    "caption": caption,
                },
            }
        )


class GenerateImageTool(Tool):
    name = "generate_image"
    timeout = 5.0  # run() 只登记任务,立即返回

    def spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "只画目录里没有的概念示意、场景、结构爆炸、教学氛围/海报感插图。"
                    "禁止画目录已有的专业 IE 图,也禁止用本工具冒充帕累托/动素/流程程序图/VSM/甘特/路线图。"
                    "图片异步生成不阻塞回答:调用后立即拿到真实 image_id(形如 img_ 后跟 12 位十六进制),"
                    "把这个真实 id 写进正文占位,没拿到真实 id 就不要写占位,不要等待图片完成。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "图片内容描述(英文效果更稳,包含图类型/要素/风格)",
                        },
                        "caption": {
                            "type": "string",
                            "description": "图片的中文一句话说明(展示给用户,描述图里画的是什么)",
                        },
                    },
                    "required": ["prompt", "caption"],
                },
            },
        }

    def available(self) -> bool:
        if breaker.is_open(self.name):
            return False
        cfg = _read_config()
        return bool(cfg["base_url"] and cfg["api_key"] and cfg["model"])

    async def run(self, args: dict, ctx: dict) -> dict:
        prompt = str(args.get("prompt") or "").strip()
        caption = str(args.get("caption") or "").strip()
        if not prompt:
            return {"error": "prompt 不能为空"}
        if not self.available():
            return {"error": "图片生成未配置"}
        image_id = f"img_{uuid.uuid4().hex[:12]}"
        ctx.setdefault("images", {})[image_id] = {
            "image_id": image_id,
            "status": "pending",
            "prompt": prompt,
            "caption": caption,
        }
        task = asyncio.create_task(_background_generate(image_id, prompt, caption, ctx))
        ctx.setdefault("image_tasks", {})[image_id] = task
        ctx.setdefault("events", []).append(
            {
                "event": "image_pending",
                "data": {"image_id": image_id, "prompt": prompt, "caption": caption},
            }
        )
        return {
            "image_id": image_id,
            "status": "pending",
            "note": f"图片已开始后台生成,请在正文插图位置写 {{{{image:{image_id}}}}} 占位,不要等待。",
        }
