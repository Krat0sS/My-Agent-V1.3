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
    _migrate_v2(conn)
    conn.close()


# ═══════════════════════════════════════════════════════════
# Schema v2: 易经架构扩展（变爻、五行、时辰、万物、太极诊断）
# ═══════════════════════════════════════════════════════════

def _migrate_v2(conn):
    """Schema v2 迁移（幂等，可重复调用）"""
    cur = conn.cursor()

    # ── tool_calls 加列：变爻标记 ──
    cols = {row[1] for row in cur.execute("PRAGMA table_info(tool_calls)").fetchall()}
    if 'yao_type' not in cols:
        cur.execute("ALTER TABLE tool_calls ADD COLUMN yao_type TEXT DEFAULT NULL")
    if 'recovery_action' not in cols:
        cur.execute("ALTER TABLE tool_calls ADD COLUMN recovery_action TEXT DEFAULT NULL")

    # ── tasks 加列：时辰 + 任务类型 ──
    cols = {row[1] for row in cur.execute("PRAGMA table_info(tasks)").fetchall()}
    if 'time_slot' not in cols:
        cur.execute("ALTER TABLE tasks ADD COLUMN time_slot TEXT DEFAULT NULL")
    if 'task_type' not in cols:
        cur.execute("ALTER TABLE tasks ADD COLUMN task_type TEXT DEFAULT NULL")

    # ── routing_decisions 加列：编排依据 ──
    cols = {row[1] for row in cur.execute("PRAGMA table_info(routing_decisions)").fetchall()}
    if 'orchestrator_note' not in cols:
        cur.execute("ALTER TABLE routing_decisions ADD COLUMN orchestrator_note TEXT DEFAULT NULL")

    # ── 新表：五行生克关系（skill_pairs）──
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS skill_pairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skill_a TEXT NOT NULL,
            skill_b TEXT NOT NULL,
            total_calls INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            success_rate REAL DEFAULT 0.5,
            relation TEXT DEFAULT 'neutral',
            is_seed INTEGER DEFAULT 0,
            seed_weight REAL DEFAULT 0.0,
            last_updated TEXT DEFAULT (datetime('now')),
            UNIQUE(skill_a, skill_b)
        );
        CREATE INDEX IF NOT EXISTS idx_skill_pairs_ab ON skill_pairs(skill_a, skill_b);
        CREATE INDEX IF NOT EXISTS idx_skill_pairs_relation ON skill_pairs(relation);
    """)

    # ── 新表：时辰规律（time_patterns）──
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS time_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time_slot TEXT NOT NULL,
            day_type TEXT NOT NULL,
            task_type TEXT NOT NULL,
            frequency INTEGER DEFAULT 0,
            last_seen TEXT DEFAULT (datetime('now')),
            UNIQUE(time_slot, day_type, task_type)
        );
        CREATE INDEX IF NOT EXISTS idx_time_patterns_slot ON time_patterns(time_slot, day_type);
    """)

    # ── 新表：太极诊断日志（diagnosis_log）──
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS diagnosis_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            session_id TEXT,
            inner_state TEXT NOT NULL,
            outer_state TEXT NOT NULL,
            inner_score REAL,
            outer_score REAL,
            hexagram TEXT NOT NULL,
            action_hint TEXT NOT NULL,
            downstream_skill TEXT,
            elapsed_ms INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_diagnosis_time ON diagnosis_log(timestamp);
    """)

    # ── 新表：万物生成计划（wanwu_plans）──
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS wanwu_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            session_id TEXT,
            user_input TEXT NOT NULL,
            skill_a TEXT NOT NULL,
            skill_b TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            success INTEGER DEFAULT 0,
            promoted INTEGER DEFAULT 0,
            user_feedback TEXT,
            elapsed_ms INTEGER DEFAULT 0,
            token_cost INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_wanwu_ab ON wanwu_plans(skill_a, skill_b);
        CREATE INDEX IF NOT EXISTS idx_wanwu_success ON wanwu_plans(success, promoted);
    """)

    conn.commit()


