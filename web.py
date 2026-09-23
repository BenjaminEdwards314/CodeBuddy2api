"""
Main Web Service for CodeBuddy2API
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Import the routers
from src.codebuddy_router import router as codebuddy_router, lifecycle_manager
from src.codebuddy_auth_router import router as codebuddy_auth_router
from src.settings_router import router as settings_router
from src.instances_router import router as instances_router
from src.frontend_router import router as frontend_router
from src.anthropic_router import router as anthropic_router, anthropic_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException

from config import get_server_host, get_server_port, get_log_level

# 配置日志
logging.basicConfig(
    level=getattr(logging, get_log_level().upper()),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def _auto_refresh_credentials():
    """后台静默续期已保存的凭证。

    access_token 约 60 天过期；只要 refresh_token 还在有效期内（约 90 天），
    就能在过期前自动换新，用户无需重新登录。
    """
    import glob
    import os as _os

    from src.codebuddy_auth_router import refresh_saved_token_file

    # 每小时检查一次；只有剩余有效期低于阈值时才真正发请求
    while True:
        try:
            cred_dir = _os.path.expanduser("~/.codebuddy_creds")
            env_dir = _os.environ.get("CODEBUDDY_CREDENTIALS_DIR")
            if env_dir:
                cred_dir = _os.path.expanduser(env_dir)
            if _os.path.isdir(cred_dir):
                for path in glob.glob(_os.path.join(cred_dir, "*.json")):
                    if _os.path.basename(path) == "manager_state.json":
                        continue
                    try:
                        await refresh_saved_token_file(path)
                    except Exception as exc:
                        logger.warning(f"续期 {path} 失败: {exc}")
        except Exception as exc:
            logger.warning(f"自动续期循环异常: {exc}")
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("Starting CodeBuddy2API Service")
    refresh_task = None
    try:
        # 启动时初始化资源
        await lifecycle_manager.startup()
        refresh_task = asyncio.create_task(_auto_refresh_credentials())
        yield
    finally:
        # 关闭时清理资源
        if refresh_task:
            refresh_task.cancel()
            try:
                await refresh_task
            except asyncio.CancelledError:
                pass
        await lifecycle_manager.shutdown()
        logger.info("CodeBuddy2API Service stopped")


# 创建FastAPI应用
app = FastAPI(
    title="CodeBuddy2API",
    description="CodeBuddy API proxy with OpenAI-compatible interface",
    version="1.1.2",
    lifespan=lifespan
)

# CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载前端路由
app.include_router(
    frontend_router,
    tags=["Frontend"]
)

# 挂载CodeBuddy认证路由
app.include_router(
    codebuddy_auth_router,
    prefix="/codebuddy",
    tags=["CodeBuddy OAuth2 Authentication"]
)

# 挂载CodeBuddy API路由
app.include_router(
    codebuddy_router,
    prefix="/codebuddy",
    tags=["CodeBuddy Compatible API"]
)

# 兼容标准 OpenAI 客户端：直接支持 /v1/* 路径
app.include_router(
    codebuddy_router,
    tags=["OpenAI Compatible API"]
)

# Anthropic Messages API 兼容端点（Claude Desktop 使用）
app.include_router(
    anthropic_router,
    tags=["Anthropic Compatible API"]
)

# Anthropic 端点的错误响应转换为标准 Anthropic 错误格式
app.add_exception_handler(StarletteHTTPException, anthropic_exception_handler)

# 挂载设置路由
app.include_router(
    settings_router,
    prefix="/api",
    tags=["Settings Management"]
)

# 挂载多实例（工作台）路由
app.include_router(
    instances_router,
    prefix="/api",
    tags=["Instance Management"]
)

# 健康检查端点
@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy", "service": "codebuddy2api"}


@app.get("/")
async def root():
    """根路径信息"""
    return {
        "service": "CodeBuddy2API",
        "version": "1.1.2",
        "description": "CodeBuddy API proxy with OpenAI-compatible interface",
        "endpoints": {
            "models": "/codebuddy/v1/models",
            "chat": "/codebuddy/v1/chat/completions",
            "credentials": "/codebuddy/v1/credentials",
            "auth_start": "/codebuddy/auth/start",
            "auth_poll": "/codebuddy/auth/poll",
            "auth_callback": "/codebuddy/auth/callback",
            "get_settings": "/api/settings",
            "save_settings": "/api/settings"
        }
    }


if __name__ == "__main__":
    from hypercorn.asyncio import serve
    from hypercorn.config import Config
    
    port = get_server_port()
    host = get_server_host()
    
    logger.info("=" * 60)
    logger.info("Starting CodeBuddy2API")
    logger.info("=" * 60)
    logger.info(f"Main Service: http://{host}:{port}")
    logger.info("=" * 60)
    logger.info("Web Interface:")
    logger.info(f"   Admin Panel: http://{host}:{port}/")
    logger.info("=" * 60)
    logger.info("API Endpoints:")
    logger.info(f"   Models: GET http://{host}:{port}/codebuddy/v1/models")
    logger.info(f"   Chat: POST http://{host}:{port}/codebuddy/v1/chat/completions")
    logger.info(f"   Credentials: GET http://{host}:{port}/codebuddy/v1/credentials")
    logger.info("=" * 60)
    logger.info("Authentication:")
    logger.info("   Set CODEBUDDY_PASSWORD environment variable")
    logger.info("   Use Bearer token in Authorization header")
    logger.info("=" * 60)

    config = Config()
    config.bind = [f"{host}:{port}"]
    config.accesslog = None
    config.errorlog = "-"
    config.loglevel = "INFO"
    config.use_colors = True

    asyncio.run(serve(app, config))
