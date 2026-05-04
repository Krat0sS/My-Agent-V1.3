"""
My Agent v1.3.1 — Streamlit 控制台
"""
import streamlit as st
import asyncio
import os
import sys
import time
import importlib
from pathlib import Path
from dotenv import load_dotenv, set_key

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv()

def async_run(coro):
    import concurrent.futures
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            with concurrent.futures.ThreadPoolExecutor() as executor:
                return executor.submit(asyncio.run, coro).result(timeout=120)
    except RuntimeError:
        return asyncio.run(coro)

@st.cache_resource
def init_agent_cached():
    import config
    from tools.registry import registry
    import tools.builtin_compat
    from skills.loader import load_all_skills
    skills = load_all_skills()
    return registry, skills

async def run_agent(message, api_key, api_base, model, session_id="gui"):
    os.environ["LLM_API_KEY"] = api_key
    os.environ["LLM_BASE_URL"] = api_base
    os.environ["LLM_MODEL"] = model
    import config
    importlib.reload(config)
    import core.llm as llm_module
    llm_module._client = None
    from core.conversation import Conversation
    conv = Conversation(session_id=session_id, restore=False)
    progress_log = []
    def on_progress(msg):
        progress_log.append(msg)
    def on_confirm(cmd):
        return False
    result = await conv.send(message, on_confirm=on_confirm, on_progress=on_progress)
    response = result.get("response", "Agent 没有返回回复。")
    tool_calls = result.get("tool_calls", [])
    stats = result.get("stats", {})
    if progress_log:
        response = "\n".join(f"  {p}" for p in progress_log) + "\n\n" + response
    await conv.cleanup()
    return response, tool_calls, stats

