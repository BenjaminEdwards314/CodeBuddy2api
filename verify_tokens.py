#!/usr/bin/env python3
"""
验证 codebuddy 凭证是否真的有效。

背景：管理界面上的「剩余 45d 10h」只是 created_at + expires_in 的本地算术，
      完全不联网。token 被吊销、封号、限额用尽时，那个数字依然显示 45 天。
      唯一可信的验证方式是拿 token 真打一次 API。

用法：
    python3 verify_tokens.py              # 验证全部凭证
    python3 verify_tokens.py --index 0    # 只验证第 1 个
    python3 verify_tokens.py --model glm-5.3-flash
    python3 verify_tokens.py --json       # 输出 JSON，便于脚本消费

退出码：0 = 全部有效；1 = 存在无效凭证；2 = 脚本自身出错
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

# CodeBuddy 的 chat 接口只支持流式；非流式会返回 400，
# 那个 400 意味着「已通过鉴权」，不能当成失败。
DEFAULT_HOST = "https://copilot.tencent.com"
DEFAULT_MODEL = "deepseek-v4-flash"
TIMEOUT = 60

HERE = os.path.dirname(os.path.abspath(__file__))
CREDS_DIR = os.path.join(HERE, ".codebuddy_creds")


def load_credentials(creds_dir):
    """按文件名排序读取凭证，保持与界面一致的顺序。"""
    if not os.path.isdir(creds_dir):
        raise SystemExit(f"凭证目录不存在: {creds_dir}")
    files = sorted(f for f in os.listdir(creds_dir) if f.endswith(".json"))
    if not files:
        raise SystemExit(f"凭证目录为空: {creds_dir}")
    out = []
    for i, fn in enumerate(files):
        path = os.path.join(creds_dir, fn)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            out.append({"index": i, "filename": fn, "user_id": "?", "error": f"读取失败: {exc}"})
            continue
        out.append(
            {
                "index": i,
                "filename": fn,
                "user_id": data.get("user_id") or "?",
                "token": data.get("bearer_token"),
                "created_at": data.get("created_at"),
                "expires_in": data.get("expires_in"),
            }
        )
    return out


def local_expiry_note(cred):
    """本地推算的过期时间，仅作参考（不代表 token 一定可用）。"""
    ca, ei = cred.get("created_at"), cred.get("expires_in")
    if not ca or not ei:
        return "无过期信息"
    left = (ca + ei) - time.time()
    if left <= 0:
        return "已过期"
    return "剩 %.1f 天" % (left / 86400)


def jwt_exp(token):
    """从 JWT payload 读 exp；失败返回 None。"""
    try:
        import base64

        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part)).get("exp")
    except Exception:
        return None


def verify_one(cred, host, model):
    """真打一次流式 chat 请求。返回 (ok, 说明, 耗时秒)。"""
    token = cred.get("token")
    if not token:
        return False, "没有 bearer_token", 0.0

    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "回复：ok"}],
            "max_tokens": 8,
            "stream": True,
        }
    ).encode()

    req = urllib.request.Request(
        host.rstrip("/") + "/v2/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "codebuddy2api-verify",
        },
    )

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            got = ""
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data: "):
                    continue
                chunk = line[6:]
                if chunk == "[DONE]":
                    break
                try:
                    delta = json.loads(chunk)["choices"][0]["delta"]
                    got += delta.get("content") or ""
                except Exception:
                    pass
            dt = time.time() - t0
            # HTTP 200 且拿到内容 => 确定有效
            if got:
                return True, f"OK，返回 {got.strip()[:20]!r}", dt
            return True, "OK（200，但本次无文本内容）", dt

    except urllib.error.HTTPError as exc:
        dt = time.time() - t0
        try:
            payload = json.loads(exc.read().decode("utf-8", "replace"))
            detail = payload.get("msg") or payload.get("error_msg") or str(payload)[:120]
        except Exception:
            detail = "（无法解析响应体）"

        if exc.code == 401:
            return False, f"无效：401 未授权（token 过期/被吊销）", dt
        if exc.code == 403:
            return False, f"无效：403 被拒绝（可能封号或无权访问）", dt
        if exc.code == 429:
            return False, f"限额：429 触发限流（token 本身有效）", dt
        if exc.code == 400 and "not supported" in str(detail):
            # 鉴权已通过，只是不支持非流式——不应出现，因为我们是流式请求
            return True, f"OK（鉴权通过；{detail[:50]}）", dt
        return False, f"HTTP {exc.code}：{detail[:80]}", dt

    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}", time.time() - t0


def main():
    ap = argparse.ArgumentParser(description="验证 codebuddy 凭证是否有效")
    ap.add_argument("--creds-dir", default=CREDS_DIR, help="凭证目录")
    ap.add_argument("--host", default=DEFAULT_HOST, help="API 地址")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="用于验证的模型")
    ap.add_argument("--index", type=int, help="只验证指定序号的凭证（从 0 开始）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args()

    creds = load_credentials(args.creds_dir)
    if args.index is not None:
        creds = [c for c in creds if c["index"] == args.index]
        if not creds:
            raise SystemExit(f"没有序号为 {args.index} 的凭证")

    results = []
    if not args.json:
        print(f"逐个真打 API 验证（{args.host}/v2/chat/completions，model={args.model}）\n")

    for cred in creds:
        ok, note, dt = verify_one(cred, args.host, args.model)
        rec = {
            "index": cred["index"],
            "user_id": cred["user_id"],
            "filename": cred["filename"],
            "valid": ok,
            "note": note,
            "latency_s": round(dt, 2),
            "local_expiry": local_expiry_note(cred),
            "jwt_exp": jwt_exp(cred.get("token") or ""),
        }
        results.append(rec)
        if not args.json:
            mark = "✅ 有效" if ok else "❌ 无效"
            print(f"{mark}  [{cred['index']}] {cred['user_id']:<18} {note}")
            print(f"          本地推算过期: {rec['local_expiry']}   耗时 {dt:.1f}s")

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        good = sum(1 for r in results if r["valid"])
        print(f"\n合计 {len(results)} 个，有效 {good} 个，无效 {len(results) - good} 个")

    return 0 if all(r["valid"] for r in results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
