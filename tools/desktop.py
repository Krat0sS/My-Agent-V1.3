"""
桌面 GUI 自动化工具
基于 PyAutoGUI，提供窗口管理、鼠标键盘控制、屏幕截图能力。

依赖：pip install pyautogui pygetwindow pillow

能力：
- activate_window: 根据窗口标题切换到前台
- click / double_click: 鼠标点击
- type_text: 模拟键盘输入
- press_keys: 组合键（如 ctrl+c, alt+tab）
- screenshot: 截取全屏或指定区域，返回 base64
- get_active_window: 获取当前活动窗口信息
- list_windows: 列出所有可见窗口

安全：所有写操作（点击、输入、按键）通过 on_confirm 回调确认。
"""
import json
import base64
import io
import time


def _check_pyautogui():
    """检查依赖是否安装"""
    try:
        import pyautogui
        return None
    except ImportError:
        return "pyautogui 未安装。运行: pip install pyautogui pygetwindow pillow"


def activate_window(title: str) -> str:
    """
    根据窗口标题关键词切换到前台。
    模糊匹配：包含 title 的窗口都会被激活。
    """
    err = _check_pyautogui()
    if err:
        return json.dumps({"error": err})

    try:
        import pygetwindow as gw
        windows = gw.getWindowsWithTitle(title)
        if not windows:
            # 尝试列出所有窗口供参考
            all_windows = gw.getAllWindows()
            visible = [w.title for w in all_windows if w.title.strip()]
            return json.dumps({
                "error": f"未找到包含 '{title}' 的窗口",
                "available_windows": visible[:20]
            })
        win = windows[0]
        if win.isMinimized:
            win.restore()
        win.activate()
        time.sleep(0.3)
        return json.dumps({
            "success": True,
            "window": win.title,
            "size": f"{win.width}x{win.height}",
            "position": f"({win.left}, {win.top})"
        })
    except Exception as e:
        return json.dumps({"error": f"窗口操作失败: {str(e)}"})


def list_windows() -> str:
    """列出所有可见窗口"""
    err = _check_pyautogui()
    if err:
        return json.dumps({"error": err})

    try:
        import pygetwindow as gw
        all_windows = gw.getAllWindows()
        windows = []
        for w in all_windows:
            if w.title.strip():
                windows.append({
                    "title": w.title,
                    "size": f"{w.width}x{w.height}",
                    "position": f"({w.left}, {w.top})",
                    "minimized": w.isMinimized,
                })
        return json.dumps({"windows": windows[:30]})
    except Exception as e:
        return json.dumps({"error": str(e)})


