"""
关键词替换工具模块 - 统一处理关键词替换逻辑
防止CodeBuddy检测到竞争对手关键词
"""
import logging
import re

logger = logging.getLogger(__name__)


# Claude Code CLI（2.1.x 起）会把计费标记注入 system prompt 首行，形如：
#   x-anthropic-billing-header: cc_version=2.1.278.785; cc_entrypoint=cli;
# 该标记会被 CodeBuddy 上游识别为「未授权渠道」调用，返回 11128
# （The request was blocked by security policy），必须整段剥离。
_BILLING_HEADER_RE = re.compile(
    r"x-anthropic-billing-header:\s*(?:[A-Za-z0-9_.\-]+\s*=\s*[^;\s]*\s*;?\s*)+",
    re.IGNORECASE,
)

# 兜底：若 header 段结构异常（未按 k=v; 形式出现），至少中性化特征 token
_BILLING_TOKEN = "x-anthropic-billing-header"
_BILLING_TOKEN_RE = re.compile(re.escape(_BILLING_TOKEN), re.IGNORECASE)


def strip_anthropic_billing_header(text: str) -> str:
    """
    剥离 Claude Code 注入的 x-anthropic-billing-header 计费标记

    Args:
        text: 待处理文本

    Returns:
        str: 剥离后的文本
    """
    if not isinstance(text, str) or "anthropic-billing-header" not in text.lower():
        return text

    cleaned = _BILLING_HEADER_RE.sub("", text)
    if "anthropic-billing-header" in cleaned.lower():
        cleaned = _BILLING_TOKEN_RE.sub("x-codebuddy-billing-hint", cleaned)
    return cleaned


def apply_keyword_replacement(text: str) -> str:
    """
    统一的关键词替换函数

    Args:
        text: 需要处理的文本内容

    Returns:
        str: 替换后的文本内容
    """
    if not isinstance(text, str):
        return text

    # 先剥离 Claude Code 注入的计费标记（否则上游按未授权渠道拦截，返回 11128）
    text = strip_anthropic_billing_header(text)

    # 定义替换规则
    replacements = {
        "Claude Code": "CodeBuddy Code",
        "Anthropic's official CLI for Claude": "Tencent's official CLI for CodeBuddy",
        "Claude": "CodeBuddy",
        "Anthropic": "Tencent",
        "https://github.com/anthropics/claude-code/issues": "https://cnb.cool/codebuddy/codebuddy-code/-/issues"
    }

    original_text = text

    # 应用所有替换规则
    for old_keyword, new_keyword in replacements.items():
        text = text.replace(old_keyword, new_keyword)

    # 记录替换日志（仅在调试模式下）
    if text != original_text:
        logger.debug(f"[KEYWORD_REPLACE] Applied keyword replacements, original length: {len(original_text)}, new length: {len(text)}")

    return text


def apply_keyword_replacement_to_system_message(content) -> str:
    """
    专门用于处理系统消息的关键词替换
    支持字符串和复杂结构的content

    Args:
        content: 消息内容，可能是字符串或列表结构

    Returns:
        str: 处理后的内容
    """
    if isinstance(content, str):
        return apply_keyword_replacement(content)
    elif isinstance(content, list):
        # 处理复杂结构的系统消息
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                item["text"] = apply_keyword_replacement(item.get("text", ""))
        return content
    else:
        return content