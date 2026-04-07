"""灵信标注 MCP Server — 历史数据来源标注服务"""

from pathlib import Path

from fastmcp import FastMCP

from lingmessage.annotate import (
    _load_raw_messages,
    annotate_all,
    detect_rapid_succession_batches,
    detect_same_second_anomalies,
    print_report,
)

mcp = FastMCP("lingmessage-annotate")


@mcp.tool()
def detect_anomalies(threads_dir: str) -> dict:
    """检测同秒多成员发言的身份幻觉异常。

    Args:
        threads_dir: 线程目录路径（如 ~/.lingmessage/threads）

    Returns:
        {"same_second_anomalies": int, "rapid_succession_batches": int}
    """
    path = Path(threads_dir).expanduser()
    messages = [msg for _, msg in _load_raw_messages(path)]
    ss = detect_same_second_anomalies(messages)
    rs = detect_rapid_succession_batches(messages)
    return {
        "same_second_anomalies": len(ss),
        "rapid_succession_batches": len(rs),
    }


@mcp.tool()
def annotate_messages(threads_dir: str, dry_run: bool = True) -> dict:
    """标注消息来源类型（GENERATED/INFERRED）。

    Args:
        threads_dir: 线程目录路径
        dry_run: True 为预览模式（不写入），False 为应用标注

    Returns:
        标注结果统计
    """
    path = Path(threads_dir).expanduser()
    result = annotate_all(path, dry_run=dry_run)
    return result.to_dict() | {"dry_run": dry_run}


@mcp.tool()
def annotation_report(threads_dir: str) -> str:
    """生成人类可读的标注报告。

    Args:
        threads_dir: 线程目录路径

    Returns:
        标注报告文本
    """
    import io
    import sys

    path = Path(threads_dir).expanduser()
    result = annotate_all(path, dry_run=True)

    old_stdout = sys.stdout
    sys.stdout = buf = io.StringIO()
    print_report(result)
    sys.stdout = old_stdout
    return buf.getvalue()


if __name__ == "__main__":
    mcp.run()
