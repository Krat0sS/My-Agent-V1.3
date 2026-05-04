"""LLM 客户端 — async 版本，兼容 OpenAI 格式"""
import json
import asyncio
from openai import AsyncOpenAI
import config

_client = None

def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=config.LLM_API_KEY,
            base_url=config.LLM_BASE_URL,
            timeout=config.LLM_TIMEOUT,
        )
    return _client

async def chat(messages: list[dict], tools: list[dict] = None,
               temperature: float = None, timeout: float = None) -> dict:
    client = get_client()
    kwargs = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "temperature": temperature or config.LLM_TEMPERATURE,
        "max_tokens": config.LLM_MAX_TOKENS,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if "deepseek" in config.LLM_MODEL.lower():
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(**kwargs),
            timeout=timeout or config.LLM_TIMEOUT
        )
    except asyncio.TimeoutError:
        return {"role": "assistant", "content": "⏱️ LLM 响应超时，请稍后重试或缩短请求。", "_timeout": True}
    except Exception as e:
        return {"role": "assistant", "content": f"❌ LLM 调用失败: {str(e)}", "_error": True}

    msg = resp.choices[0].message
    result = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        result["tool_calls"] = [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ]
    if hasattr(resp, 'usage') and resp.usage:
        result["_usage"] = {
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "total_tokens": resp.usage.total_tokens,
        }
    return result

async def chat_simple(system_prompt: str, user_prompt: str) -> str:
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
    result = await chat(messages)
    return result["content"]

def chat_simple_sync(system_prompt: str, user_prompt: str) -> str:
    import concurrent.futures
    try:
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, chat_simple(system_prompt, user_prompt))
            return future.result(timeout=30)
    except RuntimeError:
        return asyncio.run(chat_simple(system_prompt, user_prompt))
