"""
执行日志 — 记录每次工具调用和任务执行，为路由进化提供数据

所有操作从第一天起就被有结构地记录。
这是"越用越准"的数据基础。
"""
import sqlite3
import json
import os
import time
import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
import config


DB_PATH = os.path.join(config.WORKSPACE, "data", "execution_log.db")


def _ensure_dir():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def _get_conn() -> sqlite3.Connection:
    _ensure_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库表（幂等，可重复调用）"""
    conn = _get_conn()
    conn.executescript("""
        -- 工具调用日志
        CREATE TABLE IF NOT EXISTS tool_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            session_id TEXT,
            tool_name TEXT NOT NULL,
            args_json TEXT,
            result_preview TEXT,
            success INTEGER DEFAULT 1,
            elapsed_ms INTEGER DEFAULT 0,
            error_message TEXT
        );

        -- 任务执行日志
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            session_id TEXT,
            user_input TEXT NOT NULL,
            matched_skill TEXT,
            match_score REAL,
            plan_json TEXT,
            actual_steps_json TEXT,
            success INTEGER DEFAULT 1,
            user_feedback TEXT,
            token_cost INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0
        );

        -- 技能使用统计
        CREATE TABLE IF NOT EXISTS skill_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            skill_name TEXT NOT NULL,
            user_input TEXT,
            success INTEGER DEFAULT 1,
            duration_ms INTEGER DEFAULT 0,
            token_cost INTEGER DEFAULT 0
        );

        -- 路由决策日志（匹配器返回的候选列表）
        CREATE TABLE IF NOT EXISTS routing_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            user_input TEXT NOT NULL,
            candidates_json TEXT,
            chosen_skill TEXT,
            chosen_score REAL,
            fallback_to_decompose INTEGER DEFAULT 0
        );

        -- 创建索引
        CREATE INDEX IF NOT EXISTS idx_tool_calls_tool ON tool_calls(tool_name);
        CREATE INDEX IF NOT EXISTS idx_tool_calls_time ON tool_calls(timestamp);
        CREATE INDEX IF NOT EXISTS idx_tasks_time ON tasks(timestamp);
        CREATE INDEX IF NOT EXISTS idx_skill_usage_name ON skill_usage(skill_name);
    """)
    conn.commit()
    conn.close()


def log_tool_call(tool_name: str, args: dict = None, result: str = "",
                  success: bool = True, elapsed_ms: int = 0,
                  error_message: str = "", session_id: str = ""):
    """记录一次工具调用"""
    conn = _get_conn()
    conn.execute(
        """INSERT INTO tool_calls
           (session_id, tool_name, args_json, result_preview, success, elapsed_ms, error_message)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (session_id, tool_name,
         json.dumps(args or {}, ensure_ascii=False)[:2000],
         result[:500] if result else "",
         1 if success else 0,
         elapsed_ms, error_message)
    )
    conn.commit()
    conn.close()


def log_task(user_input: str, matched_skill: str = None, match_score: float = None,
             plan_json: str = None, actual_steps_json: str = None,
             success: bool = True, token_cost: int = 0, duration_ms: int = 0,
             session_id: str = ""):
    """记录一次任务执行"""
    conn = _get_conn()
    conn.execute(
        """INSERT INTO tasks
           (session_id, user_input, matched_skill, match_score, plan_json,
            actual_steps_json, success, token_cost, duration_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, user_input, matched_skill, match_score,
         plan_json, actual_steps_json,
         1 if success else 0, token_cost, duration_ms)
    )
    conn.commit()
    conn.close()


def log_skill_usage(skill_name: str, user_input: str = "",
                    success: bool = True, duration_ms: int = 0,
                    token_cost: int = 0):
    """记录一次技能使用"""
    conn = _get_conn()
    conn.execute(
        """INSERT INTO skill_usage
           (skill_name, user_input, success, duration_ms, token_cost)
           VALUES (?, ?, ?, ?, ?)""",
        (skill_name, user_input, 1 if success else 0, duration_ms, token_cost)
    )
    conn.commit()
    conn.close()


def log_routing_decision(user_input: str, candidates: list = None,
                         chosen_skill: str = None, chosen_score: float = None,
                         fallback_to_decompose: bool = False):
    """记录一次路由决策（包含 Top-N 候选）"""
    conn = _get_conn()
    conn.execute(
        """INSERT INTO routing_decisions
           (user_input, candidates_json, chosen_skill, chosen_score, fallback_to_decompose)
           VALUES (?, ?, ?, ?, ?)""",
        (user_input,
         json.dumps(candidates or [], ensure_ascii=False),
         chosen_skill, chosen_score,
         1 if fallback_to_decompose else 0)
    )
    conn.commit()
    conn.close()


# ═══ 查询接口 ═══

def get_recent_tasks(limit: int = 20) -> List[dict]:
    """获取最近的任务记录"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM tasks ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_skill_stats() -> List[dict]:
    """获取技能使用统计"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT skill_name, COUNT(*) as uses,
               SUM(success) as successes,
               AVG(duration_ms) as avg_duration,
               AVG(token_cost) as avg_tokens
        FROM skill_usage
        GROUP BY skill_name
        ORDER BY uses DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_failed_skills() -> List[dict]:
    """获取失败率高的技能"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT skill_name, COUNT(*) as total,
               SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) as failures,
               ROUND(1.0 * SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) / COUNT(*), 2) as fail_rate
        FROM skill_usage
        GROUP BY skill_name
        HAVING failures > 0
        ORDER BY fail_rate DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_unmatched_inputs(limit: int = 20) -> List[dict]:
    """获取总是匹配不到技能的用户输入（需要创建新技能的信号）"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT user_input, COUNT(*) as count
        FROM routing_decisions
        WHERE fallback_to_decompose = 1
        GROUP BY user_input
        ORDER BY count DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_tool_error_stats() -> List[dict]:
    """获取工具错误统计"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT tool_name, COUNT(*) as total,
               SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) as errors,
               ROUND(1.0 * SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) / COUNT(*), 2) as error_rate
        FROM tool_calls
        GROUP BY tool_name
        HAVING errors > 0
        ORDER BY error_rate DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# 模块导入时自动初始化
init_db()
