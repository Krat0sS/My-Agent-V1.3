"""
视觉理解模块 — 用 Vision 模型分析桌面截图
灵感来源：UFO（双 Agent）、browser-use（VLM 定位）、CogAgent（GUI 视觉）
"""
import json
import base64
import asyncio
from typing import Optional

import config


# ═══ Vision 模型客户端 ═══

_vision_client = None


def _get_vision_client():
    """获取 Vision 模型客户端（复用主 LLM 配置，可用独立 Vision 模型覆盖）"""
    global _vision_client
    if _vision_client is None:
        from openai import AsyncOpenAI
        api_key = getattr(config, 'VISION_API_KEY', None) or config.LLM_API_KEY
        base_url = getattr(config, 'VISION_BASE_URL', None) or config.LLM_BASE_URL
        _vision_client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=30)
    return _vision_client


async def analyze_screenshot(base64_image: str, question: str = None,
                              model: str = None) -> dict:
    """
    用 Vision 模型分析截图。

    Args:
        base64_image: base64 编码的图片
        question: 分析问题（默认：描述界面元素和位置）
        model: 覆盖模型名

    Returns:
        {"description": "...", "elements": [...], "error": "..."}
    """
    client = _get_vision_client()
    model = model or getattr(config, 'VISION_MODEL', 'gpt-4o-mini')

    prompt = question or (
        "请分析这张桌面截图，返回 JSON 格式：\n"
        '{"description": "界面整体描述", '
        '"elements": [{"type": "元素类型", "text": "文字内容", '
        '"position": "大致位置(如左上/中间/右下)", '
        '"grid": "网格坐标(如3,5)"}], '
        '"suggestion": "下一步建议操作"}'
    )

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{base64_image}",
                        "detail": "low"  # 省 token
                    }
                }
            ]
        }
    ]

    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=1000,
                temperature=0.1,
            ),
            timeout=30
        )
        content = resp.choices[0].message.content or ""

        # 尝试解析 JSON
        try:
            # 提取 JSON 块
            if "```json" in content:
                json_str = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                json_str = content.split("```")[1].split("```")[0].strip()
            else:
                json_str = content
            result = json.loads(json_str)
        except (json.JSONDecodeError, IndexError):
            result = {"description": content, "elements": [], "suggestion": ""}

        # 附加 token 统计
        if hasattr(resp, 'usage') and resp.usage:
            result["_usage"] = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            }

        return result

    except asyncio.TimeoutError:
        return {"error": "Vision 模型超时", "elements": []}
    except Exception as e:
        return {"error": f"Vision 调用失败: {str(e)}", "elements": []}


def analyze_screenshot_sync(base64_image: str, question: str = None) -> dict:
    """同步版本"""
    import concurrent.futures
    try:
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, analyze_screenshot(base64_image, question))
            return future.result(timeout=45)
    except RuntimeError:
        return asyncio.run(analyze_screenshot(base64_image, question))


# ═══ 网格截图工具 ═══

def add_grid_overlay(base64_image: str, grid_size: int = 100) -> str:
    """
    在截图上添加网格和坐标标注，帮助 LLM 定位。
    灵感来源：CogAgent 的坐标定位思路。

    Args:
        base64_image: base64 编码的图片
        grid_size: 网格单元大小（像素）

    Returns:
        带网格的 base64 图片
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        import io

        img_data = base64.b64decode(base64_image)
        img = Image.open(io.BytesIO(img_data)).convert("RGB")
        draw = ImageDraw.Draw(img)
        w, h = img.size

        # 自适应网格大小
        grid_size = max(50, min(grid_size, w // 5, h // 5))

        # 画网格线（半透明）
        for x in range(0, w, grid_size):
            draw.line([(x, 0), (x, h)], fill=(255, 0, 0, 80), width=1)
        for y in range(0, h, grid_size):
            draw.line([(0, y), (w, y)], fill=(255, 0, 0, 80), width=1)

        # 标注坐标（每格标注行列号）
        try:
            font = ImageFont.truetype("arial.ttf", 10)
        except (IOError, OSError):
            font = ImageFont.load_default()

        col = 0
        for x in range(0, w, grid_size):
            row = 0
            for y in range(0, h, grid_size):
                label = f"{row},{col}"
                draw.text((x + 2, y + 2), label, fill=(255, 255, 0), font=font)
                row += 1
            col += 1

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    except ImportError:
        return base64_image  # PIL 不可用时返回原图
    except Exception:
        return base64_image


# ═══ 桌面元素定位 ═══

SYSTEM_PROMPT_VISION = """你是一个 GUI 视觉分析专家。分析桌面截图，识别所有可交互的 UI 元素。

输出 JSON 格式：
{
  "elements": [
    {
      "type": "button|input|menu|icon|link|text|tab|checkbox|dropdown",
      "text": "元素上的文字",
      "position": "top-left|top-center|top-right|center-left|center|center-right|bottom-left|bottom-center|bottom-right",
      "grid": "row,col（网格坐标）",
      "confidence": 0.0-1.0
    }
  ],
  "active_window": "当前活动窗口标题",
  "suggestion": "下一步应该做什么"
}"""
