"""
Anthropic Messages API 兼容路由
将 Anthropic /v1/messages 请求转换为 OpenAI/CodeBuddy 格式，
并将 CodeBuddy 响应转换回 Anthropic 格式（含 SSE 流式事件）。

让 Claude Desktop 等 Anthropic 协议客户端可以直接使用本地代理。
"""
import asyncio
import json
import time
import uuid
import logging
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, HTTPException, Depends, Request, Header
from fastapi.responses import StreamingResponse, JSONResponse

from .codebuddy_api_client import codebuddy_api_client
from .codebuddy_router import (
    CredentialManager,
    RequestProcessor,
    CodeBuddyStreamService,
    get_codebuddy_api_url,
    get_http_client,
)
from .usage_stats_manager import usage_stats_manager
from .proxy_state import is_enabled as is_proxy_enabled

logger = logging.getLogger(__name__)

router = APIRouter()

# 上游请求串行化锁：避免 Claude Desktop 多路并发触发 CodeBuddy 渠道风控（错误码 11128）
_upstream_lock = asyncio.Lock()
# 上次发送上游请求的时间戳：用于在相邻请求间加入最小间隔，规避频率风控
_last_upstream_ts = [0.0]
# 同一错误连续出现时的指数退避
_consecutive_upstream_failures = [0]

ANTHROPIC_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Content-Type": "text/event-stream",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "*",
    "anthropic-version": "2023-06-01",
}


# --- 认证：兼容 x-api-key 与 Authorization: Bearer ---
async def verify_anthropic_auth(
    x_api_key: Optional[str] = Header(None, alias="x-api-key"),
    authorization: Optional[str] = Header(None),
) -> str:
    from config import get_server_password
    password = get_server_password()
    if not password:
        raise HTTPException(status_code=500, detail="CODEBUDDY_PASSWORD is not configured on the server.")

    token = x_api_key
    if not token and authorization:
        if authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing API key. Provide x-api-key or Authorization: Bearer <token>")
    if token != password:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return token


# --- Anthropic -> OpenAI 请求转换 ---

def _parse_block_text(content: Any) -> str:
    """从 Anthropic content block 中提取文本"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text", ""))
                elif b.get("type") == "thinking":
                    parts.append(b.get("thinking", ""))
        return "".join(parts)
    return str(content) if content is not None else ""


def _convert_content_blocks(role: str, content: Any) -> List[Dict[str, Any]]:
    """
    将 Anthropic 消息 content（str 或 blocks 数组）转为 OpenAI 消息列表。
    返回可能包含多条 OpenAI 消息（如 tool_result 需要拆成 tool 角色消息）。
    """
    messages: List[Dict[str, Any]] = []
    text_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    tool_results: List[Dict[str, Any]] = []

    if isinstance(content, str):
        return [{"role": role, "content": content}]

    if not isinstance(content, list):
        return [{"role": role, "content": str(content) if content is not None else ""}]

    for block in content:
        if not isinstance(block, dict):
            text_parts.append(str(block))
            continue
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "thinking":
            text_parts.append(block.get("thinking", ""))
        elif btype == "tool_use":
            tool_id = block.get("id") or f"call_{uuid.uuid4().hex[:24]}"
            tool_calls.append({
                "id": tool_id,
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                },
            })
        elif btype == "tool_result":
            tool_use_id = block.get("tool_use_id", "")
            tool_results.append({
                "role": "tool",
                "tool_call_id": tool_use_id,
                "content": _parse_block_text(block.get("content", "")),
            })

    # 顺序很关键：OpenAI 要求 tool 结果消息必须紧跟对应的 assistant(tool_calls)，
    # 中间不能插入 user 消息，否则 CodeBuddy 上游报 11148 "tool calls and tool results do not match"。
    # 因此把 tool_results 放在文本消息之前（后合并）。
    if tool_results:
        messages.extend(tool_results)

    if text_parts or tool_calls:
        msg: Dict[str, Any] = {"role": role, "content": "".join(text_parts)}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        messages.append(msg)

    return messages


def _fix_tool_sequence(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    修复工具调用序列，防止 CodeBuddy 上游报 11148 "tool calls and tool results do not match"：
    1. 孤立的 tool 结果消息（无对应 tool_calls）降级为普通 user 文本
    2. 末尾未闭合的 tool_calls（被打断的会话）降级为纯文本 assistant 消息
    """
    pending_tool_ids = set()
    fixed: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            ids = {tc.get("id") for tc in msg["tool_calls"] if tc.get("id")}
            pending_tool_ids |= ids
            fixed.append(msg)
        elif role == "tool":
            tc_id = msg.get("tool_call_id")
            if tc_id and tc_id in pending_tool_ids:
                pending_tool_ids.discard(tc_id)
                fixed.append(msg)
            else:
                # 孤立的 tool 消息 -> user 文本
                fixed.append({"role": "user", "content": f"[Tool result] {msg.get('content', '')}"})
        else:
            fixed.append(msg)

    # 末尾未闭合的工具调用：把最后一个含未闭合 tool_calls 的 assistant 消息降级为纯文本
    if pending_tool_ids:
        for i in range(len(fixed) - 1, -1, -1):
            m = fixed[i]
            if m.get("role") == "assistant" and m.get("tool_calls"):
                ids = {tc.get("id") for tc in m["tool_calls"]}
                if ids & pending_tool_ids:
                    fixed[i] = {k: v for k, v in m.items() if k != "tool_calls"}
                    break
    return fixed


