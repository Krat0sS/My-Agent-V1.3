"""
My Agent — 轻量 Web 服务
为 index.html 提供 API 接口
"""
import os
import sys
import json
import asyncio
import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder='.', static_url_path='')

# ═══ 延迟初始化 Agent ═══
_agent_initialized = False
_registry = None
_skills = []

def init_agent():
    global _agent_initialized, _registry, _skills
    if _agent_initialized:
        return
    import config
    from tools.registry import registry
    import tools.builtin_compat
    from skills.loader import load_all_skills
    _registry = registry
    _skills = load_all_skills()
    _agent_initialized = True


# ═══ 页面路由 ═══

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


# ═══ API 路由 ═══

@app.route('/api/status')
def api_status():
    """获取 Agent 状态"""
    init_agent()
    tools_list = []
    if _registry:
        for td in _registry.get_available():
            tools_list.append({
                'name': td.name,
                'desc': td.description[:60],
                'category': td.category,
            })

    skills_list = []
    for s in _skills:
        skills_list.append({
            'name': s.name,
            'goal': s.goal[:60],
            'tools': s.tools,
            'steps': len(s.steps),
        })

    return jsonify({
        'status': 'ok',
        'tools': tools_list,
        'skills': skills_list,
        'tool_count': len(tools_list),
        'skill_count': len(skills_list),
    })


@app.route('/api/chat', methods=['POST'])
def api_chat():
    """对话接口"""
    init_agent()
    data = request.get_json(force=True)
    message = data.get('message', '').strip()
    session_id = data.get('session_id', 'web-default')

    if not message:
        return jsonify({'error': '消息不能为空'}), 400

    import config
    from core.conversation import Conversation

    try:
        conv = Conversation(session_id=session_id, restore=True)
        progress_log = []

        def on_progress(msg):
            progress_log.append(msg)

        def on_confirm(cmd):
            # Web 模式下默认拒绝高风险操作
            return False

        result = asyncio.run(conv.send(
            message,
            on_confirm=on_confirm,
            on_progress=on_progress,
        ))

        response = result.get('response', '(无回复)')
        tool_calls = result.get('tool_calls', [])
        stats = result.get('stats', {})

        # 附加进度日志
        result_data = {
            'response': response,
            'tool_calls': tool_calls,
            'stats': stats,
            '_progress': progress_log,
        }

        asyncio.run(conv.cleanup())
        return jsonify(result_data)

    except Exception as e:
        return jsonify({
            'response': f'❌ Agent 执行出错: {str(e)}',
            'tool_calls': [],
            'stats': {},
            '_progress': [],
        }), 500


@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'time': datetime.datetime.now().isoformat()})


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--host', default='0.0.0.0')
    args = parser.parse_args()

    print(f'\n🤖 My Agent v1.3.1 Web 服务启动')
    print(f'   地址: http://localhost:{args.port}')
    print(f'   API:  http://localhost:{args.port}/api/chat')
    print(f'   健康: http://localhost:{args.port}/api/health')
    print()

    app.run(host=args.host, port=args.port, debug=False)
