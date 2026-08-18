#!/usr/bin/env python3
"""
补丁脚本 v3：合并客户端 tools + 修复每日整理 created_at bug。
在 Docker 构建时自动执行（Dockerfile 里 RUN python patch_main.py）。

v3 变更：
1. 继承 v2 的客户端工具合并逻辑（流式/非流式都处理）
2. 新增：修复 daily_digest.py 的 created_at 字符串 bug
"""

import os

# ============================================================
# 补丁1：main.py — 合并客户端 tools
# ============================================================

MAIN_PY = "/app/main.py"

if os.path.exists(MAIN_PY):
    with open(MAIN_PY, "r", encoding="utf-8") as f:
        code = f.read()

    if "client_passthrough" not in code:
        # ---- 编辑1：合并客户端 tools ----
        old1 = '    # ========== Tool Call 模式（MCP 和/或 auto 搜索） ==========\n    if openai_tools and is_stream:'

        new1 = '''    # ========== 合并客户端原始 tools ==========
    if is_stream and (openai_tools or body.get("tools")):
        client_tools = body.pop("tools", None) or []
        if client_tools:
            openai_tools.extend(client_tools)
            for ct in client_tools:
                ct_name = (ct.get("function") or {}).get("name", "")
                if ct_name and ct_name not in tool_map:
                    tool_map[ct_name] = {"type": "client_passthrough"}
            print(f"🔧 合并了 {len(client_tools)} 个客户端工具，总计 {len(openai_tools)} 个工具")

    # ========== Tool Call 模式（MCP 和/或 auto 搜索） ==========
    if openai_tools and is_stream:'''

        if old1 not in code:
            print("patch: ERROR - cannot find main.py insertion point 1")
            exit(1)
        code = code.replace(old1, new1, 1)

        # ---- 编辑2a：工具分组加入 client_passthrough ----
        old2a = '        mcp_parsed = [p for p in parsed if tool_map.get(p["name"], {}).get("type") not in ("gateway_builtin", "drawer", "meta", "external_mcp")]'

        new2a = '''        client_parsed = [p for p in parsed if tool_map.get(p["name"], {}).get("type") == "client_passthrough"]
        mcp_parsed = [p for p in parsed if tool_map.get(p["name"], {}).get("type") not in ("gateway_builtin", "drawer", "meta", "external_mcp", "client_passthrough")]'''

        if old2a not in code:
            print("patch: ERROR - cannot find main.py insertion point 2a")
            exit(1)
        code = code.replace(old2a, new2a, 1)

        # ---- 编辑2b：客户端工具调用处理 ----
        old2b = '        approved_categories = set()  # request-local; never infer from shared session state'

        new2b = '''        approved_categories = set()  # request-local; never infer from shared session state

        # ---- 客户端工具调用处理 ----
        if client_parsed and not (gw_parsed or drawer_parsed or meta_parsed or external_parsed or mcp_parsed):
            print(f"📤 客户端工具调用：{[p['name'] for p in client_parsed]}，返回给客户端执行")
            content = message.get("content") or ""
            if content:
                yield f"data: {json.dumps({'choices': [{'delta': {'content': content}, 'finish_reason': None}], 'model': model}, ensure_ascii=False)}\\n\\n"
            for tc in tool_calls:
                tc_name = tc.get("function", {}).get("name", "")
                if tool_map.get(tc_name, {}).get("type") == "client_passthrough":
                    yield f"data: {json.dumps({'choices': [{'delta': {'tool_calls': [tc]}, 'finish_reason': None}], 'model': model}, ensure_ascii=False)}\\n\\n"
            yield f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}], 'model': model}, ensure_ascii=False)}\\n\\n"

            _client_assistant_msg = (content + " " + " ".join(
                tc.get("function", {}).get("name", "")
                for tc in tool_calls
                if tool_map.get(tc.get("function", {}).get("name", ""), {}).get("type") == "client_passthrough"
            )).strip()
            if _client_assistant_msg and record_events:
                _client_emo = merge_emotion_levels(
                    detect_emotion_from_user_msg(user_message),
                    detect_emotion_from_response(_client_assistant_msg))
                _spawn_background_task(
                    _finalize_stream_memories(
                        mem_enabled, session_id, user_message,
                        _client_assistant_msg, model, _client_emo, project_id,
                        is_regenerate, record_events=record_events,
                        extract_enabled=extract_enabled,
                        usage=_usage_total, ledger_ctx=ledger_ctx))

            yield "data: [DONE]\\n\\n"
            return

        # 混合调用：给客户端工具占位结果
        for _cp in client_parsed:
            tool_results[_cp["id"]] = "[client_tool] 此工具由客户端执行，已延迟。"'''

        if old2b not in code:
            print("patch: ERROR - cannot find main.py insertion point 2b")
            exit(1)
        code = code.replace(old2b, new2b, 1)

        with open(MAIN_PY, "w", encoding="utf-8") as f:
            f.write(code)
        print("patch: main.py patched successfully")
    else:
        print("patch: main.py already patched, skipping")
else:
    print("patch: ERROR - main.py not found")
    exit(1)


# ============================================================
# 补丁2：daily_digest.py — 修复 created_at 字符串 bug
# ============================================================

DIGEST_PY = "/app/daily_digest.py"

if os.path.exists(DIGEST_PY):
    with open(DIGEST_PY, "r", encoding="utf-8") as f:
        digest_code = f.read()

    if "fix_created_at_str" not in digest_code:
        # 把字符串 f"{date_str}T00:00:00+08:00" 替换为 datetime 对象
        old3 = '''                f"{date_str}T00:00:00+08:00", cat_id'''

        new3 = '''                datetime.strptime(date_str + "T00:00:00+08:00", "%Y-%m-%dT%H:%M:%S%z"), cat_id  # fix_created_at_str'''

        if old3 not in digest_code:
            print("patch: WARNING - cannot find daily_digest.py insertion point, skipping this fix")
        else:
            digest_code = digest_code.replace(old3, new3, 1)
            with open(DIGEST_PY, "w", encoding="utf-8") as f:
                f.write(digest_code)
            print("patch: daily_digest.py patched successfully (created_at fix)")
    else:
        print("patch: daily_digest.py already patched, skipping")
else:
    print("patch: WARNING - daily_digest.py not found, skipping this fix")

print("patch: all done")
