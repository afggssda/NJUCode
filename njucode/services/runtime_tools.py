from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run_hello_world(workspace_root: Path) -> str:
    """执行最小化 Hello World 运行示例。

    函数会在工作区创建临时 Python 文件并调用当前解释器运行，
    然后根据退出码拼装成功/失败消息。

    Args:
        workspace_root: 运行目录与临时文件生成目录。

    Returns:
        人类可读的执行结果字符串。
    """
    hello_file = workspace_root / "hello_world.py"
    hello_file.write_text('print("Hello World from nju_code")\n', encoding="utf-8")

    process = subprocess.run(
        [sys.executable, str(hello_file)],
        cwd=str(workspace_root),
        capture_output=True,
        text=True,
        timeout=15,
    )

    stdout = process.stdout.strip()
    stderr = process.stderr.strip()
    if process.returncode == 0:
        return f"Hello World 执行成功: {stdout}"
    return f"Hello World 执行失败(code={process.returncode}): {stderr or stdout}"