# Claude 官方模型名 -> CodeBuddy 内部可用模型映射。
# Claude Code 会发送 claude-opus-4-8 / claude-sonnet-4-6 等官方模型名，
# CodeBuddy 内部环境没有这些模型，上游对它们触发 11128 渠道风控。
# 这里按档位映射到 CodeBuddy 实际可用模型。
CLAUDE_MODEL_MAP = {
    # Opus 档 -> 最强可用模型
    "claude-opus-4-8": "deepseek-v4-pro",
    "claude-opus-4-1": "deepseek-v4-pro",
    "claude-opus-4-0": "deepseek-v4-pro",
    "claude-4-opus": "deepseek-v4-pro",
    "claude-3-opus": "deepseek-v4-pro",
    "claude-3-7-sonnet": "deepseek-v4-pro",
    # Sonnet 档 -> 均衡模型
    "claude-sonnet-4-6": "deepseek-v4-flash",
    "claude-sonnet-4-5": "deepseek-v4-flash",
    "claude-sonnet-4-0": "deepseek-v4-flash",
    "claude-4-0": "deepseek-v4-flash",
    "claude-4-1": "deepseek-v4-flash",
    "claude-4-5": "deepseek-v4-flash",
    "claude-3-5-sonnet": "deepseek-v4-flash",
    # Haiku 档 -> 快速轻量模型
    "claude-haiku-4-5": "glm-5.0-turbo",
    "claude-haiku-4-0": "glm-5.0-turbo",
    "claude-3-5-haiku": "glm-5.0-turbo",
    "claude-3-haiku": "glm-5.0-turbo",
}


def _normalize_model(model: Any) -> str:
    """规范化模型名：
    - Claude 官方模型名 -> CodeBuddy 可用模型（按档位映射）
    - 兼容 'provider/model' 格式（如 deepseek/deepseek-v4-flash），剥离 provider 前缀
    """
    if not isinstance(model, str):
        return "unknown"
    m = model.strip().lower()
    # Claude 官方模型名映射
    if m in CLAUDE_MODEL_MAP:
        return CLAUDE_MODEL_MAP[m]
    # claude-* 兜底映射
    if m.startswith("claude-"):
        return "deepseek-v4-flash"
    # provider/model -> model（如 deepseek/deepseek-v4-flash）
    if "/" in m and not m.startswith("claude"):
        m = m.split("/", 1)[1]
    return m