# ═══════════════════════════════════════════════════════════
# 工具函数：时辰映射
# ═══════════════════════════════════════════════════════════

def get_time_slot(hour: int) -> str:
    """将 24 小时制转换为十二时辰"""
    # 子时跨日：23:00-01:00
    if hour >= 23 or hour < 1:
        return 'zi'
    slots = [
        (1, 3, 'chou'), (3, 5, 'yin'), (5, 7, 'mao'),
        (7, 9, 'chen'), (9, 11, 'si'), (11, 13, 'wu'),
        (13, 15, 'wei'), (15, 17, 'shen'), (17, 19, 'you'),
        (19, 21, 'xu'), (21, 23, 'hai')
    ]
    for start, end, name in slots:
        if start <= hour < end:
            return name
    return 'zi'


TIME_SLOT_ENERGY = {
    'si':   'peak',      # 9-11 点：认知巅峰
    'mao':  'high',      # 5-7 点：晨间高效
    'chen': 'high',      # 7-9 点：通勤前专注
    'yin':  'high',      # 3-5 点：早起者窗口
    'shen': 'recovery',  # 15-17 点：下午回升
    'you':  'medium',    # 17-19 点：傍晚中等
    'xu':   'creative',  # 19-21 点：晚间创造力
    'wu':   'decline',   # 11-13 点：午饭前疲劳
    'wei':  'low',       # 13-15 点：午后低谷
    'hai':  'low',       # 21-23 点：准备休息
    'zi':   'low',       # 23-1 点：深夜
    'chou': 'low',       # 1-3 点：凌晨
}

TIME_SLOT_NAMES = {
    'zi': '子时', 'chou': '丑时', 'yin': '寅时', 'mao': '卯时',
    'chen': '辰时', 'si': '巳时', 'wu': '午时', 'wei': '未时',
    'shen': '申时', 'you': '酉时', 'xu': '戌时', 'hai': '亥时'
}


# ═══════════════════════════════════════════════════════════
# 种子规则：五行生克冷启动
# ═══════════════════════════════════════════════════════════

SEED_RULES = [
    # (skill_a, skill_b, seed_relation)
    ('file-search', 'desktop-organize', 'generate'),   # 搜到了才能整理
    ('web-research', 'file-search', 'generate'),        # 调研完需要找文件
    ('file-search', 'web-research', 'neutral'),          # 无因果
    ('desktop-organize', 'web-research', 'neutral'),     # 无因果
    ('web-research', 'desktop-organize', 'neutral'),     # 无因果
]


def seed_skill_pairs():
    """植入冷启动种子规则（幂等，可重复调用）"""
    conn = _get_conn()
    for a, b, relation in SEED_RULES:
        conn.execute("""
            INSERT OR IGNORE INTO skill_pairs
            (skill_a, skill_b, relation, is_seed, seed_weight, success_rate, total_calls)
            VALUES (?, ?, ?, 1, 0.3, 0.5, 0)
        """, (a, b, relation))
    conn.commit()
    conn.close()


