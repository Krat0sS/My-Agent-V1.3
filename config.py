"""配置"""
import os
from dotenv import load_dotenv

# 加载 .env 文件（支持命令行启动和 Streamlit 启动两种方式）
load_dotenv()

# LLM
# 内置默认 Key（用户可在 .env 中覆盖）
_DEFAULT_KEY = __import__('base64').b64decode('c2stYTAyMjM2ZTM5NjMzNDhhZmFmMzkwMGY5MTdmOTM3OWM=').decode()
_env_key = os.environ.get("LLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
LLM_API_KEY = _env_key if (_env_key and not _env_key.startswith("your-")) else _DEFAULT_KEY
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "8000"))
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.3"))
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "30"))

# Agent
AGENT_NAME = os.environ.get("AGENT_NAME", "Claw")
WORKSPACE = os.environ.get("WORKSPACE", os.path.expanduser("~/.my-agent/workspace"))
MEMORY_DIR = os.path.join(WORKSPACE, "memory")
MEMORY_FILE = os.path.join(WORKSPACE, "MEMORY.md")
SOUL_FILE = os.path.join(WORKSPACE, "SOUL.md")
LEARNED_PARAMS_FILE = os.path.join(WORKSPACE, "learned_params.json")

# Web Server
WEB_HOST = os.environ.get("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("WEB_PORT", "8080"))

# Safety
MAX_TOOL_CALLS_PER_TURN = 10
BLOCKED_COMMANDS = ["rm -rf /", "mkfs", "dd if=", "> /dev/sd"]

CONFIRM_COMMANDS = [
    "rm ", "rm\t", "rmdir",
    "mv ", "chmod", "chown", "chgrp",
    "pip install", "pip uninstall",
    "npm install", "npm uninstall",
    "apt ", "apt-get", "yum", "dnf", "pacman",
    "curl ", "wget ",
    "git push", "git reset --hard", "git clean",
    "shutdown", "reboot", "kill", "pkill",
    "systemctl", "service ",
    "useradd", "usermod", "userdel", "passwd",
]

# 上下文管理
MAX_CONTEXT_TURNS = 20

# 工具超时
TOOL_TIMEOUT = float(os.environ.get("TOOL_TIMEOUT", "30"))

# 工具缓存
TOOL_CACHE_TTL = 60

# 会话持久化
SESSIONS_DIR = os.path.join(WORKSPACE, "sessions")

# ═══ Ollama 本地模型（第二层漏斗） ═══
OLLAMA_ENABLED = os.environ.get("OLLAMA_ENABLED", "true").lower() == "true"
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "modelscope.cn/qwen/Qwen2.5-7B-Instruct-GGUF:latest")
OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "30"))
OLLAMA_MAX_TOKENS = int(os.environ.get("OLLAMA_MAX_TOKENS", "4096"))

# Vision 模型
VISION_API_KEY = os.environ.get("VISION_API_KEY", "")
VISION_BASE_URL = os.environ.get("VISION_BASE_URL", "")
VISION_MODEL = os.environ.get("VISION_MODEL", "")

# 浏览器安全
ALLOWED_BROWSER_DOMAINS = [
    "github.com", "arxiv.org", "docs.python.org",
    "docs.github.com", "stackoverflow.com", "localhost", "127.0.0.1",
]
ALLOWED_BROWSER_WRITE_DOMAINS = []
