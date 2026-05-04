"""
builtin_compat — 将 builtin.py 中的 40 个旧工具桥接到新 ToolRegistry

这个文件在启动时被导入，它做的事情：
1. import builtin.py 中的所有工具函数
2. 为每个工具创建 ToolDefinition 并注册到 registry
3. 这样新系统可以用 registry.execute() 调用旧工具

不需要修改 builtin.py 的任何代码。
"""
import json
from tools.registry import registry, ToolDefinition


def _check_pyautogui():
    try:
        import pyautogui
        return True
    except ImportError:
        return False

def _check_playwright():
    try:
        import playwright
        return True
    except ImportError:
        return False

def _check_duckduckgo():
    try:
        from duckduckgo_search import DDGS
        return True
    except ImportError:
        return False

def _check_jieba():
    try:
        import jieba
        return True
    except ImportError:
        return False


def _import_and_register():
    """导入 builtin.py，提取所有 @register 装饰的工具，注册到新 registry"""
    from tools import builtin

    # builtin.py 使用 _tools 字典存储工具
    old_tools = getattr(builtin, '_tools', {})

    # 工具 → 分类映射
    CATEGORY_MAP = {
        'read_file': 'file', 'write_file': 'file', 'edit_file': 'file',
        'list_files': 'file', 'scan_files': 'file', 'find_files': 'file',
        'move_file': 'file', 'batch_move': 'file', 'organize_directory': 'file',
        'rollback_operation': 'file', 'list_rollback_history': 'file',
        'check_directory_status': 'file_monitor', 'get_new_files': 'file_monitor',
        'mark_cleanup_done': 'file_monitor',
        'run_command': 'system', 'run_command_confirmed': 'system',
        'web_search': 'search', 'news_search': 'search',
        'remember': 'memory', 'recall': 'memory', 'set_preference': 'memory',
        'browser_navigate': 'browser', 'browser_screenshot': 'browser',
        'browser_click': 'browser', 'browser_type': 'browser',
        'browser_press_key': 'browser', 'browser_download': 'browser',
        'browser_session_screenshot': 'browser', 'browser_get_content': 'browser',
        'browser_wait': 'browser',
        'list_windows': 'desktop', 'get_active_window': 'desktop',
        'activate_window': 'desktop', 'desktop_screenshot': 'desktop',
        'desktop_screenshot_grid': 'desktop',
        'desktop_click': 'desktop', 'desktop_double_click': 'desktop',
        'desktop_type': 'desktop', 'desktop_keys': 'desktop',
        'desktop_move_mouse': 'desktop', 'desktop_scroll': 'desktop',
        'vision_analyze': 'vision',
    }

    # 工具 → 风险等级
    RISK_MAP = {
        'read_file': 'low', 'list_files': 'low', 'scan_files': 'low',
        'find_files': 'low', 'recall': 'low', 'web_search': 'low',
        'news_search': 'low', 'list_windows': 'low', 'get_active_window': 'low',
        'desktop_screenshot': 'low', 'desktop_screenshot_grid': 'low',
        'browser_navigate': 'low', 'browser_screenshot': 'low',
        'browser_session_screenshot': 'low', 'browser_get_content': 'low',
        'check_directory_status': 'low', 'get_new_files': 'low',
        'list_rollback_history': 'low', 'vision_analyze': 'low',
        'write_file': 'medium', 'edit_file': 'medium', 'move_file': 'medium',
        'batch_move': 'medium', 'organize_directory': 'medium',
        'remember': 'medium', 'set_preference': 'medium',
        'activate_window': 'medium', 'desktop_click': 'medium',
        'desktop_double_click': 'medium', 'desktop_type': 'medium',
        'desktop_keys': 'medium', 'desktop_move_mouse': 'medium',
        'desktop_scroll': 'medium', 'browser_click': 'medium',
        'browser_type': 'medium', 'browser_press_key': 'medium',
        'browser_download': 'medium', 'browser_wait': 'medium',
        'mark_cleanup_done': 'low',
        'run_command': 'high', 'run_command_confirmed': 'high',
        'rollback_operation': 'medium',
    }

    # 工具 → 依赖检查
    CHECK_MAP = {
        'desktop_click': _check_pyautogui,
        'desktop_double_click': _check_pyautogui,
        'desktop_type': _check_pyautogui,
        'desktop_keys': _check_pyautogui,
        'desktop_move_mouse': _check_pyautogui,
        'desktop_scroll': _check_pyautogui,
        'desktop_screenshot': _check_pyautogui,
        'desktop_screenshot_grid': _check_pyautogui,
        'list_windows': _check_pyautogui,
        'get_active_window': _check_pyautogui,
        'activate_window': _check_pyautogui,
        'browser_navigate': _check_playwright,
        'browser_screenshot': _check_playwright,
        'web_search': _check_duckduckgo,
        'news_search': _check_duckduckgo,
        'recall': _check_jieba,
    }

    registered = 0
    for name, tool_info in old_tools.items():
        schema = tool_info.get('schema', {})
        func = tool_info.get('func')
        enabled = tool_info.get('enabled', True)

        if not enabled or func is None:
            continue

        # 提取 description
        func_schema = schema.get('function', schema)
        description = func_schema.get('description', '')

        registry.register(
            name=name,
            description=description,
            schema=func_schema,
            handler=func,
            category=CATEGORY_MAP.get(name, 'general'),
            check_fn=CHECK_MAP.get(name),
            is_async=False,
            risk_level=RISK_MAP.get(name, 'low'),
        )
        registered += 1

    return registered


# 模块级自动注册
try:
    count = _import_and_register()
    # print(f"✅ 从 builtin.py 桥接了 {count} 个工具到 ToolRegistry")
except Exception as e:
    print(f"⚠️ builtin_compat 桥接失败: {e}")
