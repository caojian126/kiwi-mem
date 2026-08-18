#!/usr/bin/env python3
"""
补丁脚本 v5：合并客户端 tools + 修复每日整理 + 日页面回退读 conversations 表。
在 Docker 构建时自动执行（Dockerfile 里 RUN python patch_main.py）。

v5 变更：
1. 继承 v2-v4 全部修复
2. 新增：日页面生成在 chat_messages 表为空时回退读 conversations 表
   （不支持云端同步的 App 也能生成日页面）
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

        old2a = '        mcp_parsed = [p for p in parsed if tool_map.get(p["name"], {}).get("type") not in ("gateway_builtin", "drawer", "meta", "external_mcp")]'

        new2a = '''        client_parsed = [p for p in parsed if tool_map.get(p["name"], {}).get("type") == "client_passthrough"]
        mcp_parsed = [p for p in parsed if tool_map.get(p["name"], {}).get("type") not in ("gateway_builtin", "drawer", "meta", "external_mcp", "client_passthrough")]'''

        if old2a not in code:
            print("patch: ERROR - cannot find main.py insertion point 2a")
            exit(1)
        code = code.replace(old2a, new2a, 1)

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
# 补丁2：daily_digest.py — 修复 created_at + choices=None
# ============================================================

DIGEST_PY = "/app/daily_digest.py"

if os.path.exists(DIGEST_PY):
    with open(DIGEST_PY, "r", encoding="utf-8") as f:
        digest_code = f.read()

    # ---- 修复1：created_at 字符串 → datetime 对象 ----
    if "fix_created_at_str" not in digest_code:
        old3 = '''                f"{date_str}T00:00:00+08:00", cat_id'''

        new3 = '''                datetime.strptime(date_str + "T00:00:00+08:00", "%Y-%m-%dT%H:%M:%S%z"), cat_id  # fix_created_at_str'''

        if old3 in digest_code:
            digest_code = digest_code.replace(old3, new3, 1)
            print("patch: daily_digest.py created_at fix applied")
        else:
            print("patch: WARNING - cannot find daily_digest.py created_at insertion point")

    # ---- 修复2：choices=None 导致 'NoneType' object is not subscriptable ----
    if "fix_choices_none" not in digest_code:
        old4 = 'text = data.get("choices", [{}])[0].get("message", {}).get("content", "")'

        new4 = 'text = (data.get("choices") or [{}])[0].get("message", {}) if (data.get("choices") or [{}])[0] and isinstance((data.get("choices") or [{}])[0], dict) else {};\n            text = text.get("content", "") if isinstance(text, dict) else ""  # fix_choices_none'

        if old4 in digest_code:
            digest_code = digest_code.replace(old4, new4, 1)
            print("patch: daily_digest.py choices=None fix applied")
        else:
            print("patch: WARNING - cannot find daily_digest.py choices insertion point")

    with open(DIGEST_PY, "w", encoding="utf-8") as f:
        f.write(digest_code)
    print("patch: daily_digest.py patched successfully")
else:
    print("patch: WARNING - daily_digest.py not found, skipping")


# ============================================================
# 补丁3：database.py — 日页面回退读 conversations 表
# 不支持云端同步的 App 不会写 chat_messages 表，
# 日页面生成读不到数据就会一直跳过。
# 补丁：chat_messages 查询为空时，回退读 conversations 事件账本表。
# ============================================================

DB_PY = "/app/database.py"

if os.path.exists(DB_PY):
    with open(DB_PY, "r", encoding="utf-8") as f:
        db_code = f.read()

    if "fix_daypage_fallback" not in db_code:
        # 匹配 get_chat_messages_for_date 函数的 return 语句
        # 原代码在 async with 块外 return，回退查询需要重新获取连接
        old5 = '''              AND c.project_id IS NULL
            ORDER BY m.time ASC
        """, d)
    return [dict(r) for r in rows]'''

        new5 = '''              AND c.project_id IS NULL
            ORDER BY m.time ASC
        """, d)

    # fix_daypage_fallback: chat_messages 表为空时回退读 conversations 事件账本
    if not rows:
        async with pool.acquire() as conn2:
            rows = await conn2.fetch("""
                SELECT c.role, c.content, c.created_at AS time, c.session_id AS conversation_id,
                       COALESCE(r.rev, 0) AS source_rev,
                       (SELECT reset_generation FROM deletion_epoch WHERE id = 1) AS reset_generation
                FROM conversations c
                LEFT JOIN session_source_rev r ON r.session_id = c.session_id
                WHERE (c.created_at AT TIME ZONE 'Asia/Shanghai')::date = $1
                  AND c.role IN ('user', 'assistant')
                  AND c.content != ''
                  AND c.scope_known = TRUE AND c.project_id IS NULL
                ORDER BY c.created_at ASC
            """, d)
        if rows:
            print(f"   📋 chat_messages 为空，回退读 conversations 表：{len(rows)} 条")

    return [dict(r) for r in rows]'''

        if old5 in db_code:
            db_code = db_code.replace(old5, new5, 1)
            with open(DB_PY, "w", encoding="utf-8") as f:
                f.write(db_code)
            print("patch: database.py daypage fallback applied")
        else:
            print("patch: WARNING - cannot find database.py daypage fallback insertion point")
    else:
        print("patch: database.py already patched (daypage fallback), skipping")
else:
    print("patch: WARNING - database.py not found, skipping")

print("patch: all done")
