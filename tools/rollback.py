"""
回滚引擎 — 记录文件操作，支持一键恢复

所有文件移动/创建操作都通过此模块记录，用户说"恢复上次整理"时一键回滚。
回滚日志持久化到磁盘，跨会话可查，7 天自动过期。
"""
import json
import os
import time
import shutil
import datetime
from typing import Optional
import config


# 回滚日志目录
ROLLBACK_DIR = os.path.join(config.WORKSPACE, "rollback")
# 回滚记录保留天数
ROLLBACK_TTL_DAYS = 7


def _ensure_dir():
    os.makedirs(ROLLBACK_DIR, exist_ok=True)


def _op_id() -> str:
    """生成操作 ID：时间戳 + 随机后缀"""
    import random
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = random.randint(1000, 9999)
    return f"op_{ts}_{suffix}"


def _save_log(op_id: str, log: dict):
    """保存回滚日志到磁盘"""
    _ensure_dir()
    path = os.path.join(ROLLBACK_DIR, f"{op_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def _load_log(op_id: str) -> Optional[dict]:
    """加载回滚日志"""
    path = os.path.join(ROLLBACK_DIR, f"{op_id}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def _cleanup_expired():
    """清理过期的回滚日志"""
    _ensure_dir()
    cutoff = time.time() - ROLLBACK_TTL_DAYS * 86400
    for fname in os.listdir(ROLLBACK_DIR):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(ROLLBACK_DIR, fname)
        try:
            if os.path.getmtime(fpath) < cutoff:
                os.remove(fpath)
        except OSError:
            pass


# ═══ 公开 API ═══

def record_move(src: str, dst: str) -> dict:
    """
    记录一次文件移动操作。
    如果目标已存在，先记录冲突信息（不覆盖）。
    返回操作记录条目。
    """
    src = os.path.abspath(os.path.expanduser(src))
    dst = os.path.abspath(os.path.expanduser(dst))

    entry = {
        "action": "move",
        "src": src,
        "dst": dst,
        "src_existed": os.path.exists(src),
        "dst_existed": os.path.exists(dst),
        "timestamp": datetime.datetime.now().isoformat(),
    }

    if os.path.isdir(src):
        entry["is_dir"] = True
        entry["src_size"] = sum(
            os.path.getsize(os.path.join(dp, f))
            for dp, _, filenames in os.walk(src)
            for f in filenames
        )
    elif os.path.isfile(src):
        entry["is_dir"] = False
        entry["src_size"] = os.path.getsize(src)
    else:
        entry["src_size"] = 0

    return entry


def record_create(path: str) -> dict:
    """记录一次目录创建操作"""
    path = os.path.abspath(os.path.expanduser(path))
    return {
        "action": "create_dir",
        "path": path,
        "timestamp": datetime.datetime.now().isoformat(),
    }


def start_operation(description: str = "") -> str:
    """
    开始一个回滚操作组，返回 op_id。
    一次"整理桌面"可能包含多个 move/create，用 op_id 把它们归为一组。
    """
    op_id = _op_id()
    log = {
        "op_id": op_id,
        "description": description,
        "entries": [],
        "created_at": datetime.datetime.now().isoformat(),
        "status": "recording",  # recording | completed | rolled_back
    }
    _save_log(op_id, log)
    return op_id


def add_entry(op_id: str, entry: dict):
    """向操作组添加一条记录"""
    log = _load_log(op_id)
    if log is None:
        return
    log["entries"].append(entry)
    _save_log(op_id, log)


def complete_operation(op_id: str) -> dict:
    """标记操作组完成"""
    log = _load_log(op_id)
    if log is None:
        return {"error": f"操作记录不存在: {op_id}"}
    log["status"] = "completed"
    log["completed_at"] = datetime.datetime.now().isoformat()
    _save_log(op_id, log)
    return {"success": True, "op_id": op_id, "entries_count": len(log["entries"])}


def rollback(op_id: str) -> dict:
    """
    回滚指定操作组。按逆序恢复所有文件移动。
    返回详细回滚结果。
    """
    log = _load_log(op_id)
    if log is None:
        return {"error": f"操作记录不存在: {op_id}"}
    if log["status"] == "rolled_back":
        return {"error": "该操作已经回滚过了"}

    results = []
    entries = list(reversed(log["entries"]))  # 逆序回滚

    for entry in entries:
        action = entry.get("action")

        if action == "move":
            src = entry["src"]  # 原始位置（回滚时是目标）
            dst = entry["dst"]  # 移动后位置（回滚时是源）

            if not os.path.exists(dst):
                results.append({
                    "entry": entry,
                    "status": "skipped",
                    "reason": f"文件已不存在: {dst}",
                    "filename": os.path.basename(dst)
                })
                continue

            # 记录回滚前的时间戳
            pre_rollback_mtime = None
            try:
                pre_rollback_mtime = datetime.datetime.fromtimestamp(
                    os.path.getmtime(dst)
                ).isoformat()
            except OSError:
                pass

            # 如果原始位置被其他文件占了，重命名
            if os.path.exists(src):
                backup = src + f".rollback_backup_{int(time.time())}"
                try:
                    os.rename(src, backup)
                    results.append({
                        "entry": entry,
                        "status": "conflict",
                        "reason": f"原位置已被占用，已备份到: {backup}",
                        "filename": os.path.basename(dst),
                        "conflict_backup": backup
                    })
                except OSError as e:
                    results.append({
                        "entry": entry,
                        "status": "error",
                        "reason": f"无法备份冲突文件: {e}",
                        "filename": os.path.basename(dst)
                    })
                    continue

            try:
                os.makedirs(os.path.dirname(src), exist_ok=True)
                shutil.move(dst, src)

                # Q5: 回滚后验证
                post_rollback_exists = os.path.exists(src)
                post_rollback_mtime = None
                post_rollback_size = 0
                if post_rollback_exists:
                    post_rollback_mtime = datetime.datetime.fromtimestamp(
                        os.path.getmtime(src)
                    ).isoformat()
                    post_rollback_size = os.path.getsize(src)

                results.append({
                    "entry": entry,
                    "status": "restored",
                    "from": dst,
                    "to": src,
                    "filename": os.path.basename(src),
                    "pre_rollback_mtime": pre_rollback_mtime,
                    "post_rollback_mtime": post_rollback_mtime,
                    "post_rollback_size": post_rollback_size,
                    "verified": post_rollback_exists,
                })
            except Exception as e:
                results.append({
                    "entry": entry,
                    "status": "error",
                    "reason": str(e),
                    "filename": os.path.basename(dst)
                })

        elif action == "create_dir":
            dirpath = entry["path"]
            if os.path.isdir(dirpath):
                try:
                    # 只删空目录
                    os.rmdir(dirpath)
                    results.append({
                        "entry": entry,
                        "status": "removed",
                        "path": dirpath,
                        "filename": os.path.basename(dirpath)
                    })
                except OSError:
                    results.append({
                        "entry": entry,
                        "status": "skipped",
                        "reason": f"目录非空，未删除: {dirpath}",
                        "filename": os.path.basename(dirpath)
                    })
            else:
                results.append({
                    "entry": entry,
                    "status": "skipped",
                    "reason": f"目录已不存在: {dirpath}",
                    "filename": os.path.basename(dirpath)
                    "status": "skipped",
                    "reason": f"目录已不存在: {dirpath}"
                })

    log["status"] = "rolled_back"
    log["rolled_back_at"] = datetime.datetime.now().isoformat()
    log["rollback_results"] = results
    _save_log(op_id, log)

    restored = sum(1 for r in results if r["status"] == "restored")
    errors = sum(1 for r in results if r["status"] == "error")

    # ═══ Q5 信任审计 ═══
    restored_list = []
    for r in results:
        if r["status"] == "restored":
            file_info = {
                "filename": r.get("filename", os.path.basename(r.get("to", ""))),
                "to": r.get("to", ""),
                "verified": r.get("verified", False),
            }
            if r.get("post_rollback_mtime"):
                file_info["last_modified"] = r["post_rollback_mtime"]
            if r.get("post_rollback_size"):
                file_info["size_bytes"] = r["post_rollback_size"]
            restored_list.append(file_info)

    comparison_hook = None
    if restored_list:
        sample = "、".join(f["filename"] for f in restored_list[:3])
        comparison_hook = (
            f"如果你想确认，我可以列出每个文件的最后修改时间戳，"
            f"你一眼就能看出是否回到原位。（最近修改：{sample}）"
        )

    user_msg = f"✅ 已恢复「{log.get('description', '')}」中的 {restored} 个文件"
    if errors:
        user_msg += f"，{errors} 个恢复失败"
    if restored_list:
        user_msg += "。\n最近恢复的文件："
        for f in restored_list[:5]:
            mtime = f.get("last_modified", "")
            if mtime:
                try:
                    dt = datetime.datetime.fromisoformat(mtime)
                    time_str = dt.strftime("%m-%d %H:%M")
                except (ValueError, TypeError):
                    time_str = mtime
                user_msg += f"\n  • {f['filename']}（最后修改: {time_str}）"
            else:
                user_msg += f"\n  • {f['filename']}"
    if comparison_hook:
        user_msg += f"\n\n💡 {comparison_hook}"

    return {
        "success": True,
        "op_id": op_id,
        "description": log.get("description", ""),
        "total": len(results),
        "restored": restored,
        "errors": errors,
        "details": results,
        "restored_files": restored_list,
        "comparison_hook": comparison_hook,
        "user_message": user_msg,
    }


def list_operations(include_rolled_back: bool = False) -> list[dict]:
    """列出所有回滚操作记录"""
    _cleanup_expired()
    _ensure_dir()

    ops = []
    for fname in sorted(os.listdir(ROLLBACK_DIR), reverse=True):
        if not fname.endswith(".json"):
            continue
        log = _load_log(fname.replace(".json", ""))
        if log is None:
            continue
        if not include_rolled_back and log.get("status") == "rolled_back":
            continue
        ops.append({
            "op_id": log["op_id"],
            "description": log.get("description", ""),
            "status": log.get("status", "unknown"),
            "entries_count": len(log.get("entries", [])),
            "created_at": log.get("created_at", ""),
        })

    return ops


def get_operation_summary(op_id: str) -> Optional[dict]:
    """获取操作摘要（不含完整条目，用于展示）"""
    log = _load_log(op_id)
    if log is None:
        return None

    summary = {
        "op_id": log["op_id"],
        "description": log.get("description", ""),
        "status": log.get("status", "unknown"),
        "created_at": log.get("created_at", ""),
        "entries_count": len(log.get("entries", [])),
    }

    # 统计
    entries = log.get("entries", [])
    total_size = sum(e.get("src_size", 0) for e in entries)
    summary["total_size_bytes"] = total_size
    summary["total_size_human"] = _human_size(total_size)

    # 条目摘要（最多显示 20 条）
    entry_summaries = []
    for e in entries[:20]:
        if e["action"] == "move":
            entry_summaries.append(f"  {os.path.basename(e['src'])} → {os.path.basename(os.path.dirname(e['dst']))}/")
        elif e["action"] == "create_dir":
            entry_summaries.append(f"  📁 创建: {os.path.basename(e['path'])}/")
    if len(entries) > 20:
        entry_summaries.append(f"  ... 还有 {len(entries) - 20} 条")
    summary["entries_preview"] = "\n".join(entry_summaries)

    return summary


def _human_size(size_bytes: int) -> str:
    """人类可读的文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"
