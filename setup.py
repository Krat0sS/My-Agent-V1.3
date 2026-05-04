#!/usr/bin/env python3
"""
首次运行设置 — 从原始仓库下载未修改的源文件

用法：python setup.py

这个脚本会从 GitHub 下载 v1.0 的原始源文件，
这些文件在 v1.1 中不需要修改，但作为依赖必须存在。
"""
import urllib.request
import os
import sys

BASE_URL = "https://raw.githubusercontent.com/Krat0sS/my-agent/main/"

FILES = [
    "tools/builtin.py",
    "tools/browser.py", 
    "tools/desktop.py",
    "tools/search.py",
    "tools/rollback.py",
    "tools/file_monitor.py",
    "tools/subprocess_runner.py",
    "tools/planner.py",
    "tools/vision.py",
    "channels/webchat.py",
    "knowledge_base.py",
    "kb_tools.py",
]

def main():
    print("📦 正在从 GitHub 下载原始源文件...")
    os.makedirs("tools", exist_ok=True)
    os.makedirs("channels", exist_ok=True)
    
    success = 0
    for filepath in FILES:
        url = BASE_URL + filepath
        try:
            print(f"  ⬇️  {filepath}...", end=" ")
            urllib.request.urlretrieve(url, filepath)
            print("✅")
            success += 1
        except Exception as e:
            print(f"❌ {e}")
    
    print(f"\n✅ 下载完成: {success}/{len(FILES)} 个文件")
    if success < len(FILES):
        print("⚠️  部分文件下载失败，请手动从 https://github.com/Krat0sS/my-agent 下载")
    
    print("\n🚀 现在可以运行: python main.py")

if __name__ == "__main__":
    main()
