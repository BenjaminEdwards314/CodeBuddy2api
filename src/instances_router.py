"""
Instances Router - manage multiple proxy instances from the admin workbench.
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth import authenticate
from .instance_manager import get_manager, list_pool_credentials, seed_pool_from, project_root

logger = logging.getLogger(__name__)
router = APIRouter()


class InstancePayload(BaseModel):
    name: Optional[str] = ""
    port: Optional[int] = None
    password: Optional[str] = ""
    auth_mode: Optional[str] = "api_key"
    api_key: Optional[str] = ""
    token_names: Optional[List[str]] = []
    auto_start: Optional[bool] = False


class InstanceUpdate(BaseModel):
    name: Optional[str] = None
    port: Optional[int] = None
    password: Optional[str] = None
    auth_mode: Optional[str] = None
    api_key: Optional[str] = None
    token_names: Optional[List[str]] = None


@router.get("/instances", summary="List all proxy instances")
async def list_instances(_token: str = Depends(authenticate)) -> Dict[str, Any]:
    try:
        manager = get_manager()
        _seed_once()
        return {"instances": manager.list_all()}
    except Exception as exc:
        logger.error(f"Could not list instances: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="无法读取实例列表。")


@router.post("/instances", summary="Create a new proxy instance")
async def create_instance(payload: InstancePayload,
                          _token: str = Depends(authenticate)) -> Dict[str, Any]:
    try:
        manager = get_manager()
        _seed_once()
        created = manager.create(
            name=payload.name or "",
            port=payload.port,
            password=payload.password or "",
            auth_mode=payload.auth_mode or "api_key",
            api_key=payload.api_key or "",
            token_names=payload.token_names or [],
            auto_start=bool(payload.auto_start),
        )
        return {"message": f"实例 “{created['name']}” 已创建。", "instance": created}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"Could not create instance: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="创建实例失败。")


@router.put("/instances/{instance_id}", summary="Update an instance")
async def update_instance(instance_id: str, payload: InstanceUpdate,
                          _token: str = Depends(authenticate)) -> Dict[str, Any]:
    try:
        fields = {k: v for k, v in payload.dict().items() if v is not None}
        updated = get_manager().update(instance_id, **fields)
        return {"message": "实例配置已保存。", "instance": updated}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"Could not update instance: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="更新实例失败。")


@router.delete("/instances/{instance_id}", summary="Delete an instance")
async def delete_instance(instance_id: str,
                          _token: str = Depends(authenticate)) -> Dict[str, str]:
    try:
        get_manager().delete(instance_id)
        return {"message": "实例已删除。"}
    except Exception as exc:
        logger.error(f"Could not delete instance: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="删除实例失败。")


@router.post("/instances/{instance_id}/start", summary="Start an instance")
async def start_instance(instance_id: str,
                         _token: str = Depends(authenticate)) -> Dict[str, Any]:
    try:
        result = get_manager().start(instance_id)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("message", "启动失败。"))
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Could not start instance: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="启动实例失败。")


@router.post("/instances/{instance_id}/stop", summary="Stop an instance")
async def stop_instance(instance_id: str,
                        _token: str = Depends(authenticate)) -> Dict[str, Any]:
    try:
        return get_manager().stop(instance_id)
    except Exception as exc:
        logger.error(f"Could not stop instance: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="停止实例失败。")


@router.get("/credential-pool", summary="Credentials available to instances")
async def credential_pool(_token: str = Depends(authenticate)) -> Dict[str, Any]:
    try:
        _seed_once()
        return {"credentials": list_pool_credentials()}
    except Exception as exc:
        logger.error(f"Could not read credential pool: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="无法读取凭证池。")


_seeded = False


def _seed_once() -> None:
    """Populate the shared credential pool from existing local credentials.

    Checks both the repo checkout (source runs) and the process working
    directory (the packaged app runs with its data dir as cwd), so the pool is
    populated either way.
    """
    global _seeded
    if _seeded:
        return
    _seeded = True
    try:
        import os

        candidates = [
            os.path.join(os.getcwd(), ".codebuddy_creds"),
            os.path.join(project_root(), ".codebuddy_creds"),
        ]
        for local in candidates:
            count = seed_pool_from(local)
            if count:
                logger.info(f"Seeded credential pool with {count} credential(s) from {local}")
                break
    except Exception:
        logger.warning("Could not seed credential pool", exc_info=True)
