"""
CodeBuddy Authentication Router
基于真实CodeBuddy API的认证实现
"""
import hashlib
import secrets
import httpx
import base64
import json
import uuid
import time
from urllib.parse import urlparse
from typing import Dict, Any, Optional
from fastapi.responses import JSONResponse
from fastapi import APIRouter, HTTPException, Depends, Body
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from config import get_server_password, get_codebuddy_api_endpoint
import logging
import os

logger = logging.getLogger(__name__)

# --- Constants ---
# 认证域名跟随 CODEBUDDY_API_ENDPOINT / CODEBUDDY_INTERNET_ENVIRONMENT：
# 国内(internal/ioa) 走 copilot.tencent.com，否则走 www.codebuddy.ai。
# 令牌与区域绑定，用错域名登录会拿到无法使用的凭证。
_CODEBUDDY_FALLBACK_BASE_URL = 'https://www.codebuddy.ai'
CODEBUDDY_BASE_URL = get_codebuddy_api_endpoint() or _CODEBUDDY_FALLBACK_BASE_URL
CODEBUDDY_AUTH_TOKEN_ENDPOINT = f'{CODEBUDDY_BASE_URL}/v2/plugin/auth/token'
CODEBUDDY_AUTH_STATE_ENDPOINT = f'{CODEBUDDY_BASE_URL}/v2/plugin/auth/state'
# 静默续期：用 refresh_token 换新的 access_token，全程无需用户登录。
# 这是让凭证长期可用的关键——access_token 约 60 天过期，但只要在
# refresh_token 有效期内（约 90 天）就能一直续下去。
CODEBUDDY_AUTH_REFRESH_ENDPOINT = f'{CODEBUDDY_BASE_URL}/v2/plugin/auth/token/refresh'
CODEBUDDY_AUTH_HOST = urlparse(CODEBUDDY_BASE_URL).netloc or 'www.codebuddy.ai'
_last_auth_state: Optional[str] = None

# --- Router Setup ---
router = APIRouter()
security = HTTPBearer()

# --- JWT Authentication ---
import jwt

def get_jwt_secret():
    """基于服务密码生成JWT密钥"""
    password = get_server_password()
    if not password:
        return "fallback-secret-for-development-only"
    return hashlib.sha256(password.encode()).hexdigest()

JWT_SECRET = get_jwt_secret()
ALGORITHM = "HS256"

def authenticate(credentials = Depends(security)) -> str:
    """基于服务密码的认证"""
    password = get_server_password()
    if not password:
        raise HTTPException(status_code=500, detail="CODEBUDDY_PASSWORD is not configured on the server.")
    
    token = credentials.credentials
    if token != password:
        raise HTTPException(status_code=403, detail="Invalid password")
    return token

# --- Helper Functions ---
def generate_auth_state() -> str:
    """生成CodeBuddy认证的state参数"""
    timestamp = int(time.time())
    random_part = secrets.token_hex(16)
    return f"{random_part}_{timestamp}"

def get_auth_start_headers() -> Dict[str, str]:
    """生成启动认证(/state)所需的请求头"""
    request_id = str(uuid.uuid4()).replace('-', '')
    return {
        'Host': CODEBUDDY_AUTH_HOST,
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Connection': 'close',
        'X-Requested-With': 'XMLHttpRequest',
        'X-Domain': CODEBUDDY_AUTH_HOST,
        'X-No-Authorization': 'true',
        'X-No-User-Id': 'true',
        'X-No-Enterprise-Id': 'true',
        'X-No-Department-Info': 'true',
        'User-Agent': 'CLI/1.0.8 CodeBuddy/1.0.8',
        'X-Product': 'SaaS',
        'X-Request-ID': request_id,
    }

