"""批量测试 CodeBuddy2api 代理透传的模型名是否真实可用。"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8001/codebuddy/v1/chat/completions"
PASSWORD = "cb_local_pwd"

# 候选模型名（含已知可用/不可用 + 各种常见写法扩展）
CANDIDATES = [
    # 已知可用
    "deepseek-v3", "deepseek-r1", "hy3", "glm-5.1",
    # deepseek 变体
    "deepseek-v3.1", "deepseek-v3.2", "deepseek-v3.1-terminus",
    "deepseek-r1-0528", "deepseek-r1-distill", "deepseek-chat", "deepseek-reasoner",
    "deepseek-v2", "deepseek-v2.5",
    # hunyuan 变体
    "hunyuan", "hunyuan-turbo", "hunyuan-3", "hunyuan3", "hunyuan-standard",
    "hunyuan-pro", "hunyuan-t1", "hunyuan-a13b", "hy3.0", "hy3-turbo",
    # glm 变体
    "glm-5", "glm-5.0", "glm5.1", "glm-4", "glm-4-plus", "glm-4.5", "glm-4.6", "glm-z1",
    # claude 变体
    "claude-4.0", "claude-4", "claude-4-sonnet", "claude-opus-4", "claude-sonnet-4",
    "claude-3.7", "claude-3.7-sonnet", "claude-3.5-sonnet", "claude-3.5", "claude-3-opus",
    # gpt 变体
    "gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5.1", "gpt-4o", "gpt-4o-mini",
    "o4-mini", "o3", "o3-mini", "gpt-4.1", "gpt-4.1-mini",
    # gemini 变体
    "gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash", "gemini-1.5-pro",
    # 其他
    "auto-chat", "auto", "qwen-max", "qwen2.5", "qwen3", "qwen3-max",
    "kimi-k2", "moonshot-v1", "minimax", "abab", "step", "doubao", "doubao-pro",
    "ernie-4.5", "yi-large", "mistral-large", "llama-3.1", "grok-3",
    "codestral", "command-r", "baichuan", "chatglm",
]

PROMPT = "你好，请用一句话回复：1+1等于几。"


def test_model(model):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": False,
        "max_tokens": 64,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {PASSWORD}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            obj = json.loads(body)
            if "error" in obj:
                return False, f"error: {obj['error'].get('message', '')[:120]}"
            msg = obj.get("choices", [{}])[0].get("message", {})
            content = msg.get("content", "")
            return True, content[:80]
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")[:200]
        return False, f"HTTP {e.code}: {detail}"
    except Exception as e:
        return False, f"exc: {type(e).__name__}: {str(e)[:120]}"


def main():
    results = []
    for m in CANDIDATES:
        ok, info = test_model(m)
        results.append((m, ok, info))
        tag = "OK  " if ok else "FAIL"
        print(f"[{tag}] {m:28s} {info}")
        time.sleep(0.3)
    ok_models = [m for m, ok, _ in results if ok]
    print("\n==== 可用模型 ====")
    print(", ".join(ok_models))
    print(f"\n总计: {len(ok_models)}/{len(CANDIDATES)} 可用")


if __name__ == "__main__":
    main()
