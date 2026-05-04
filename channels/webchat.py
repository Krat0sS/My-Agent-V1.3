"""Web 服务器 — WebChat 界面 + REST API"""
import json
import uuid
import asyncio
from flask import Flask, request, jsonify, render_template_string
from core.conversation import ConversationManager
import config

app = Flask(__name__)
manager = ConversationManager()

# 全局事件循环（用于在 Flask 同步路由中调用 async 代码）
_loop = None

def get_loop():
    """获取或创建事件循环"""
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop

# ═══ WebChat 前端 ═══
WEBCHAT_HTML = """
<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ agent_name }}</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1a1a2e; color: #eee; height: 100vh; display: flex; flex-direction: column; }

        /* 顶部导航 */
        .header { background: #16213e; padding: 12px 20px; display: flex; align-items: center; gap: 10px; border-bottom: 1px solid #0f3460; }
        .header h1 { font-size: 18px; color: #e94560; }
        .header .status { font-size: 12px; color: #4ecca3; }

        /* 首页 */
        .landing { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 40px 20px; }
        .landing h2 { font-size: 28px; color: #e94560; margin-bottom: 8px; }
        .landing .subtitle { color: #888; font-size: 14px; margin-bottom: 32px; }
        .capabilities { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; max-width: 700px; width: 100%; margin-bottom: 32px; }
        .cap-card { background: #16213e; border: 1px solid #0f3460; border-radius: 12px; padding: 16px; transition: border-color 0.2s; }
        .cap-card:hover { border-color: #e94560; }
        .cap-card .icon { font-size: 24px; margin-bottom: 8px; }
        .cap-card h3 { font-size: 14px; color: #4ecca3; margin-bottom: 4px; }
        .cap-card p { font-size: 12px; color: #888; line-height: 1.5; }
        .start-btn { background: #e94560; color: white; border: none; border-radius: 10px; padding: 12px 32px; font-size: 16px; cursor: pointer; transition: background 0.2s; }
        .start-btn:hover { background: #c73e54; }

        /* 聊天界面（默认隐藏） */
        .chat-view { display: none; flex: 1; flex-direction: column; height: 100%; }
        .chat-view.active { display: flex; }
        .landing.hidden { display: none; }

        .messages { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 12px; }
        .msg { max-width: 80%; padding: 10px 14px; border-radius: 12px; line-height: 1.5; font-size: 14px; white-space: pre-wrap; word-break: break-word; }
        .msg.user { align-self: flex-end; background: #0f3460; border-bottom-right-radius: 4px; }
        .msg.assistant { align-self: flex-start; background: #16213e; border-bottom-left-radius: 4px; border: 1px solid #0f3460; }
        .msg.tool { align-self: flex-start; background: #1a1a2e; color: #4ecca3; font-size: 12px; border: 1px dashed #333; padding: 6px 10px; }
        .msg.stats { align-self: flex-start; background: #1a1a2e; color: #888; font-size: 11px; border: 1px dashed #2a2a4a; padding: 4px 10px; }
        .msg pre { background: #0d1117; padding: 8px; border-radius: 6px; overflow-x: auto; margin: 6px 0; font-size: 13px; }
        .msg code { font-family: 'Fira Code', monospace; }
        .input-area { background: #16213e; padding: 12px 20px; border-top: 1px solid #0f3460; display: flex; gap: 10px; }
        .input-area textarea { flex: 1; background: #1a1a2e; color: #eee; border: 1px solid #0f3460; border-radius: 8px; padding: 10px; font-size: 14px; resize: none; height: 44px; font-family: inherit; }
        .input-area textarea:focus { outline: none; border-color: #e94560; }
        .input-area button { background: #e94560; color: white; border: none; border-radius: 8px; padding: 0 20px; font-size: 14px; cursor: pointer; }
        .input-area button:hover { background: #c73e54; }
        .input-area button:disabled { background: #333; cursor: not-allowed; }
        .input-area .cancel-btn { background: #f39c12; display: none; }
        .input-area .cancel-btn:hover { background: #e67e22; }
        .thinking { color: #4ecca3; font-style: italic; padding: 10px 14px; }
        .back-btn { background: none; border: none; color: #888; cursor: pointer; font-size: 13px; padding: 4px 8px; }
        .back-btn:hover { color: #eee; }
        @media (max-width: 600px) { .msg { max-width: 95%; } .capabilities { grid-template-columns: 1fr; } }
    </style>
</head>
<body>
    <div class="header">
        <button class="back-btn" id="backBtn" onclick="showLanding()" style="display:none">← 返回</button>
        <h1>🤖 {{ agent_name }}</h1>
        <span class="status">● Online</span>
    </div>

    <!-- 首页：能力展示 -->
    <div class="landing" id="landing">
        <h2>{{ agent_name }}</h2>
        <p class="subtitle">可自定义的 AI 智能体 — 为你而生</p>
        <div class="capabilities">
            <div class="cap-card">
                <div class="icon">📁</div>
                <h3>文件操作</h3>
                <p>查找、阅读、编辑、创建文件。支持精确查找替换。</p>
            </div>
            <div class="cap-card">
                <div class="icon">💻</div>
                <h3>命令执行</h3>
                <p>运行脚本、编译代码、系统管理。内置安全确认机制。</p>
            </div>
            <div class="cap-card">
                <div class="icon">🌐</div>
                <h3>网页浏览</h3>
                <p>打开网页、获取内容、截图分析。支持 GitHub、文档站等。</p>
            </div>
            <div class="cap-card">
                <div class="icon">🧠</div>
                <h3>长期记忆</h3>
                <p>记住你的偏好和重要信息。跨会话持久化，语义检索。</p>
            </div>
            <div class="cap-card">
                <div class="icon">🔧</div>
                <h3>编程辅助</h3>
                <p>阅读代码、解释错误、重构优化。支持多语言。</p>
            </div>
            <div class="cap-card">
                <div class="icon">🖥️</div>
                <h3>桌面操控</h3>
                <p>窗口管理、鼠标点击、键盘输入、屏幕截图。操控整个桌面环境。</p>
            </div>
            <div class="cap-card">
                <div class="icon">🎯</div>
                <h3>自适应人格</h3>
                <p>通过 SOUL.md 定义性格。可学习你的偏好，越用越懂你。</p>
            </div>
        </div>
        <button class="start-btn" onclick="startChat()">开始对话 →</button>
    </div>

    <!-- 聊天界面 -->
    <div class="chat-view" id="chatView">
        <div class="messages" id="messages"></div>
        <div class="input-area">
            <textarea id="input" placeholder="输入消息..." rows="1" onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();send()}"></textarea>
            <button onclick="send()" id="btn">发送</button>
            <button class="cancel-btn" onclick="cancelOp()" id="cancelBtn">取消</button>
        </div>
    </div>

    <script>
        const msgs = document.getElementById('messages');
        const input = document.getElementById('input');
        const btn = document.getElementById('btn');
        const landing = document.getElementById('landing');
        const chatView = document.getElementById('chatView');
        const backBtn = document.getElementById('backBtn');
        let sessionId = localStorage.getItem('sessionId') || crypto.randomUUID();
        localStorage.setItem('sessionId', sessionId);

        function startChat() {
            landing.classList.add('hidden');
            chatView.classList.add('active');
            backBtn.style.display = 'inline';
            input.focus();
        }

        function showLanding() {
            landing.classList.remove('hidden');
            chatView.classList.remove('active');
            backBtn.style.display = 'none';
        }

        function addMsg(role, content) {
            const div = document.createElement('div');
            div.className = 'msg ' + role;
            div.textContent = content;
            msgs.appendChild(div);
            msgs.scrollTop = msgs.scrollHeight;
        }

        function addToolCalls(toolCalls) {
            if (!toolCalls || !toolCalls.length) return;
            for (const tc of toolCalls) {
                const div = document.createElement('div');
                div.className = 'msg tool';
                const status = tc.error ? '❌' : '✅';
                const cached = (tc.result_preview || '').includes('_cached') ? ' 📦缓存' : '';
                const args = Object.entries(tc.args || {}).map(([k,v]) => `${k}=${v}`).join(', ');
                div.textContent = `${status} 🛠️ ${tc.tool}(${args.slice(0,50)}) — ${tc.elapsed_ms}ms${cached}`;
                msgs.appendChild(div);
            }
            msgs.scrollTop = msgs.scrollHeight;
        }

        function addStats(stats) {
            const div = document.createElement('div');
            div.className = 'msg stats';
            const cost = stats.estimated_cost_cny > 0 ? ` | ≈ ¥${stats.estimated_cost_cny}` : '';
            div.textContent = `📊 tokens: ${stats.total_tokens} (prompt ${stats.prompt_tokens} + completion ${stats.completion_tokens}) | 工具: ${stats.tool_calls_count} 次 | 轮次: ${stats.rounds}${cost}`;
            msgs.appendChild(div);
        }

        async function send() {
            const text = input.value.trim();
            if (!text) return;
            input.value = '';
            addMsg('user', text);
            btn.disabled = true;
            cancelBtn.style.display = 'inline';
            const think = document.createElement('div');
            think.className = 'thinking';
            think.textContent = '思考中...';
            msgs.appendChild(think);
            msgs.scrollTop = msgs.scrollHeight;

            try {
                const resp = await fetch('/api/chat', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({message: text, session_id: sessionId})
                });
                const data = await resp.json();
                think.remove();

                // 检查是否需要确认
                if (data.needs_confirm && data.commands) {
                    for (const cmd of data.commands) {
                        const confirmed = confirm(`⚠️ 该命令可能修改系统状态，是否确认执行？\n\n$ ${cmd}`);
                        if (confirmed) {
                            // 发送确认请求
                            const confirmResp = await fetch('/api/chat', {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({confirm_command: cmd, session_id: sessionId})
                            });
                            const confirmData = await confirmResp.json();
                            addToolCalls(confirmData.tool_calls);
                            addMsg('assistant', confirmData.reply || '命令已执行');
                        } else {
                            addMsg('assistant', '已取消命令执行。');
                        }
                    }
                } else {
                    addToolCalls(data.tool_calls);
                    addMsg('assistant', data.reply);
                    if (data.stats && data.stats.total_tokens > 0) {
                        addStats(data.stats);
                    }
                }
            } catch(e) {
                think.remove();
                addMsg('assistant', '错误: ' + e.message);
            }
            btn.disabled = false;
            cancelBtn.style.display = 'none';
            input.focus();
        }

        async function cancelOp() {
            try {
                await fetch('/api/cancel', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({session_id: sessionId})
                });
                addMsg('assistant', '⏳ 已发送取消信号...');
            } catch(e) {}
        }
    </script>
</body>
</html>
"""