def log_tool_call(tool_name: str, args: dict = None, result: str = "",
                  success: bool = True, elapsed_ms: int = 0,
                  error_message: str = "", session_id: str = "",
                  yao_type: str = None, recovery_action: str = None):
    """记录一次工具调用（v2: 支持变爻标记）"""
    conn = _get_conn()
    conn.execute(
        """INSERT INTO tool_calls
           (session_id, tool_name, args_json, result_preview, success, elapsed_ms,
            error_message, yao_type, recovery_action)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, tool_name,
         json.dumps(args or {}, ensure_ascii=False)[:2000],
         result[:500] if result else "",
         1 if success else 0,
         elapsed_ms, error_message,
         yao_type, recovery_action)
    )
    conn.commit()
    conn.close()


def log_task(user_input: str, matched_skill: str = None, match_score: float = None,
             plan_json: str = None, actual_steps_json: str = None,
             success: bool = True, token_cost: int = 0, duration_ms: int = 0,
             session_id: str = "", time_slot: str = None, task_type: str = None):
    """记录一次任务执行（v2: 支持时辰 + 任务类型）"""
    conn = _get_conn()
    conn.execute(
        """INSERT INTO tasks
           (session_id, user_input, matched_skill, match_score, plan_json,
            actual_steps_json, success, token_cost, duration_ms, time_slot, task_type)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, user_input, matched_skill, match_score,
         plan_json, actual_steps_json,
         1 if success else 0, token_cost, duration_ms,
         time_slot, task_type)
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
                         fallback_to_decompose: bool = False,
                         orchestrator_note: str = None):
    """记录一次路由决策（包含 Top-N 候选）（v2: 支持编排依据）"""
    conn = _get_conn()
    conn.execute(
        """INSERT INTO routing_decisions
           (user_input, candidates_json, chosen_skill, chosen_score,
            fallback_to_decompose, orchestrator_note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_input,
         json.dumps(candidates or [], ensure_ascii=False),
         chosen_skill, chosen_score,
         1 if fallback_to_decompose else 0,
         orchestrator_note)
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


# ═══════════════════════════════════════════════════════════
# v2 新增：太极诊断
# ═══════════════════════════════════════════════════════════

def log_diagnosis(inner_state: str, outer_state: str,
                  inner_score: float, outer_score: float,
                  hexagram: str, action_hint: str,
                  downstream_skill: str = None,
                  elapsed_ms: int = 0, session_id: str = ""):
    """记录一次太极诊断"""
    conn = _get_conn()
    conn.execute(
        """INSERT INTO diagnosis_log
           (session_id, inner_state, outer_state, inner_score, outer_score,
            hexagram, action_hint, downstream_skill, elapsed_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, inner_state, outer_state, inner_score, outer_score,
         hexagram, action_hint, downstream_skill, elapsed_ms)
    )
    conn.commit()
    conn.close()


def get_recent_diagnoses(limit: int = 10) -> List[dict]:
    """获取最近的诊断记录"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM diagnosis_log ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════
# v2 新增：变爻查询
# ═══════════════════════════════════════════════════════════

def get_recent_tool_calls(tool_name: str = None, limit: int = 10) -> List[dict]:
    """获取最近的工具调用（可按工具名过滤）"""
    conn = _get_conn()
    if tool_name:
        rows = conn.execute(
            "SELECT * FROM tool_calls WHERE tool_name=? ORDER BY timestamp DESC LIMIT ?",
            (tool_name, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tool_calls ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_tool_call_yao(tool_call_id: int, yao_type: str, recovery_action: str):
    """更新工具调用的变爻标记"""
    conn = _get_conn()
    conn.execute(
        "UPDATE tool_calls SET yao_type=?, recovery_action=? WHERE id=?",
        (yao_type, recovery_action, tool_call_id)
    )
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════
# v2 新增：五行生克
# ═══════════════════════════════════════════════════════════

def update_skill_pair(skill_a: str, skill_b: str, success: bool):
    """更新技能对的执行记录（自动计算成功率和生克关系）"""
    conn = _get_conn()
    conn.execute("""
        INSERT INTO skill_pairs (skill_a, skill_b, total_calls, success_count, seed_weight, is_seed)
        VALUES (?, ?, 1, ?, 0, 0)
        ON CONFLICT(skill_a, skill_b) DO UPDATE SET
            total_calls = total_calls + 1,
            success_count = success_count + ?,
            seed_weight = MAX(0, seed_weight * 0.7),
            last_updated = datetime('now')
    """, (skill_a, skill_b, 1 if success else 0, 1 if success else 0))

    # 重新计算成功率和关系
    conn.execute("""
        UPDATE skill_pairs
        SET success_rate = ROUND(1.0 * success_count / total_calls, 3),
            relation = CASE
                WHEN is_seed = 1 AND seed_weight > 0.1 THEN relation
                WHEN 1.0 * success_count / total_calls > 0.8 THEN 'generate'
                WHEN 1.0 * success_count / total_calls < 0.5 THEN 'overcome'
                ELSE 'neutral'
            END
        WHERE skill_a = ? AND skill_b = ?
    """, (skill_a, skill_b))
    conn.commit()
    conn.close()


def get_skill_pair(skill_a: str, skill_b: str) -> Optional[dict]:
    """查询特定技能对的关系"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM skill_pairs WHERE skill_a=? AND skill_b=?",
        (skill_a, skill_b)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_generate_pairs() -> List[dict]:
    """获取所有相生技能对"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM skill_pairs WHERE relation='generate' ORDER BY success_rate DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_overcome_pairs() -> List[dict]:
    """获取所有相克技能对"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM skill_pairs WHERE relation='overcome' ORDER BY success_rate ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_skill_pairs() -> List[dict]:
    """获取所有技能对"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM skill_pairs ORDER BY total_calls DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════
# v2 新增：万物生成
# ═══════════════════════════════════════════════════════════

def log_wanwu_plan(user_input: str, skill_a: str, skill_b: str,
                   plan_json: str, success: bool = False,
                   user_feedback: str = None, elapsed_ms: int = 0,
                   token_cost: int = 0, session_id: str = ""):
    """记录一次万物生成计划"""
    conn = _get_conn()
    conn.execute(
        """INSERT INTO wanwu_plans
           (session_id, user_input, skill_a, skill_b, plan_json,
            success, user_feedback, elapsed_ms, token_cost)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, user_input, skill_a, skill_b, plan_json,
         1 if success else 0, user_feedback, elapsed_ms, token_cost)
    )
    conn.commit()
    conn.close()