def convert_anthropic_to_openai(body: Dict[str, Any]) -> Dict[str, Any]:
    """Anthropic Messages API 请求体 -> OpenAI chat/completions 请求体"""
    oai: Dict[str, Any] = {
        "model": _normalize_model(body.get("model")),
    }
    for key in ("temperature", "top_p", "max_tokens", "seed"):
        if key in body:
            oai[key] = body[key]
    if body.get("stop_sequences"):
        oai["stop"] = body["stop_sequences"]
    if body.get("stream") is not None:
        oai["stream"] = body["stream"]

    # system（字符串或 blocks）
    system_text = _parse_block_text(body.get("system", ""))
    messages: List[Dict[str, Any]] = []
    if system_text:
        messages.append({"role": "system", "content": system_text})

    # messages
    for msg in body.get("messages", []):
        if not isinstance(msg, dict):
            continue
        messages.extend(_convert_content_blocks(msg.get("role", "user"), msg.get("content", "")))

    messages = _fix_tool_sequence(messages)
    oai["messages"] = messages

    # tools: Anthropic {name, description, input_schema} -> OpenAI function tools
    tools = body.get("tools")
    if tools:
        # 裁剪工具数量：CodeBuddy 上游对过多工具触发渠道风控（11128），
        # 官方客户端通常只挂载少数工具。保留前 MAX_TOOLS 个。
        MAX_TOOLS = 8
        oai_tools = []
        for t in tools:
            if not isinstance(t, dict) or len(oai_tools) >= MAX_TOOLS:
                continue
            name = t.get("name", "")
            if not name:
                continue
            # 过滤掉上游可能不接受的复杂/空 schema
            schema = t.get("input_schema")
            if not isinstance(schema, dict) or not schema.get("type"):
                schema = {"type": "object", "properties": {}}
            oai_tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": (t.get("description") or "")[:500],
                    "parameters": schema,
                },
            })
        oai["tools"] = oai_tools

        tc = body.get("tool_choice")
        if isinstance(tc, dict):
            ttype = tc.get("type")
            if ttype == "any":
                oai["tool_choice"] = "required"
            elif ttype == "none":
                oai["tool_choice"] = "none"
            elif ttype == "tool":
                oai["tool_choice"] = {"type": "function", "function": {"name": tc.get("name", "")}}
            else:  # auto / 其他
                oai["tool_choice"] = "auto"

    # 思考模型需要足够的 max_tokens，避免全被思考吃掉
    if oai.get("max_tokens") is None:
        oai["max_tokens"] = 4096
    elif isinstance(oai.get("max_tokens"), int) and oai["max_tokens"] < 256:
        oai["max_tokens"] = 256

    return oai


# --- OpenAI -> Anthropic 响应转换 ---

def _parse_json(text: str) -> Any:
    try:
        return json.loads(text) if text else {}
    except Exception:
        return {}


def _extract_usage(oai_usage: Optional[Dict[str, Any]]) -> Dict[str, int]:
    usage = oai_usage or {}
    return {
        "input_tokens": usage.get("prompt_tokens") or 0,
        "output_tokens": usage.get("completion_tokens") or 0,
    }


def _finish_to_stop_reason(finish_reason: Optional[str]) -> str:
    if finish_reason == "tool_calls":
        return "tool_use"
    if finish_reason == "length":
        return "max_tokens"
    return "end_turn"


def openai_to_anthropic_response(oai: Dict[str, Any], model: str) -> Dict[str, Any]:
    """OpenAI chat.completion -> Anthropic message（非流式）"""
    choice = (oai.get("choices") or [{}])[0]
    msg = choice.get("message", {}) or {}
    content_blocks: List[Dict[str, Any]] = []

    reasoning = msg.get("reasoning_content")
    if reasoning:
        content_blocks.append({"type": "thinking", "thinking": reasoning})

    text = msg.get("content")
    if text:
        content_blocks.append({"type": "text", "text": text})

    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {}) or {}
        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:24]}",
            "name": fn.get("name", ""),
            "input": _parse_json(fn.get("arguments", "{}")),
        })

    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content_blocks,
        "stop_reason": _finish_to_stop_reason(choice.get("finish_reason")),
        "stop_sequence": None,
        "usage": _extract_usage(oai.get("usage")),
    }


