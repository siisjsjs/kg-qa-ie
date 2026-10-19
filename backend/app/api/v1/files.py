"""files:生成图片等缓存文件的只读端点(改造计划 §5.3)。"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse, Response

from app.core.deps import CurrentUserId
from app.services.tools.chart_generate import exchange_user_token, zhihuitu_base_url
from app.services.tools.imagegen import IMAGE_ID_RE, genimg_dir

router = APIRouter()


@router.get("/files/genimg/{filename}")
async def get_generated_image(filename: str) -> FileResponse:
    """读取问答生成的示意图。文件名格式白名单校验,防路径穿越。"""
    if not filename.endswith(".png"):
        raise HTTPException(status_code=404, detail="not found")
    image_id = filename[:-4]
    if not IMAGE_ID_RE.match(image_id):
        raise HTTPException(status_code=404, detail="not found")
    path = genimg_dir() / f"{image_id}.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/files/chart-excel/{chart_id}")
async def export_chart_excel(
    chart_id: int,
    user_id: CurrentUserId,
    authorization: str | None = Header(default=None),
) -> Response:
    """代理智绘图的服务端 Excel 导出,前端拿到即为直接下载。

    以提问用户本人身份换取智绘图 token 再取文件——与出图/存图同一套 SSO,
    产物和智绘图编辑器里点「导出 Excel」完全一致。
    """
    kgqa_token = ""
    if authorization and authorization.lower().startswith("bearer "):
        kgqa_token = authorization.split(" ", 1)[1].strip()

    base_url = zhihuitu_base_url()
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            token = await exchange_user_token(client, base_url, kgqa_token)
            url = f"{base_url}/api/v1/charts/{chart_id}/export-excel"
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 401:
                token = await exchange_user_token(client, base_url, kgqa_token, force=True)
                resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"绘图平台不可达: {exc!r}") from exc

    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="图表不存在或无权访问")
    if resp.status_code >= 400:
        detail = ""
        try:
            detail = str(resp.json().get("detail") or "")
        except Exception:  # noqa: BLE001
            detail = resp.text[:200]
        raise HTTPException(status_code=resp.status_code, detail=detail or "Excel 导出失败")

    headers = {"Cache-Control": "no-store"}
    disposition = resp.headers.get("content-disposition")
    if disposition:
        headers["Content-Disposition"] = disposition
    return Response(resp.content, media_type=_XLSX_MIME, headers=headers)
