#!/usr/bin/env python3
"""
补丁脚本 v2：让 kiwi-mem 合并客户端 tools 而不是替换。
在 Docker 构建时自动执行（Dockerfile 里 RUN python patch_main.py）。

v2 修复：
1. 非流式模式下保留 body["tools"]，不再丢失客户端工具
2. 混合工具调用（客户端+网关）时给客户端工具占位结果，不丢弃网关工具
3. 客户端工具调用结束时后台保存对话+提取记忆
"""

MAIN_PY = "/app/main.py"

with open(MAIN_PY, "r", encoding="utf-8") as f:
    code = f.read()

# ---- 检查是否已经打过补丁 ----
if "client_passthrough" in code:
    print("patch_main.py: already patched, skipping")
    exit(0)

# ================================================================
# 编辑1：在 Tool Call 模式判断之前，合并客户端 tools
# 原代码：直接用网关工具，客户端 body["tools"] 被忽略
# 补丁后：流式模式下把客户端 tools 合并进来，标记为 client_passthrough
# ================================================================
old1 = '    # ========== Tool Call 模式（MCP 和/或 auto 搜索） ==========\n    if openai_tools and is_stream:'

new1 = '''    # ========== 合并客户端原始 tools ==========
    # 只在流式模式下合并；非流式路径保留 body["tools"] 原样透传给上游。
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
    print("patch_main.py: ERROR - cannot find insertion point 1")
    exit(1)
code = code.replace(old1, new1, 1)


# ================================================================
# 编辑2a：在工具分组中加入 client_passthrough 类型
# 原代码：mcp_parsed 包含所有非网关/抽屉/meta/外部MCP的工具
# 补丁后：client_passthrough 从 mcp_parsed 中排除，单独分组
# ================================================================
old2a = '        mcp_parsed = [p for p in parsed if tool_map.get(p["name"], {}).get("type") not in ("gateway_builtin", "drawer", "meta", "external_mcp")]'

new2a = '''        client_parsed = [p for p in parsed if tool_map.get(p["name"], {}).get("type") == "client_passthrough"]
        mcp_parsed = [p for p in parsed if tool_map.get(p["name"], {}).get("type") not in ("gateway_builtin", "drawer", "meta", "external_mcp", "client_passthrough")]'''

if old2a not in code:
    print("patch_main.py: ERROR - cannot find insertion point 2a")
    exit(1)
code = code.replace(old2a, new2a, 1)


# ================================================================
# 编辑2b：在 tool_results 定义之后插入客户端工具调用处理
# 纯客户端调用：原样返回 tool_calls 给客户端 APP 执行
# 混合调用：给客户端工具占位结果，继续执行网关工具
# ================================================================
old2b = '        approved_categories = set()  # request-local; never infer from shared session state'

new2b = '''        approved_categories = set()  # request-local; never infer from shared session state

        # ---- 客户端工具调用处理 ----
        # 纯客户端工具调用：不执行，把 tool_calls 原样返回给客户端 APP 执行
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

            # 后台保存对话 + 记忆提取（不阻塞流，断连也不影响）
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

        # 混合调用（客户端 + 网关）：给客户端工具占位结果，让循环继续执行网关工具
        for _cp in client_parsed:
            tool_results[_cp["id"]] = "[client_tool] 此工具由客户端执行，已延迟。"'''

if old2b not in code:
    print("patch_main.py: ERROR - cannot find insertion point 2b")
    exit(1)
code = code.replace(old2b, new2b, 1)


with open(MAIN_PY, "w", encoding="utf-8") as f:
    f.write(code)

print("patch_main.py: patch applied successfully")