def _sse_event(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_openai_to_anthropic(payload: Dict[str, Any], headers: Dict[str, str], model: str):
    """
    将上游 CodeBuddy(OpenAI SSE) 流实时转换为 Anthropic SSE 事件流。
    Claude Desktop 依赖此格式，且需要即时输出避免超时。
    """
    client = await get_http_client()
    message_id = f"msg_{uuid.uuid4().hex[:24]}"
    output_tokens = 0
    input_tokens = 0

    # 工具调用聚合（按 id）
    tool_map: Dict[str, Dict[str, Any]] = {}
    tool_order: List[str] = []
    current_tool_id: Optional[str] = None

    async with client.stream("POST", get_codebuddy_api_url(), json=payload, headers=headers) as response:
        if response.status_code != 200:
            error_text = (await response.aread()).decode("utf-8", errors="ignore")
            err = {
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": f"Upstream error {response.status_code}: {error_text[:200]}",
                },
            }
            yield f"event: error\ndata: {json.dumps(err, ensure_ascii=False)}\n\n"
            return

        buffer = ""
        block_index = 0
        text_block_open = False
        sent_message_start = False
        finish_reason: Optional[str] = None

        async for chunk in response.aiter_text(chunk_size=8192):
            if not chunk:
                continue
            buffer += chunk
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line or line.startswith(":") or line.startswith("event:"):
                    continue
                if line.startswith("data: "):
                    data_str = line[6:].strip()
                elif line == "data:":
                    data_str = ""
                else:
                    continue

                if data_str == "[DONE]":
                    # 结束事件
                    if text_block_open:
                        yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": 0})
                        text_block_open = False
                    for tool_id in tool_order:
                        if tool_id in tool_map:
                            yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": tool_map[tool_id].get("_index", 1)})
                    if sent_message_start:
                        yield _sse_event("message_delta", {
                            "type": "message_delta",
                            "delta": {"stop_reason": _finish_to_stop_reason(finish_reason), "stop_sequence": None},
                            "usage": {"output_tokens": output_tokens},
                        })
                        yield _sse_event("message_stop", {"type": "message_stop"})
                    return

                try:
                    obj = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                choices = obj.get("choices")
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                if choice.get("finish_reason"):
                    finish_reason = choice.get("finish_reason")

                # usage（首个 chunk 可能带 prompt_tokens）
                if obj.get("usage"):
                    u = obj.get("usage")
                    if u.get("prompt_tokens"):
                        input_tokens = u.get("prompt_tokens") or 0
                    if u.get("completion_tokens"):
                        output_tokens = u.get("completion_tokens") or 0

                # 工具调用（分片）
                tool_calls = delta.get("tool_calls") or []
                if tool_calls:
                    for tc in tool_calls:
                        tc_id = tc.get("id")
                        if tc_id:
                            if tc_id not in tool_map:
                                # 新工具调用 -> content_block_start(tool_use)
                                tool_map[tc_id] = {
                                    "_index": block_index,
                                    "id": tc_id,
                                    "type": "tool_use",
                                    "name": "",
                                    "input": {},
                                    "_args": "",
                                }
                                tool_order.append(tc_id)
                                yield _sse_event("content_block_start", {
                                    "type": "content_block_start",
                                    "index": block_index,
                                    "content_block": {"type": "tool_use", "id": tc_id, "name": "", "input": {}},
                                })
                                block_index += 1
                            current_tool_id = tc_id
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                tool_map[tc_id]["name"] = fn.get("name")
                            if fn.get("arguments"):
                                tool_map[tc_id]["_args"] += fn.get("arguments")
                                yield _sse_event("content_block_delta", {
                                    "type": "content_block_delta",
                                    "index": tool_map[tc_id]["_index"],
                                    "delta": {"type": "input_json_delta", "partial_json": fn.get("arguments")},
                                })
                        elif current_tool_id and current_tool_id in tool_map:
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                tool_map[current_tool_id]["name"] = fn.get("name")
                            if fn.get("arguments"):
                                tool_map[current_tool_id]["_args"] += fn.get("arguments")
                                yield _sse_event("content_block_delta", {
                                    "type": "content_block_delta",
                                    "index": tool_map[current_tool_id]["_index"],
                                    "delta": {"type": "input_json_delta", "partial_json": fn.get("arguments")},
                                })

                # 文本 / 思考内容
                text = delta.get("content")
                reasoning = delta.get("reasoning_content")
                if text or reasoning:
                    if not sent_message_start:
                        # 发送 message_start 与首个 text 块
                        msg_obj = {
                            "id": message_id,
                            "type": "message",
                            "role": "assistant",
                            "model": model,
                            "content": [],
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {"input_tokens": input_tokens or 1, "output_tokens": 0},
                        }
                        yield _sse_event("message_start", {"type": "message_start", "message": msg_obj})
                        sent_message_start = True
                        yield _sse_event("content_block_start", {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "text", "text": ""},
                        })
                        text_block_open = True
                        output_tokens += 1

                    if reasoning:
                        yield _sse_event("content_block_delta", {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "thinking_delta", "thinking": reasoning},
                        })
                        output_tokens += max(1, len(reasoning) // 4)
                    if text:
                        yield _sse_event("content_block_delta", {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": text},
                        })
                        output_tokens += max(1, len(text) // 4)

                # 若已结束（本 chunk 带 finish_reason）且没有更多块，提前收尾由 [DONE] 处理
                if choice.get("finish_reason"):
                    finish_reason = choice.get("finish_reason")

        # 流结束但未收到 [DONE]（兜底）
        if text_block_open:
            yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": 0})
        for tool_id in tool_order:
            if tool_id in tool_map:
                yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": tool_map[tool_id]["_index"]})
        if sent_message_start:
            yield _sse_event("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": _finish_to_stop_reason(finish_reason), "stop_sequence": None},
                "usage": {"output_tokens": output_tokens},
            })
            yield _sse_event("message_stop", {"type": "message_stop"})


