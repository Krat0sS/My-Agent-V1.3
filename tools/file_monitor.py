"""
文件监控 — 跟踪关键目录状态，智能提醒整理

不是实时文件系统监听（太重），而是轻量检查：
- 检查目录文件数
- 距离上次整理的时间
- 新增文件类型分析
- 智能判断是否该提醒

配合心跳钩子使用。
"""
import os
import json
import time
import datetime
from typing import Optional
import config


# 监控状态文件
MONITOR_STATE_FILE = os.path.join(config.WORKSPACE, "monitor_state.json")

# 默认监控目录（Windows + Linux/macOS）
DEFAULT_WATCH_DIRS = [
    ("~/Desktop", "桌面"),
    ("~/Downloads", "下载"),
]

# 提醒阈值
MIN_FILES_TO_REMIND = 3       # 文件数 < 3 不提醒
MIN_INTERVAL_DAYS = 7         # 距上次整理 < 7 天不提醒
FILE_COUNT_THRESHOLD = 20     # 文件数 > 20 才主动提醒

# 不需要提醒的文件扩展名（系统/临时文件）
IGNORE_EXTS = {".tmp", ".bak", ".swp", ".lnk", ".ini", ".ds_store", ".thumbs.db"}


def _load_state() -> dict:
    """加载监控状态"""
    if os.path.exists(MONITOR_STATE_FILE):
        try:
            with open(MONITOR_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"last_cleanup": {}, "last_remind": {}, "file_snapshots": {}}


