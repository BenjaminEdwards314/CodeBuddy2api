"""补充探测：针对新发现的可用模型及其近邻变体。"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8001/codebuddy/v1/chat/completions"
PASSWORD = "cb_local_pwd"

CANDIDATES = [
    # 基于已发现可用模型的近邻
    "deepseek-v3.2", "deepseek-v3.2-exp", "deepseek-v32",
    "glm-5.0", "glm5.0", "glm-5.0-turbo", "glm5.0-turbo", "glm-5", "glm-5.2",
    "auto", "auto-chat",
    # hunyuan 其它写法
    "hunyuan-t1", "hunyuan-pro", "hunyuan-standard", "hunyuan-lite",
    "hy3.0", "hy3-turbo", "hy3-pro", "hunyuan3-pro",
    # 其它可能的中文厂商
    "qwen-max", "qwen3-max", "qwen3-235b", "qwen2.5-72b", "qwq", "qwq-32b",
    "kimi-k2", "moonshot-v1-8k", "abab7", "abab6.5",
    "doubao-seed-1.6", "doubao-1.5-pro", "doubao-pro-32k",
    "step-2", "step-1.5", "minimax-text-01", "MiniMax-Text-01",
    "yi-lightning", "chatglm4", "glm-4.6",
    "claude-sonnet-4-20250514", "claude-opus-4-20250514",
    "gpt-5.1", "gpt-5.2", "o4-mini", "o3",
    "gemini-3-pro", "gemini-3-flash",
    "ernie-5.0", "ernie-4.5-8k",
]

PROMPT = "用一句话回复：1+1=?"


def test_model(model):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": False,
        "max_tokens": 32,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {PASSWORD}")
    req.add_header("Content-Type", "application/json")
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            obj = json.loads(body)
            if "error" in obj:
                return False, f"err:{obj['error'].get('message','')[:80]}"
            content = obj.get("choices", [{}])[0].get("message", {}).get("content", "")
            return True, content[:60]
    except urllib.error.HTTPError as e:
        return False, f"HTTP{e.code}:{e.read().decode('utf-8','ignore')[:100]}"
    except Exception as e:
        return False, f"exc:{type(e).__name__}"


def main():
    for m in CANDIDATES:
        ok, info = test_model(m)
        print(f"[{'OK  ' if ok else 'FAIL'}] {m:28s} {info}  ({time.time()-start:.1f}s)" if False else f"[{'OK  ' if ok else 'FAIL'}] {m:28s} {info}")
        time.sleep(0.2)
    print("\ndone")


if __name__ == "__main__":
    main()