def get_active_window() -> str:
    """获取当前活动窗口信息"""
    err = _check_pyautogui()
    if err:
        return json.dumps({"error": err})

    try:
        import pygetwindow as gw
        win = gw.getActiveWindow()
        if win:
            return json.dumps({
                "title": win.title,
                "size": f"{win.width}x{win.height}",
                "position": f"({win.left}, {win.top})"
            })
        return json.dumps({"error": "无法获取活动窗口"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def click(x: int, y: int, button: str = "left") -> str:
    """在屏幕坐标 (x, y) 处点击鼠标"""
    err = _check_pyautogui()
    if err:
        return json.dumps({"error": err})

    try:
        import pyautogui
        pyautogui.click(x, y, button=button)
        return json.dumps({"success": True, "action": "click", "x": x, "y": y, "button": button})
    except Exception as e:
        return json.dumps({"error": f"点击失败: {str(e)}"})


def double_click(x: int, y: int) -> str:
    """在屏幕坐标 (x, y) 处双击"""
    err = _check_pyautogui()
    if err:
        return json.dumps({"error": err})

    try:
        import pyautogui
        pyautogui.doubleClick(x, y)
        return json.dumps({"success": True, "action": "double_click", "x": x, "y": y})
    except Exception as e:
        return json.dumps({"error": f"双击失败: {str(e)}"})


def type_text(text: str, interval: float = 0.05) -> str:
    """模拟键盘输入文本"""
    err = _check_pyautogui()
    if err:
        return json.dumps({"error": err})

    try:
        import pyautogui
        pyautogui.typewrite(text, interval=interval) if text.isascii() else pyautogui.write(text)
        return json.dumps({"success": True, "action": "type", "length": len(text)})
    except Exception as e:
        return json.dumps({"error": f"输入失败: {str(e)}"})


def press_keys(keys: str) -> str:
    """
    按下组合键。
    格式：用 + 连接，如 'ctrl+c', 'alt+tab', 'ctrl+shift+s'
    支持：ctrl, alt, shift, win, enter, tab, esc, space, backspace, delete, up, down, left, right
    """
    err = _check_pyautogui()
    if err:
        return json.dumps({"error": err})

    try:
        import pyautogui
        key_list = [k.strip().lower() for k in keys.split("+")]
        pyautogui.hotkey(*key_list)
        return json.dumps({"success": True, "action": "hotkey", "keys": key_list})
    except Exception as e:
        return json.dumps({"error": f"按键失败: {str(e)}"})


def screenshot(region: str = None, with_grid: bool = False) -> str:
    """
    截取屏幕（使用 mss 高性能截图 + 压缩）。
    Args:
        region: 可选，格式 "x,y,width,height"（如 "0,0,800,600"）。为空则全屏。
        with_grid: 是否叠加网格坐标（用于 GUI 定位）
    Returns:
        base64 编码的压缩图片（~800px 宽，JPEG 质量 60）
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        import io

        try:
            import mss
            import mss.tools

            with mss.mss() as sct:
                if region:
                    parts = [int(x.strip()) for x in region.split(",")]
                    if len(parts) != 4:
                        return json.dumps({"error": "region 格式: x,y,width,height"})
                    monitor = {"left": parts[0], "top": parts[1], "width": parts[2], "height": parts[3]}
                else:
                    monitor = sct.monitors[1]

                img = sct.grab(monitor)
                pil_img = Image.frombytes("RGB", img.size, img.rgb)
        except ImportError:
            import pyautogui
            if region:
                parts = [int(x.strip()) for x in region.split(",")]
                if len(parts) != 4:
                    return json.dumps({"error": "region 格式: x,y,width,height"})
                pil_img = pyautogui.screenshot(region=tuple(parts))
            else:
                pil_img = pyautogui.screenshot()

        # 压缩：缩放到最大 800px 宽，JPEG 质量 60
        w, h = pil_img.size
        max_w = 800
        if w > max_w:
            ratio = max_w / w
            pil_img = pil_img.resize((max_w, int(h * ratio)), Image.LANCZOS)

        result_info = {
            "success": True,
            "format": "jpeg",
            "size": f"{pil_img.width}x{pil_img.height}",
        }

        # 网格叠加（GUI 定位辅助）
        if with_grid:
            draw = ImageDraw.Draw(pil_img)
            gw, gh = pil_img.size
            grid_size = max(50, min(100, gw // 8, gh // 8))

            # 画网格线
            for x in range(0, gw, grid_size):
                draw.line([(x, 0), (x, gh)], fill=(255, 50, 50), width=1)
            for y in range(0, gh, grid_size):
                draw.line([(0, y), (gw, y)], fill=(255, 50, 50), width=1)

            # 标注坐标
            try:
                font = ImageFont.truetype("arial.ttf", 9)
            except (IOError, OSError):
                font = ImageFont.load_default()

            col = 0
            for x in range(0, gw, grid_size):
                row = 0
                for y in range(0, gh, grid_size):
                    label = f"{row},{col}"
                    # 背景色块提高可读性
                    draw.rectangle([x+1, y+1, x+28, y+12], fill=(0, 0, 0, 180))
                    draw.text((x + 2, y + 1), label, fill=(255, 255, 0), font=font)
                    row += 1
                col += 1

            result_info["grid"] = True
            result_info["grid_size"] = grid_size
            result_info["grid_hint"] = "坐标格式: row,col（行,列），从0开始"

        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG", quality=60, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        result_info["base64"] = b64
        return json.dumps(result_info, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"截图失败: {str(e)}"})


def mouse_move(x: int, y: int, duration: float = 0.3) -> str:
    """移动鼠标到指定位置"""
    err = _check_pyautogui()
    if err:
        return json.dumps({"error": err})

    try:
        import pyautogui
        pyautogui.moveTo(x, y, duration=duration)
        return json.dumps({"success": True, "action": "move", "x": x, "y": y})
    except Exception as e:
        return json.dumps({"error": str(e)})


def scroll(clicks: int, x: int = None, y: int = None) -> str:
    """
    滚动鼠标滚轮。
    clicks: 正数向上，负数向下
    x, y: 可选，滚动位置。不指定则在当前位置滚动。
    """
    err = _check_pyautogui()
    if err:
        return json.dumps({"error": err})

    try:
        import pyautogui
        if x is not None and y is not None:
            pyautogui.scroll(clicks, x=x, y=y)
        else:
            pyautogui.scroll(clicks)
        return json.dumps({"success": True, "action": "scroll", "clicks": clicks})
    except Exception as e:
        return json.dumps({"error": str(e)})