def _save_state(state: dict):
    """保存监控状态"""
    os.makedirs(os.path.dirname(MONITOR_STATE_FILE), exist_ok=True)
    with open(MONITOR_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _expand_path(path: str) -> str:
    """展开 ~ 和环境变量"""
    return os.path.abspath(os.path.expanduser(path))


def _get_file_stats(path: str) -> dict:
    """获取目录文件统计"""
    path = _expand_path(path)
    if not os.path.isdir(path):
        return {"exists": False, "path": path}

    files = []
    ext_counts = {}
    total_size = 0

    try:
        for fname in os.listdir(path):
            if fname.startswith("."):
                continue
            fpath = os.path.join(path, fname)
            if os.path.isfile(fpath):
                ext = os.path.splitext(fname)[1].lower()
                if ext in IGNORE_EXTS:
                    continue
                try:
                    stat = os.stat(fpath)
                    files.append({
                        "name": fname,
                        "ext": ext,
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                    })
                    ext_counts[ext] = ext_counts.get(ext, 0) + 1
                    total_size += stat.st_size
                except OSError:
                    continue
    except PermissionError:
        return {"exists": True, "path": path, "error": "权限不足"}

    return {
        "exists": True,
        "path": path,
        "file_count": len(files),
        "total_size": total_size,
        "total_size_human": _human_size(total_size),
        "ext_counts": ext_counts,
        "files": sorted(files, key=lambda x: x["modified"], reverse=True)[:30],
    }


def _human_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def _should_remind(dir_key: str, stats: dict, state: dict) -> dict:
    """
    智能判断是否应该提醒整理。
    返回 {"remind": bool, "reason": str, "urgency": str}
    """
    file_count = stats.get("file_count", 0)

    # 文件太少，不值得整理
    if file_count < MIN_FILES_TO_REMIND:
        return {"remind": False, "reason": f"只有 {file_count} 个文件，还不够乱"}

    # 检查距离上次整理的时间
    last_cleanup_ts = state.get("last_cleanup", {}).get(dir_key, 0)
    days_since = (time.time() - last_cleanup_ts) / 86400

    if days_since < MIN_INTERVAL_DAYS:
        return {"remind": False, "reason": f"距离上次整理只有 {days_since:.1f} 天，太频繁了"}

    # 检查距离上次提醒的时间
    last_remind_ts = state.get("last_remind", {}).get(dir_key, 0)
    hours_since_remind = (time.time() - last_remind_ts) / 3600

    if hours_since_remind < 24:
        return {"remind": False, "reason": f"距离上次提醒只有 {hours_since_remind:.1f} 小时"}

    # 判断紧急程度
    if file_count > 50:
        urgency = "high"
        reason = f"有 {file_count} 个文件了，确实该整理了"
    elif file_count > FILE_COUNT_THRESHOLD:
        urgency = "medium"
        reason = f"有 {file_count} 个文件，可以整理一下"
    else:
        urgency = "low"
        reason = f"有 {file_count} 个文件，最近 7 天没整理了"

    # 文件类型分析
    ext_counts = stats.get("ext_counts", {})
    if ext_counts:
        top_ext = max(ext_counts, key=ext_counts.get)
        if top_ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            reason += f"（主要是图片文件 {ext_counts[top_ext]} 个）"
        elif top_ext in (".exe", ".msi", ".dmg"):
            reason += f"（有 {ext_counts[top_ext]} 个安装包）"

    return {"remind": True, "reason": reason, "urgency": urgency}


def check_all() -> dict:
    """检查所有监控目录，返回需要提醒的列表"""
    state = _load_state()
    results = []
    needs_remind = []

    for dir_path, dir_label in DEFAULT_WATCH_DIRS:
        expanded = _expand_path(dir_path)
        if not os.path.isdir(expanded):
            results.append({"dir": dir_label, "path": dir_path, "exists": False})
            continue

        stats = _get_file_stats(dir_path)
        decision = _should_remind(dir_label, stats, state)

        result = {
            "dir": dir_label,
            "path": dir_path,
            "exists": True,
            "file_count": stats.get("file_count", 0),
            "total_size": stats.get("total_size_human", "0 B"),
            "should_remind": decision["remind"],
            "reason": decision["reason"],
            "urgency": decision.get("urgency", "low"),
        }
        results.append(result)

        if decision["remind"]:
            needs_remind.append(result)

    return {
        "checked": len(results),
        "needs_remind": len(needs_remind),
        "directories": results,
        "remind_dirs": needs_remind,
    }


def mark_cleanup(dir_label: str):
    """标记某目录已整理"""
    state = _load_state()
    state.setdefault("last_cleanup", {})[dir_label] = time.time()
    _save_state(state)


def mark_reminded(dir_label: str):
    """标记某目录已提醒"""
    state = _load_state()
    state.setdefault("last_remind", {})[dir_label] = time.time()
    _save_state(state)


def get_new_files(dir_path: str, since_hours: int = 24) -> list:
    """获取最近 N 小时内新增的文件"""
    dir_path = _expand_path(dir_path)
    if not os.path.isdir(dir_path):
        return []

    cutoff = time.time() - since_hours * 3600
    new_files = []

    try:
        for fname in os.listdir(dir_path):
            if fname.startswith("."):
                continue
            fpath = os.path.join(dir_path, fname)
            if os.path.isfile(fpath):
                try:
                    if os.path.getmtime(fpath) > cutoff:
                        ext = os.path.splitext(fname)[1].lower()
                        size = os.path.getsize(fpath)
                        new_files.append({
                            "name": fname,
                            "ext": ext,
                            "size": size,
                            "size_human": _human_size(size),
                            "category": _quick_categorize(ext),
                        })
                except OSError:
                    continue
    except PermissionError:
        pass

    return sorted(new_files, key=lambda x: x["size"], reverse=True)


def _quick_categorize(ext: str) -> str:
    """快速分类（不依赖完整的 _EXT_CATEGORIES）"""
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"):
        return "图片"
    if ext in (".mp4", ".avi", ".mkv", ".mov", ".webm"):
        return "视频"
    if ext in (".doc", ".docx", ".pdf", ".txt", ".xlsx", ".pptx", ".md"):
        return "文档"
    if ext in (".py", ".js", ".ts", ".java", ".c", ".cpp", ".go", ".rs", ".html", ".css", ".json"):
        return "代码"
    if ext in (".zip", ".rar", ".7z", ".tar", ".gz"):
        return "压缩包"
    if ext in (".exe", ".msi", ".dmg", ".deb", ".apk"):
        return "程序"
    return "其他"