def mark_wanwu_success(plan_id: int):
    """标记万物计划执行成功"""
    conn = _get_conn()
    conn.execute(
        "UPDATE wanwu_plans SET success=1 WHERE id=?", (plan_id,)
    )
    conn.commit()
    conn.close()


def mark_wanwu_promoted(plan_id: int):
    """标记万物计划已升级为正式技能"""
    conn = _get_conn()
    conn.execute(
        "UPDATE wanwu_plans SET promoted=1 WHERE id=?", (plan_id,)
    )
    conn.commit()
    conn.close()


def get_wanwu_promotion_candidates(threshold: int = 3) -> List[dict]:
    """查询可以升级为正式技能的万物组合"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT skill_a, skill_b, COUNT(*) as success_count,
               MIN(id) as first_plan_id
        FROM wanwu_plans
        WHERE success = 1 AND promoted = 0
        GROUP BY skill_a, skill_b
        HAVING success_count >= ?
    """, (threshold,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════
# v2 新增：时辰规律
# ═══════════════════════════════════════════════════════════

def update_time_pattern(time_slot: str, day_type: str, task_type: str):
    """更新时辰规律（自动累加频率）"""
    conn = _get_conn()
    conn.execute("""
        INSERT INTO time_patterns (time_slot, day_type, task_type, frequency)
        VALUES (?, ?, ?, 1)
        ON CONFLICT(time_slot, day_type, task_type) DO UPDATE SET
            frequency = frequency + 1,
            last_seen = datetime('now')
    """, (time_slot, day_type, task_type))
    conn.commit()
    conn.close()


def get_time_pattern(time_slot: str, day_type: str) -> List[dict]:
    """查询某时段的任务规律"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT * FROM time_patterns
        WHERE time_slot=? AND day_type=?
        ORDER BY frequency DESC
    """, (time_slot, day_type)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_peak_tasks(time_slot: str, day_type: str, min_confidence: float = 0.6) -> List[dict]:
    """查询某时段的高频任务（过滤低置信度）"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT task_type, frequency,
               ROUND(1.0 * frequency / SUM(frequency) OVER(), 3) as confidence
        FROM time_patterns
        WHERE time_slot=? AND day_type=?
        HAVING confidence >= ?
        ORDER BY frequency DESC
    """, (time_slot, day_type, min_confidence)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# 模块导入时自动初始化
init_db()
seed_skill_pairs()
