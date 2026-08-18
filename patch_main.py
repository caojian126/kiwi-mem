#!/usr/bin/env python3
"""
补丁脚本：让 kiwi-mem 合并客户端 tools 而不是替换。
在 Docker 构建时自动执行（Dockerfile 里 RUN python patch_main.py）。
"""
import re

MAIN_PY = "/app/main.py"

with open(MAIN_PY, "r", encoding="utf-8") as f:
    code = f.read()

# ---- 检查是否已经打过补丁 ----
if "client_passthrough" in code:
    print("patch_main.py: already patched, skipping")
    exit(0)

# ---- 编辑1：在 Tool Call 模式之前，合并客户端 tools ----
old1 = '    # ========== Tool Call 模式（MCP 和/或 auto 搜索） ==========
    if openai_tools and is_stream:'

new1 = '''    # ========== 合并客户端原始 tools ==========
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

# ---- 编辑2：在 _stream_with_tools 中处理客户端工具调用 ----
old2 = '        mcp_parsed = [p for p in parsed if tool_map.get(p["name"], {}).get("type") not in ("gateway_builtin", "drawer", "meta", "external_mcp")]'

new2 = '''        client_parsed = [p for p in parsed if tool_map.get(p["name"], {}).get("type") == "client_passthrough"]
        mcp_parsed = [p for p in parsed if tool_map.get(p["name"], {}).get("type") not in ("gateway_builtin", "drawer", "meta", "external_mcp", "client_passthrough")]

        # 客户端工具调用：不执行，把 tool_calls 原样返回给客户端
        if client_parsed:
            print(f"📤 客户端工具调用：{[p[\'name\'] for p in client_parsed]}，返回给客户端执行")
            content = message.get("content") or ""
            if content:
                yield f"data: {json.dumps({\'choices\': [{\'delta\': {\'content\': content}, \'finish_reason\': None}], \'model\': model}, ensure_ascii=False)}\\n\\n"
            for tc in tool_calls:
                tc_name = tc.get("function", {}).get("name", "")
                if tool_map.get(tc_name, {}).get("type") == "client_passthrough":
                    yield f"data: {json.dumps({\'choices\': [{\'delta\': {\'tool_calls\': [tc]}, \'finish_reason\': None}], \'model\': model}, ensure_ascii=False)}\\n\\n"
            yield f"data: {json.dumps({\'choices\': [{\'delta\': {}, \'finish_reason\': \'tool_calls\'}], \'model\': model}, ensure_ascii=False)}\\n\\n"
            yield "data: [DONE]\\n\\n"
            return'''

if old2 not in code:
    print("patch_main.py: ERROR - cannot find insertion point 2")
    exit(1)
code = code.replace(old2, new2, 1)

with open(MAIN_PY, "w", encoding="utf-8") as f:
    f.write(code)

print("patch_main.py: patch applied successfully")