# ═══ API 路由 ═══

@app.route("/")
def index():
    return render_template_string(WEBCHAT_HTML, agent_name=config.AGENT_NAME)


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """对话 API — 异步调用，返回回复 + 工具调用日志 + token 统计"""
    data = request.json or {}
    message = data.get("message", "").strip()
    session_id = data.get("session_id", "default")
    confirm_command = data.get("confirm_command")

    if not message and not confirm_command:
        return jsonify({"error": "消息不能为空"}), 400

    conv = manager.get_or_create(session_id)
    prev_log_len = len(conv.tool_log)

    pending_commands = []

    def web_on_confirm(cmd: str) -> bool:
        pending_commands.append(cmd)
        return False

    loop = get_loop()

    # 确认后回传，直接执行
    if confirm_command:
        from tools import builtin as tools
        result = tools.execute("run_command_confirmed", {"command": confirm_command})
        conv.messages.append({"role": "tool", "content": result})
        new_tool_calls = conv.tool_log[prev_log_len:]
        return jsonify({
            "reply": f"已执行命令: {confirm_command}\n{result}",
            "session_id": session_id,
            "tool_calls": new_tool_calls if new_tool_calls else None,
        })

    # 异步调用 conversation.send
    try:
        result = loop.run_until_complete(
            conv.send(message, on_confirm=web_on_confirm)
        )
    except Exception as e:
        return jsonify({"error": f"调用失败: {str(e)}"}), 500

    # 如果有待确认的命令
    if pending_commands:
        return jsonify({
            "needs_confirm": True,
            "commands": pending_commands,
            "pending_reply": result.get("response", ""),
            "session_id": session_id,
        })

    new_tool_calls = conv.tool_log[prev_log_len:]
    return jsonify({
        "reply": result.get("response", ""),
        "session_id": session_id,
        "tool_calls": new_tool_calls if new_tool_calls else None,
        "stats": result.get("stats", {}),
    })