# --- API Endpoints ---

@router.post("/v1/messages")
async def anthropic_messages(
    request: Request,
    _token: str = Depends(verify_anthropic_auth),
):
    """Anthropic Messages API 兼容端点"""
    try:
        # 代理开关：关闭时不转发上游
        if not is_proxy_enabled():
            raise HTTPException(
                status_code=503,
                detail="代理未启动，请在桌面端「工作台」点击「开启代理」",
            )

        try:
            request_body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON request body")

        if not isinstance(request_body, dict) or not request_body.get("messages"):
            raise HTTPException(status_code=400, detail="Request body must include 'messages' array")

        # 诊断日志：记录请求结构摘要（不含消息内容）
        try:
            _msgs = request_body.get("messages", [])
            _system = request_body.get("system")
            logger.info(
                "Anthropic request: model=%s stream=%s msgs=%d system=%s tools=%s keys=%s",
                request_body.get("model"),
                request_body.get("stream"),
                len(_msgs) if isinstance(_msgs, list) else -1,
                "yes" if _system else "no",
                len(request_body.get("tools") or []) if isinstance(request_body.get("tools"), list) else 0,
                sorted(k for k in request_body.keys() if k not in ("messages",)),
            )
        except Exception:
            pass

        # 转换请求格式
        oai_payload = convert_anthropic_to_openai(request_body)

        # 诊断日志：记录转换后上游 payload 摘要
        try:
            _oai_msgs = oai_payload.get("messages") or []
            _oai_tools = oai_payload.get("tools") or []
            _schema_keys = sorted({
                k for t in _oai_tools
                for k in ((t.get("function") or {}).get("parameters") or {}).keys()
            })
            _tool_names = [((t.get("function") or {}).get("name") or "") for t in _oai_tools][:12]
            _tool_roles = sum(1 for m in _oai_msgs if m.get("role") == "tool")
            _call_msgs = sum(1 for m in _oai_msgs if m.get("tool_calls"))
            logger.info(
                "Upstream payload: model=%s msgs=%d tool_msgs=%d toolcall_msgs=%d tools=%d(%s) schema_keys=%s oai_keys=%s",
                oai_payload.get("model"),
                len(_oai_msgs),
                _tool_roles,
                _call_msgs,
                len(_oai_tools),
                ",".join(_tool_names)[:120],
                _schema_keys,
                sorted(k for k in oai_payload.keys()),
            )
        except Exception:
            pass
        # 预处理（CodeBuddy 只支持流式，强制 stream=true）
        oai_payload = RequestProcessor.prepare_payload(oai_payload)

        # 获取认证并生成上游请求头
        auth_context = CredentialManager.get_auth_context()
        headers = codebuddy_api_client.generate_codebuddy_headers(
            auth=auth_context,
            user_id=auth_context.get("user_id") or None,
        )
        usage_stats_manager.record_model_usage(oai_payload.get("model", "unknown"))

        model = request_body.get("model", oai_payload.get("model", "unknown"))
        want_stream = bool(request_body.get("stream", False))

        try:
            # 无论是流式还是非流式，都先走"内部非流式聚合 + 自动重试"，
            # 这样上游错误（11128/6004/429/500）能被捕获并转成 Anthropic 标准错误格式，
            # Claude Desktop 不会因 HTTP 4xx 状态码而显示笼统的 "Invalid request"。
            service = CodeBuddyStreamService()
            last_he = None
            oai_response = None
            for attempt in range(3):
                try:
                    # 串行化 + 最小间隔 + 指数退避，应对 CodeBuddy 渠道风控（11128）
                    async with _upstream_lock:
                        # 最小间隔：根据连续失败次数动态调整（2.0s 起步，指数退避）
                        import time as _time
                        now = _time.time()
                        min_interval = 2.0 * (2 ** _consecutive_upstream_failures[0])
                        min_interval = min(min_interval, 15.0)  # 上限 15s
                        wait = _last_upstream_ts[0] + min_interval - now
                        if wait > 0:
                            await asyncio.sleep(wait)
                        oai_response = await service.handle_non_stream_response(oai_payload, headers)
                        _last_upstream_ts[0] = _time.time()
                        _consecutive_upstream_failures[0] = 0
                    break
                except HTTPException as he:
                    last_he = he
                    if attempt < 2 and he.status_code in (400, 429, 500, 502):
                        # 上游临时性错误：退避重试。11128（400）尤为关键，需要更长等待
                        _consecutive_upstream_failures[0] += 1
                        backoff = 2.0 * (2 ** _consecutive_upstream_failures[0])
                        backoff = min(backoff, 15.0)
                        logger.warning(
                            f"Anthropic upstream {he.status_code} (consec {_consecutive_upstream_failures[0]}x), "
                            f"retrying in {backoff:.1f}s (attempt {attempt+1}/3)"
                        )
                        await asyncio.sleep(backoff)
                        continue
                    break

            if oai_response is None:
                # 诊断：把失败的上游请求完整保存，便于本地复现 11128
                try:
                    _dbg_path = "/tmp/codebuddy_anthropic_fail.json"
                    with open(_dbg_path, "w", encoding="utf-8") as _f:
                        json.dump({
                            "timestamp": time.time(),
                            "headers": {k: v for k, v in headers.items() if k.lower() not in ("authorization",)},
                            "payload": oai_payload,
                            "error_status": last_he.status_code if last_he else None,
                            "error_detail": str(getattr(last_he, "detail", "")),
                        }, _f, ensure_ascii=False, indent=2, default=str)
                    logger.info("11128 debug payload saved to %s", _dbg_path)
                except Exception:
                    pass
                return _convert_http_exception_to_anthropic(last_he)

            anthropic_msg = openai_to_anthropic_response(oai_response, model)
            if want_stream:
                return StreamingResponse(
                    _fake_anthropic_sse(anthropic_msg),
                    media_type="text/event-stream",
                    headers=ANTHROPIC_SSE_HEADERS,
                )
            return JSONResponse(content=anthropic_msg)
        except HTTPException as he:
            return _convert_http_exception_to_anthropic(he)
    except HTTPException as he:
        return _convert_http_exception_to_anthropic(he)
    except Exception as e:
        logger.error(f"Anthropic Messages API error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": f"Internal server error: {str(e)}",
                },
            },
        )


