"""命令执行工具 — asyncio.subprocess 版本"""
import json
import asyncio
import config


async def run_command_async(command: str, cwd: str = None, timeout: int = 30) -> str:
    """
    异步执行 shell 命令。
    用 asyncio.subprocess 替代 subprocess.run，支持取消。
    """
    # 安全检查：黑名单
    for blocked in config.BLOCKED_COMMANDS:
        if blocked in command:
            return json.dumps({"error": f"危险命令被阻止: {blocked}"})

    # 安全检查：确认列表
    needs_confirm = False
    for prefix in config.CONFIRM_COMMANDS:
        if command.strip().startswith(prefix) or f" {prefix}" in command:
            needs_confirm = True
            break

    if needs_confirm:
        return json.dumps({
            "needs_confirm": True,
            "command": command,
            "warning": f"⚠️ 该命令可能修改系统状态，是否确认执行？\n$ {command}"
        })

    return await _exec_subprocess(command, cwd, timeout)


async def run_command_confirmed_async(command: str, cwd: str = None, timeout: int = 30) -> str:
    """异步执行已确认的命令（跳过确认检查）"""
    for blocked in config.BLOCKED_COMMANDS:
        if blocked in command:
            return json.dumps({"error": f"危险命令被阻止: {blocked}"})
    return await _exec_subprocess(command, cwd, timeout)


async def _exec_subprocess(command: str, cwd: str = None, timeout: int = 30) -> str:
    """底层 asyncio.subprocess 执行"""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            # 超时：杀掉进程组
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            return json.dumps({
                "error": f"命令超时 ({timeout}s)",
                "command": command,
                "hint": "进程已被终止。"
            })

        return json.dumps({
            "stdout": stdout.decode(errors="replace")[:10000],
            "stderr": stderr.decode(errors="replace")[:5000],
            "returncode": proc.returncode,
            "success": proc.returncode == 0
        })
    except asyncio.CancelledError:
        # 取消：杀掉进程
        try:
            proc.kill()
            await proc.wait()
        except (ProcessLookupError, UnboundLocalError):
            pass
        return json.dumps({
            "cancelled": True,
            "message": "命令已被用户取消。"
        })
    except Exception as e:
        return json.dumps({"error": str(e)})