# ── 页面配置 ──
st.set_page_config(page_title="My Agent", page_icon="🤖", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    
    * { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }
    
    .stApp {
        background: linear-gradient(135deg, #0f0f23 0%, #1a1a3e 50%, #0f0f23 100%);
    }
    
    div[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #12122a 0%, #1e1e3f 100%);
        border-right: 1px solid rgba(99, 102, 241, 0.15);
    }
    
    div[data-testid="stSidebar"] .stMarkdown h1 {
        background: linear-gradient(90deg, #818cf8, #c084fc);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 1.6rem !important;
        font-weight: 700 !important;
    }
    
    div[data-testid="stSidebar"] .stMarkdown h3 {
        color: #a5b4fc !important;
        font-size: 0.95rem !important;
        font-weight: 600 !important;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    
    div[data-testid="stSidebar"] .stMarkdown p {
        color: #94a3b8;
    }
    
    .stChatMessage {
        border-radius: 16px !important;
        padding: 16px 20px !important;
        margin: 8px 0 !important;
        border: 1px solid rgba(99, 102, 241, 0.1) !important;
        box-shadow: 0 2px 8px rgba(0,0,0,0.2) !important;
    }
    
    div[data-testid="stChatMessage"][data-testid-type="user"] {
        background: linear-gradient(135deg, #1e1b4b 0%, #312e81 100%) !important;
        border-color: rgba(129, 140, 248, 0.2) !important;
    }
    
    div[data-testid="stChatMessage"][data-testid-type="assistant"] {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%) !important;
        border-color: rgba(99, 102, 241, 0.1) !important;
    }
    
    .stChatInput > div {
        background: #1e1e3f !important;
        border: 1px solid rgba(99, 102, 241, 0.2) !important;
        border-radius: 12px !important;
    }
    
    .stChatInput textarea {
        color: #e2e8f0 !important;
    }
    
    .stButton > button {
        background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%) !important;
        color: white !important;
        border: none !important;
        border-radius: 10px !important;
        font-weight: 600 !important;
        transition: all 0.2s ease !important;
        box-shadow: 0 2px 8px rgba(99, 102, 241, 0.3) !important;
    }
    
    .stButton > button:hover {
        transform: translateY(-1px) !important;
        box-shadow: 0 4px 16px rgba(99, 102, 241, 0.4) !important;
    }
    
    div[data-testid="stSidebar"] .stButton > button {
        background: linear-gradient(135deg, #374151 0%, #4b5563 100%) !important;
        box-shadow: none !important;
        font-size: 0.85rem !important;
        padding: 6px 12px !important;
    }
    
    div[data-testid="stSidebar"] .stButton > button:hover {
        background: linear-gradient(135deg, #4b5563 0%, #6b7280 100%) !important;
    }
    
    .stExpander {
        background: rgba(30, 30, 63, 0.5) !important;
        border: 1px solid rgba(99, 102, 241, 0.1) !important;
        border-radius: 12px !important;
    }
    
    div[data-testid="stSidebar"] .stExpander {
        background: rgba(18, 18, 42, 0.5) !important;
        border: 1px solid rgba(99, 102, 241, 0.08) !important;
    }
    
    .stSpinner > div {
        border-top-color: #818cf8 !important;
    }
    
    div[data-testid="stMetric"] {
        background: rgba(30, 30, 63, 0.5);
        border: 1px solid rgba(99, 102, 241, 0.1);
        border-radius: 12px;
        padding: 12px;
    }
    
    div[data-testid="stMetric"] label {
        color: #94a3b8 !important;
    }
    
    div[data-testid="stMetric"] [data-testid="stMetricValue"] {
        color: #c084fc !important;
    }
    
    .stCaption {
        color: #64748b !important;
    }
    
    h1, h2, h3 { color: #e2e8f0 !important; }
    
    div[data-testid="stSidebar"] hr {
        border-color: rgba(99, 102, 241, 0.1) !important;
    }
    
    .block-container {
        padding-top: 2rem !important;
    }
    
    div[data-testid="stSidebar"] [data-testid="stTextInput"] input {
        background: #12122a !important;
        border: 1px solid rgba(99, 102, 241, 0.15) !important;
        border-radius: 8px !important;
        color: #e2e8f0 !important;
    }
    
    div[data-testid="stSidebar"] [data-testid="stSelectbox"] > div > div {
        background: #12122a !important;
        border: 1px solid rgba(99, 102, 241, 0.15) !important;
        border-radius: 8px !important;
        color: #e2e8f0 !important;
    }
    
    .stAlert {
        border-radius: 10px !important;
    }
</style>
""", unsafe_allow_html=True)

# ── Session State ──
if "messages" not in st.session_state:
    st.session_state.messages = []

try:
    registry, skills = init_agent_cached()
    agent_ready = True
except Exception as e:
    registry, skills = None, []
    agent_ready = False

# ── 侧边栏 ──
with st.sidebar:
    st.markdown("# 🤖 My Agent")
    st.caption("Personal Meta-OS Agent v1.3.1")
    
    st.markdown("---")
    
    st.markdown("### ⚙️ API 配置")
    
    saved_key = os.getenv("LLM_API_KEY", "")
    saved_base = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
    saved_model = os.getenv("LLM_MODEL", "deepseek-chat")
    
    api_key = st.text_input("API Key", type="password", value=saved_key, placeholder="sk-...")
    api_base = st.text_input("API 地址", value=saved_base)
    
    model_options = ["deepseek-chat", "deepseek-v4-pro", "deepseek-reasoner", "gpt-4o", "gpt-4o-mini"]
    model_index = model_options.index(saved_model) if saved_model in model_options else 0
    model = st.selectbox("模型", model_options, index=model_index)
    
    if api_key != saved_key or api_base != saved_base or model != saved_model:
        env_path = Path(".env")
        if not env_path.exists():
            env_path.touch()
        if api_key:
            set_key(str(env_path), "LLM_API_KEY", api_key)
        set_key(str(env_path), "LLM_BASE_URL", api_base)
        set_key(str(env_path), "LLM_MODEL", model)
        os.environ["LLM_API_KEY"] = api_key
        os.environ["LLM_BASE_URL"] = api_base
        os.environ["LLM_MODEL"] = model
    
    if api_key:
        st.success(f"✅ {model}")
    else:
        st.warning("⚠️ 请输入 API Key")
    
    st.markdown("---")
    
    st.markdown("### 🔧 工具")
    if registry:
        categories = registry.list_by_category()
        for category, tool_list in categories.items():
            with st.expander(f"📁 {category} ({len(tool_list)})", expanded=False):
                for tool_name in tool_list:
                    td = registry.get(tool_name)
                    if td:
                        avail = "🟢" if td.is_available() else "⚪"
                        desc = td.description[:45] + "..." if len(td.description) > 45 else td.description
                        st.markdown(f"{avail} `{tool_name}` — {desc}")
        st.caption(f"{registry.available_count()}/{registry.count()} 可用")
    
    st.markdown("---")
    
    st.markdown("### 🎯 技能")
    if skills:
        for skill in skills:
            st.markdown(f"• **{skill.name}** — {skill.goal[:40]}")
    else:
        st.caption("暂无技能")
    
    st.markdown("---")
    
    if st.button("📊 执行统计", use_container_width=True):
        try:
            from data.execution_log import get_skill_stats, get_recent_tasks
            ss = get_skill_stats()
            if ss:
                for s in ss:
                    rate = (s['successes'] / s['uses'] * 100) if s['uses'] > 0 else 0
                    st.markdown(f"• {s['skill_name']}: {s['uses']}次, {rate:.0f}%")
            rt = get_recent_tasks(3)
            if rt:
                for t in rt:
                    st.caption(f"{'✅' if t.get('success') else '❌'} {t['user_input'][:30]}")
        except:
            pass

# ── 主区域 ──
st.markdown("## 💬 对话")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("tool_calls"):
            with st.expander(f"⚙️ 工具 ({len(msg['tool_calls'])} 次)", expanded=False):
                for tc in msg["tool_calls"]:
                    st.markdown(f"{'❌' if tc.get('error') else '✅'} `{tc.get('tool', '?')}` — {tc.get('elapsed_ms', 0)}ms")
        if msg.get("stats") and msg["stats"].get("total_tokens", 0) > 0:
            s = msg["stats"]
            st.caption(f"📊 {s.get('total_tokens', 0)} tokens · {s.get('tool_calls_count', 0)} 次工具 · {s.get('rounds', 0)} 轮 · ≈ ¥{s.get('estimated_cost_cny', 0)}")

if prompt := st.chat_input("说点什么..."):
    current_key = os.getenv("LLM_API_KEY", "")
    if not current_key:
        st.error("请先在左侧填入 API Key")
        st.stop()
    
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    
    with st.chat_message("assistant"):
        with st.spinner("思考中..."):
            start_time = time.time()
            try:
                response, tool_calls, stats = async_run(run_agent(
                    prompt,
                    api_key=os.getenv("LLM_API_KEY", ""),
                    api_base=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
                    model=os.getenv("LLM_MODEL", "deepseek-chat"),
                ))
                elapsed = time.time() - start_time
                response += f"\n\n⏱️ {elapsed:.1f}s"
            except Exception as e:
                response = f"❌ {str(e)}"
                tool_calls, stats = [], {}
        st.markdown(response)
        if tool_calls:
            with st.expander(f"⚙️ 工具 ({len(tool_calls)} 次)", expanded=False):
                for tc in tool_calls:
                    st.markdown(f"{'❌' if tc.get('error') else '✅'} `{tc.get('tool', '?')}` — {tc.get('elapsed_ms', 0)}ms")
        if stats and stats.get("total_tokens", 0) > 0:
            st.caption(f"📊 {stats.get('total_tokens', 0)} tokens · ≈ ¥{stats.get('estimated_cost_cny', 0)}")
    
    st.session_state.messages.append({"role": "assistant", "content": response, "tool_calls": tool_calls, "stats": stats})

col1, col2, col3 = st.columns([1, 1, 2])
with col1:
    if st.button("🗑️ 清空", use_container_width=True):
        st.session_state.messages = []
        st.rerun()
with col2:
    if st.button("🔄 重置", use_container_width=True):
        import config
        sf = os.path.join(config.SESSIONS_DIR, "gui.json")
        if os.path.exists(sf):
            os.remove(sf)
        st.session_state.messages = []
        st.rerun()
with col3:
    st.caption(f"🔧 {registry.available_count() if registry else 0} 工具 · 🎯 {len(skills)} 技能")