async def _fake_anthropic_sse(message: Dict[str, Any]):
    """
    将一条完整 Anthropic message 拆解为 SSE 事件流输出给 Claude Desktop。
    用 chunked 模拟打字机效果，避免大输出导致首字延迟过高。
    """
    msg_id = message.get("id") or f"msg_{uuid.uuid4().hex[:24]}"
    blocks = message.get("content") or [{"type": "text", "text": ""}]
    usage = message.get("usage") or {"input_tokens": 0, "output_tokens": 0}
    stop_reason = message.get("stop_reason") or "end_turn"

    msg_obj = {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": message.get("model", ""),
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": usage,
    }
    yield _sse_event("message_start", {"type": "message_start", "message": msg_obj})

    for idx, block in enumerate(blocks):
        btype = block.get("type")
        if btype == "text":
            text = block.get("text", "") or ""
            yield _sse_event("content_block_start", {
                "type": "content_block_start",
                "index": idx,
                "content_block": {"type": "text", "text": ""},
            })
            # 分块：每 16 字符一个 delta，模拟打字机
            step = 16
            for i in range(0, len(text), step):
                chunk = text[i:i + step]
                yield _sse_event("content_block_delta", {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {"type": "text_delta", "text": chunk},
                })
                if len(text) > step:
                    await asyncio.sleep(0.01)
            yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": idx})
        elif btype == "thinking":
            thinking = block.get("thinking", "") or ""
            yield _sse_event("content_block_start", {
                "type": "content_block_start",
                "index": idx,
                "content_block": {"type": "thinking", "thinking": ""},
            })
            step = 32
            for i in range(0, len(thinking), step):
                chunk = thinking[i:i + step]
                yield _sse_event("content_block_delta", {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {"type": "thinking_delta", "thinking": chunk},
                })
                await asyncio.sleep(0.01)
            yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": idx})
        elif btype == "tool_use":
            tool_id = block.get("id") or f"toolu_{uuid.uuid4().hex[:24]}"
            tool_name = block.get("name", "")
            tool_input = block.get("input", {})
            yield _sse_event("content_block_start", {
                "type": "content_block_start",
                "index": idx,
                "content_block": {"type": "tool_use", "id": tool_id, "name": tool_name, "input": {}},
            })
            input_json = json.dumps(tool_input, ensure_ascii=False)
            step = 24
            for i in range(0, len(input_json), step):
                yield _sse_event("content_block_delta", {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {"type": "input_json_delta", "partial_json": input_json[i:i + step]},
                })
                await asyncio.sleep(0.01)
            yield _sse_event("content_block_stop", {"type": "content_block_stop", "index": idx})

    yield _sse_event("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": usage.get("output_tokens", 0)},
    })
    yield _sse_event("message_stop", {"type": "message_stop"})


