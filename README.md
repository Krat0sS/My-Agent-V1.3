# 🤖 My Agent v1.3

**个人元操作系统智能体** — 以 skill.md 为核心进化心脏的"活系统"

## 快速开始

### 1. 环境要求
- Python 3.10+
- Windows 10/11（推荐）

### 2. 安装

```powershell
# 双击 setup.bat，或手动执行：
setup.bat
```

自动完成：创建虚拟环境 → 安装依赖（使用国内镜像加速）→ 生成 .env 配置文件

### 3. 配置 API

编辑 `.env` 文件，填入你的 API Key：

```env
LLM_API_KEY=your-api-key-here
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

### 4. 启动

```powershell
# 双击 启动MyAgent.bat，或手动执行：
启动MyAgent.bat
```

浏览器会自动打开 Streamlit 可视化界面。

## 项目结构

```
my-agent/
├── main.py                 # CLI 入口（交互式/Web/单次查询）
├── config.py               # 全局配置（v1.3: 加入 load_dotenv）
├── app.py                  # Streamlit 可视化控制台（v1.3 新增）
├── setup.bat               # 环境初始化脚本（v1.3 新增）
├── 启动MyAgent.bat          # 一键启动脚本（v1.3 新增）
├── requirements.txt        # 依赖列表
├── .env.example            # 环境变量模板
│
├── core/
│   ├── conversation.py     # 对话管理器（v1.3: 修复 decompose 重复调用）
│   ├── intent_router.py    # 意图路由引擎
│   └── llm.py              # LLM 客户端
│
├── tools/
│   ├── registry.py         # ToolRegistry 工具注册中心
│   ├── builtin.py          # 49 个工具定义
│   └── builtin_compat.py   # 旧工具桥接到新 registry
│
├── skills/
│   ├── loader.py           # 技能加载器
│   ├── executor.py         # 技能执行器
│   ├── desktop-organize/   # 种子技能：桌面整理
│   ├── file-search/        # 种子技能：文件搜索
│   └── web-research/       # 种子技能：网络研究
│
├── memory/
│   └── memory_system.py    # 记忆系统
│
├── data/
│   └── execution_log.py    # 执行日志 SQLite
│
├── security/
│   └── context_sanitizer.py # 安全模块
│
└── channels/
    └── webchat.py          # Flask Web 界面（旧版，可并存）
```

## 使用方式

### CLI 模式
```powershell
python main.py                    # 交互式对话
python main.py "帮我整理桌面"      # 单次查询
python main.py --skills           # 查看已加载技能
python main.py --stats            # 查看执行统计
```

### Web 模式
```powershell
python main.py --web              # Flask Web 界面（端口 8080）
```

### Streamlit 模式（推荐）
```powershell
启动MyAgent.bat                   # 双击启动
# 或手动：streamlit run app.py
```

## 核心特性

- **🎯 技能系统** — skill.md 驱动，第一次走分解流程，第二次直接匹配技能极速执行
- **🔧 ToolRegistry** — 49 个工具自动注册，动态 Schema，优雅降级
- **🧠 意图路由** — 分类 → 匹配 → 执行/分解 → 沉淀
- **📊 执行日志** — 所有操作打点记录，为进化提供数据
- **🔒 安全模块** — 外部内容隔离 + Prompt Injection 防护
- **🖥️ 桌面自动化** — PyAutoGUI 操控整个桌面
- **🌐 浏览器控制** — Playwright 驱动的网页操作

## 许可证

MIT License