@app.route("/api/history", methods=["GET"])
def api_history():
    """获取对话历史"""
    session_id = request.args.get("session_id", "default")
    conv = manager.get_or_create(session_id)
    return jsonify({"history": conv.get_history()})


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """重置对话"""
    data = request.json or {}
    session_id = data.get("session_id", "default")
    conv = manager.get_or_create(session_id)
    conv.reset()
    return jsonify({"status": "reset"})


@app.route("/api/stats", methods=["GET"])
def api_stats():
    """会话统计"""
    session_id = request.args.get("session_id", "default")
    conv = manager.get_or_create(session_id)
    total_tools = len(conv.tool_log)
    errors = sum(1 for e in conv.tool_log if e.get("error"))
    total_time = sum(e.get("elapsed_ms", 0) for e in conv.tool_log)
    msg_count = len([m for m in conv.messages if m["role"] != "system"])
    browser_status = "unknown"
    if conv._browser_session:
        browser_status = "healthy" if conv._browser_session._healthy else "disconnected"
    return jsonify({
        "session_id": session_id,
        "messages": msg_count,
        "tool_calls": total_tools,
        "tool_errors": errors,
        "tool_total_ms": total_time,
        "browser": browser_status,
    })


@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    """取消当前操作"""
    data = request.json or {}
    session_id = data.get("session_id", "default")
    conv = manager.get_or_create(session_id)
    conv.cancel()
    return jsonify({"status": "cancelled", "session_id": session_id})


@app.route("/api/tools", methods=["GET"])
def api_tools():
    """列出可用工具"""
    from tools import builtin as tools
    return jsonify({"tools": tools.list_tools()})


@app.route("/api/tool-log", methods=["GET"])
def api_tool_log():
    """获取工具调用日志"""
    session_id = request.args.get("session_id", "default")
    conv = manager.get_or_create(session_id)
    return jsonify({"log": conv.get_tool_log()})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "agent": config.AGENT_NAME})