def _convert_http_exception_to_anthropic(he: HTTPException) -> JSONResponse:
    """将代理内部 HTTPException 转换为 Anthropic 标准错误响应"""
    code = he.status_code
    if code == 429:
        err_type = "rate_limit_error"
    elif code == 401:
        err_type = "authentication_error"
    elif code == 403:
        err_type = "permission_error"
    elif code == 404:
        err_type = "not_found_error"
    elif code in (408, 504):
        err_type = "timeout_error"
    elif code >= 500:
        err_type = "api_error"
    else:
        err_type = "invalid_request_error"
    detail = he.detail if isinstance(he.detail, str) else json.dumps(he.detail, ensure_ascii=False)
    return JSONResponse(
        status_code=code if code >= 400 else 500,
        content={
            "type": "error",
            "error": {
                "type": err_type,
                "message": detail,
            },
        },
    )


async def anthropic_exception_handler(request: Request, exc: HTTPException):
    """
    把 Anthropic 端点的 HTTPException 转成 Anthropic 标准错误格式。
    其他端点（OpenAI 兼容）保持原 FastAPI `{"detail":...}` 不变。
    """
    if request.url.path.startswith(("/v1/messages", "/anthropic/")):
        return _convert_http_exception_to_anthropic(exc)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@router.get("/anthropic/v1/models")
async def anthropic_list_models(_token: str = Depends(verify_anthropic_auth)):
    """Anthropic 模型列表兼容端点"""
    from .codebuddy_router import get_available_models_list
    models = get_available_models_list()
    return {
        "data": [
            {
                "type": "model",
                "id": m,
                "display_name": m,
                "created_at": "2026-01-01T00:00:00Z",
            }
            for m in models
        ],
        "has_more": False,
        "first_id": None,
        "last_id": None,
    }