def get_auth_poll_headers() -> Dict[str, str]:
    """生成轮询认证(/token)所需的请求头"""
    request_id = str(uuid.uuid4()).replace('-', '')
    span_id = secrets.token_hex(8)
    return {
        'Host': CODEBUDDY_AUTH_HOST,
        'Accept': 'application/json, text/plain, */*',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Connection': 'close',
        'X-Requested-With': 'XMLHttpRequest',
        'X-Request-ID': request_id,
        'b3': f'{request_id}-{span_id}-1-',
        'X-B3-TraceId': request_id,
        'X-B3-ParentSpanId': '',
        'X-B3-SpanId': span_id,
        'X-B3-Sampled': '1',
        'X-No-Authorization': 'true',
        'X-No-User-Id': 'true',
        'X-No-Enterprise-Id': 'true',
        'X-No-Department-Info': 'true',
        'X-Domain': CODEBUDDY_AUTH_HOST,
        'User-Agent': 'CLI/1.0.8 CodeBuddy/1.0.8',
        'X-Product': 'SaaS',
    }

async def start_codebuddy_auth() -> Dict[str, Any]:
    """启动CodeBuddy认证流程"""
    try:
        logger.info("启动CodeBuddy认证流程...")
        
        headers = get_auth_start_headers()
        
        # 调用 /v2/plugin/auth/state 获取认证状态和URL
        async with httpx.AsyncClient(verify=False, trust_env=False) as client:
            # 为避免上游/中间层缓存，添加随机nonce参数，确保每次请求唯一
            nonce = secrets.token_hex(8)
            state_url = f"{CODEBUDDY_AUTH_STATE_ENDPOINT}?platform=CLI&nonce={nonce}"
            payload = {"nonce": nonce}
            
            response = await client.post(state_url, json=payload, headers=headers, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('code') == 0 and result.get('data'):
                    data = result['data']
                    auth_state = data.get('state')
                    auth_url = data.get('authUrl')
                    
                    if auth_state and auth_url:
                        global _last_auth_state
                        if _last_auth_state and auth_state == _last_auth_state:
                            logger.warning("上游返回的state与上一次相同，尝试重新获取新的state...")
                            try:
                                nonce2 = secrets.token_hex(8)
                                state_url2 = f"{CODEBUDDY_AUTH_STATE_ENDPOINT}?platform=CLI&nonce={nonce2}"
                                payload2 = {"nonce": nonce2}
                                async with httpx.AsyncClient(verify=False, trust_env=False) as client2:
                                    response2 = await client2.post(state_url2, json=payload2, headers=headers, timeout=30)
                                if response2.status_code == 200:
                                    result2 = response2.json()
                                    if result2.get('code') == 0 and result2.get('data'):
                                        data2 = result2['data']
                                        ns = data2.get('state')
                                        nu = data2.get('authUrl')
                                        if ns and nu and ns != auth_state:
                                            auth_state = ns
                                            auth_url = nu
                            except Exception:
                                pass
                        token_endpoint = f"{CODEBUDDY_AUTH_TOKEN_ENDPOINT}?state={auth_state}"
                        _last_auth_state = auth_state
                        
                        return {
                            "success": True,
                            "method": "codebuddy_real_auth",
                            "auth_state": auth_state,
                            "verification_uri_complete": auth_url,
                            "verification_uri": CODEBUDDY_BASE_URL,
                            "token_endpoint": token_endpoint,
                            "expires_in": 1800,
                            "interval": 5,
                            "status": "awaiting_login",
                            "instructions": "请点击链接完成CodeBuddy登录",
                            "message": "请使用提供的链接登录CodeBuddy",
                            "platform": "CLI"
                        }
                        
        return {
            "success": False,
            "error": "auth_start_failed",
            "message": "无法启动认证流程"
        }
        
    except Exception as e:
        logger.error(f"启动CodeBuddy认证失败: {e}")
        return {
            "success": False,
            "error": "auth_start_failed", 
            "message": f"认证启动失败: {str(e)}"
        }

async def poll_codebuddy_auth_status(auth_state: str) -> Dict[str, Any]:
    """轮询CodeBuddy认证状态"""
    try:
        headers = get_auth_poll_headers()
        url = f"{CODEBUDDY_AUTH_TOKEN_ENDPOINT}?state={auth_state}"
        
        async with httpx.AsyncClient(verify=False, trust_env=False) as client:
            response = await client.get(url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                
                if result.get('code') == 11217:
                    # 仍在等待登录
                    return {
                        "status": "pending",
                        "message": result.get('msg', 'login ing...'),
                        "code": result.get('code')
                    }
                elif result.get('code') == 0 and result.get('data') and result.get('data', {}).get('accessToken'):
                    # 认证成功，获得token
                    data = result.get('data', {})
                    return {
                        "status": "success",
                        "message": "认证成功！",
                        "token_data": {
                            "access_token": data.get('accessToken'),
                            "bearer_token": data.get('accessToken'),
                            "token_type": data.get('tokenType', 'Bearer'),
                            "expires_in": data.get('expiresIn'),
                            "refresh_token": data.get('refreshToken'),
                            "session_state": data.get('sessionState'),
                            "scope": data.get('scope'),
                            "domain": data.get('domain'),
                            "full_response": result
                        }
                    }
                else:
                    # 其他状态码
                    return {
                        "status": "unknown",
                        "message": result.get('msg', 'Unknown status'),
                        "code": result.get('code'),
                        "response": result
                    }
            else:
                return {
                    "status": "error",
                    "message": f"API请求失败，状态码: {response.status_code}",
                    "response_text": response.text
                }
                
    except Exception as e:
        logger.error(f"轮询认证状态失败: {e}")
        return {
            "status": "error",
            "message": f"轮询失败: {str(e)}"
        }

async def refresh_codebuddy_token(refresh_token: str) -> Optional[Dict[str, Any]]:
    """用 refresh_token 静默换取新的 access_token（无需用户登录）。

    成功返回新的 token 数据字典，失败返回 None。
    """
    if not refresh_token:
        return None
    headers = {
        'Host': CODEBUDDY_AUTH_HOST,
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'X-Refresh-Token': refresh_token,
        'X-Domain': CODEBUDDY_AUTH_HOST,
        'User-Agent': 'CLI/1.0.8 CodeBuddy/1.0.8',
        'X-Product': 'SaaS',
        'X-Request-ID': str(uuid.uuid4()).replace('-', ''),
    }
    try:
        async with httpx.AsyncClient(verify=False, trust_env=False) as client:
            resp = await client.post(
                CODEBUDDY_AUTH_REFRESH_ENDPOINT,
                json={'refresh_token': refresh_token},
                headers=headers,
                timeout=30,
            )
        if resp.status_code != 200:
            logger.warning(f"刷新 token 失败: HTTP {resp.status_code}")
            return None
        payload = resp.json()
        if payload.get('code') != 0 or not payload.get('data'):
            logger.warning(f"刷新 token 返回异常: {payload.get('code')} {payload.get('msg')}")
            return None
        data = payload['data']
        access = data.get('accessToken') or data.get('access_token')
        if not access:
            return None
        return {
            'access_token': access,
            'bearer_token': access,
            'refresh_token': data.get('refreshToken') or refresh_token,
            'expires_in': data.get('expiresIn'),
            'token_type': data.get('tokenType', 'Bearer'),
            'scope': data.get('scope'),
            'domain': data.get('domain') or CODEBUDDY_AUTH_HOST,
        }
    except Exception as exc:
        logger.warning(f"刷新 token 异常: {exc}")
        return None


async def refresh_saved_token_file(path: str, force: bool = False) -> bool:
    """给已保存的凭证文件做静默续期；成功则就地更新。

    仅在剩余有效期低于阈值（或 force）时才真正请求，避免无谓调用。
    失败原因会通过 _LAST_REFRESH_REASON 暴露给调用方（如缺少 refresh_token）。
    """
    try:
        import json as _json
        with open(path, 'r', encoding='utf-8') as fh:
            cred = _json.load(fh)
    except Exception:
        return False

    refresh_token = cred.get('refresh_token')
    if not refresh_token:
        return False

    # 判断是否需要续期：解析 access_token 的 exp
    if not force:
        token = cred.get('bearer_token') or ''
        exp = 0
        try:
            parts = token.split('.')
            if len(parts) >= 2:
                payload = parts[1] + '=' * (-len(parts[1]) % 4)
                exp = _json.loads(base64.urlsafe_b64decode(payload)).get('exp', 0)
        except Exception:
            exp = 0
        # 剩余超过 7 天就先不动
        if exp and (exp - time.time()) > 7 * 86400:
            return False

    refreshed = await refresh_codebuddy_token(refresh_token)
    if not refreshed:
        return False

    cred.update({
        'bearer_token': refreshed['access_token'],
        'refresh_token': refreshed['refresh_token'],
        'expires_in': refreshed.get('expires_in'),
        'token_type': refreshed.get('token_type', 'Bearer'),
        'refreshed_at': int(time.time()),
    })
    try:
        with open(path, 'w', encoding='utf-8') as fh:
            _json.dump(cred, fh, ensure_ascii=False, indent=2)
        logger.info(f"已静默续期凭证: {os.path.basename(path)}")
        return True
    except Exception as exc:
        logger.warning(f"写入续期凭证失败: {exc}")
        return False


async def save_codebuddy_token(token_data: Dict[str, Any]) -> bool:
    """保存CodeBuddy token到文件"""
    try:
        from .codebuddy_token_manager import codebuddy_token_manager
        
        # 添加创建时间
        token_data["created_at"] = int(time.time())
        
        # 从JWT中解析用户信息
        bearer_token = token_data.get("access_token") or token_data.get("bearer_token")
        user_id = "unknown"
        user_info = {}
        
        try:
            if bearer_token and '.' in bearer_token:
                # 分割JWT token
                parts = bearer_token.split('.')
                if len(parts) >= 2:
                    payload_part = parts[1]
                    
                    # 修复Base64 padding问题
                    missing_padding = len(payload_part) % 4
                    if missing_padding:
                        payload_part += '=' * (4 - missing_padding)
                    
                    # 解码JWT payload
                    try:
                        payload = base64.urlsafe_b64decode(payload_part)
                        jwt_data = json.loads(payload.decode('utf-8'))
                        
                        # 提取用户信息，优先使用邮箱作为用户标识
                        user_id = (jwt_data.get('email') or 
                                 jwt_data.get('preferred_username') or 
                                 jwt_data.get('sub') or 
                                 "unknown")
                        
                        # 保存完整的用户信息
                        user_info = {
                            'sub': jwt_data.get('sub'),
                            'email': jwt_data.get('email'),
                            'preferred_username': jwt_data.get('preferred_username'),
                            'name': jwt_data.get('name'),
                            'given_name': jwt_data.get('given_name'),
                            'family_name': jwt_data.get('family_name'),
                            'exp': jwt_data.get('exp'),
                            'iat': jwt_data.get('iat'),
                            'scope': jwt_data.get('scope'),
                            'session_state': jwt_data.get('sid')
                        }
                        
                        # 移除None值
                        user_info = {k: v for k, v in user_info.items() if v is not None}
                        
                        logger.info(f"成功解析JWT，用户: {user_id}")
                        logger.debug(f"JWT用户信息: {user_info}")
                        
                    except (json.JSONDecodeError, UnicodeDecodeError) as decode_error:
                        logger.warning(f"JWT payload解码失败: {decode_error}")
                        user_id = token_data.get('domain', 'unknown')
                else:
                    logger.warning("JWT格式无效：缺少必要的部分")
                    user_id = token_data.get('domain', 'unknown')
            else:
                logger.warning("Bearer token为空或格式无效")
                user_id = token_data.get('domain', 'unknown')
                
        except Exception as e:
            logger.error(f"JWT解析过程发生异常: {e}")
            user_id = token_data.get('domain', 'unknown')
        
        # 构建完整的凭证数据
        credential_data = {
            "bearer_token": bearer_token,
            "user_id": user_id,
            "created_at": int(time.time()),
            "expires_in": token_data.get('expires_in'),
            "refresh_token": token_data.get('refresh_token'),
            "token_type": token_data.get('token_type', 'Bearer'),
            "scope": token_data.get('scope'),
            "domain": token_data.get('domain'),
            "session_state": token_data.get('session_state'),
            "user_info": user_info,
            "full_response": token_data  # 保存完整的原始响应
        }
        
        # 移除None值，保持文件整洁
        credential_data = {k: v for k, v in credential_data.items() if v is not None}
        
        # 生成更友好的文件名
        timestamp = int(time.time())
        safe_user_id = "".join(c for c in user_id if c.isalnum() or c in "._-")[:20]
        filename = f"codebuddy_{safe_user_id}_{timestamp}.json"
        
        # 使用token管理器保存
        success = codebuddy_token_manager.add_credential_with_data(
            credential_data=credential_data,
            filename=filename
        )
        
        if success:
            logger.info(f"成功保存CodeBuddy token，用户: {user_id}，文件: {filename}")
        
        return success
        
    except Exception as e:
        logger.error(f"保存CodeBuddy token失败: {e}")
        return False

# --- API Endpoints ---
@router.get("/auth/start", summary="Start CodeBuddy Authentication")
async def start_device_auth():
    """启动CodeBuddy认证流程"""
    try:
        logger.info("开始启动CodeBuddy认证流程...")
        
        # 尝试真实的CodeBuddy认证API
        real_auth_result = await start_codebuddy_auth()
        
        if real_auth_result.get('success'):
            logger.info("真实CodeBuddy认证API启动成功!")
            return real_auth_result
        else:
            logger.warning(f"真实认证API失败: {real_auth_result}")
            return real_auth_result
        
    except Exception as e:
        logger.error(f"认证启动过程发生异常: {e}")
        return {
            "success": False,
            "error": "Unexpected error",
            "message": f"认证启动失败: {str(e)}"
        }

@router.post("/auth/poll", summary="Poll for OAuth token")
async def poll_for_token(
    device_code: str = Body(None, embed=True),
    code_verifier: str = Body(None, embed=True),
    auth_state: str = Body(None, embed=True)
):
    """轮询CodeBuddy token端点"""
    from .codebuddy_token_manager import codebuddy_token_manager
    
    # 如果有auth_state，说明是真实的CodeBuddy认证流程
    if auth_state:
        logger.info(f"轮询真实CodeBuddy认证状态: {auth_state}")
        poll_result = await poll_codebuddy_auth_status(auth_state)
        
        if poll_result.get('status') == 'success':
            # 认证成功，保存token
            token_data = poll_result.get('token_data', {})
            if token_data:
                # 提取token信息
                bearer_token = token_data.get('access_token') or token_data.get('bearer_token')
                if bearer_token:
                    # 保存token
                    token_saved = await save_codebuddy_token(token_data)
                    return JSONResponse(content={
                        "access_token": bearer_token,
                        "token_type": token_data.get('token_type', 'Bearer'),
                        "expires_in": token_data.get('expires_in'),
                        "refresh_token": token_data.get('refresh_token'),
                        "scope": token_data.get('scope'),
                        "saved": token_saved,
                        "message": "认证成功！🎉",
                        "user_info": token_data,
                        "domain": token_data.get('domain')
                    }, status_code=200)
                else:
                    return JSONResponse(content={
                        "error": "invalid_token_response",
                        "error_description": "API返回的响应中没有找到token"
                    }, status_code=400)
        elif poll_result.get('status') == 'pending':
            # 仍在等待
            return JSONResponse(content={
                "error": "authorization_pending",
                "error_description": poll_result.get('message', '等待用户登录...'),
                "code": poll_result.get('code')
            }, status_code=400)
        else:
            # 错误状态
            return JSONResponse(content={
                "error": "auth_error",
                "error_description": poll_result.get('message', '认证过程发生错误'),
                "details": poll_result
            }, status_code=400)
    else:
        return JSONResponse(content={
            "error": "missing_parameters",
            "error_description": "缺少必要的参数：auth_state"
        }, status_code=400)

@router.post("/auth/refresh", summary="Silently refresh a saved credential")
async def refresh_credential(
    filename: str = Body(None, embed=True),
    force: bool = Body(False, embed=True),
    _auth: str = Depends(authenticate),
):
    """用 refresh_token 静默续期凭证，无需用户重新登录。"""

    from .codebuddy_token_manager import codebuddy_token_manager

    cred_dir = codebuddy_token_manager.creds_dir
    if not filename:
        return JSONResponse(
            content={"success": False, "message": "缺少 filename"},
            status_code=400,
        )

    # 防目录穿越：只取 basename 并限制在凭证目录内
    safe = os.path.basename(filename)
    path = os.path.join(cred_dir, safe)
    if not safe.endswith('.json') or not os.path.isfile(path):
        return JSONResponse(
            content={"success": False, "message": f"凭证不存在: {safe}"},
            status_code=404,
        )

    ok = await refresh_saved_token_file(path, force=bool(force))
    return JSONResponse(content={
        "success": ok,
        "filename": safe,
        "message": "已续期" if ok else "无需续期或续期失败（refresh_token 可能已过期）",
    }, status_code=200 if ok else 400)


@router.post("/auth/refresh-all", summary="Silently refresh every saved credential")
async def refresh_all_credentials(
    force: bool = Body(False, embed=True),
    _auth: str = Depends(authenticate),
):
    """批量静默续期所有凭证。"""

    from .codebuddy_token_manager import codebuddy_token_manager

    cred_dir = codebuddy_token_manager.creds_dir
    results = []
    try:
        for name in sorted(os.listdir(cred_dir)):
            if not name.endswith('.json') or name == 'manager_state.json':
                continue
            path = os.path.join(cred_dir, name)
            try:
                # 先看这个凭证有没有 refresh_token —— 没有就永远无法静默续期，
                # 需要前端明确提示用户重新登录，而不是当成普通失败。
                has_rt = False
                try:
                    with open(path, 'r', encoding='utf-8') as fh:
                        has_rt = bool(json.load(fh).get('refresh_token'))
                except Exception:
                    has_rt = False

                ok = await refresh_saved_token_file(path, force=bool(force))
                entry = {"filename": name, "refreshed": ok}
                if not ok and not has_rt:
                    entry["no_refresh_token"] = True
                results.append(entry)
            except Exception as exc:
                results.append({"filename": name, "refreshed": False, "error": str(exc)})
    except Exception as exc:
        return JSONResponse(content={"success": False, "message": str(exc)}, status_code=500)

    n = sum(1 for r in results if r["refreshed"])
    return JSONResponse(content={"success": True, "refreshed": n, "total": len(results), "results": results})


@router.get("/auth/callback", summary="OAuth2 callback endpoint")
async def oauth_callback(code: str = None, state: str = None, error: str = None):
    """OAuth2回调端点"""
    if error:
        return JSONResponse(
            content={"error": error, "error_description": "授权被拒绝或出现错误"},
            status_code=400
        )
    
    return JSONResponse(
        content={
            "message": "授权成功！请返回应用程序。",
            "code": code,
            "state": state
        }
    )
