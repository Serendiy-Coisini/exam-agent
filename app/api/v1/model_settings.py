import ipaddress
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.api.dependencies import get_current_user
from app.core.config import settings
from app.schemas.model_settings_schema import ModelSettingsPayload
from app.services import model_settings_service as service


class PrivateValidationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                errors = [{k: e[k] for k in ("loc", "msg", "type") if k in e}
                          for e in exc.errors()]
                return JSONResponse(status_code=422, content={"detail": errors},
                                    headers={"Cache-Control": "no-store"})
        return safe_handler


def require_model_settings_access(request: Request, user: dict = Depends(get_current_user)):
    try:
        local = request.client and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    if not local:
        raise HTTPException(status_code=403, detail="模型设置仅允许在运行工作台的本机管理")
    if settings.AUTH_ENABLED and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以管理工作台模型配置")
    hostname = request.url.hostname
    if request.headers.get("host") and hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="请通过本机地址打开模型设置")
    origin = request.headers.get("origin")
    if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
        raise HTTPException(status_code=403, detail="不允许跨站访问模型设置")


router = APIRouter(prefix="/settings/model", tags=["model settings"],
                   route_class=PrivateValidationRoute,
                   dependencies=[Depends(require_model_settings_access)])


@router.get("")
def model_status():
    return JSONResponse(service.get_model_status(), headers={"Cache-Control": "no-store"})


@router.put("")
def save_model(payload: ModelSettingsPayload):
    try:
        return JSONResponse(service.save_model_settings(payload), headers={"Cache-Control": "no-store"})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="模型配置保存失败，请检查本地数据目录是否可写") from exc


@router.post("/test")
def test_model(payload: ModelSettingsPayload):
    try:
        return JSONResponse(service.test_model_connection(payload), headers={"Cache-Control": "no-store"})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
