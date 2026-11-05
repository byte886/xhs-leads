"""统一 CDP 调用超时保护（R-6.53s）。

背景（Nov 5 实测取证）：Playwright 内部 timeout（TimeoutError）由 driver 子进程
控制、依赖 CDP 消息往返；CFT 158 引擎假死（渲染进程冻结）时 CDP 无响应，内部
timeout 不触发，`await page.goto/evaluate/bring_to_front` 永久挂起——曾导致
假死强杀重建、采集 0 条。所有 CDP 调用必须经 cdp_call 包裹：绝对超时
（asyncio.wait_for）+ 统一超时档位（一处定义）+ 统一日志（label 溯源，grep 即
定位问题）+ 统一异常（调用方按脏页处理）。

超时语义（借鉴 browser-control watchdog 经验）：
- 超时只是放弃等待；底层 CDP 请求不取消、可能仍在引擎侧执行。
- 超时后该 page/会话视为脏，不得复用——由 engine 假死检测（R-6.53l）接管重建。
- skip=True 用于非关键路径（焦点切换、窗口校准等），超时降级继续。
"""
import asyncio
import logging
from typing import Awaitable, Optional, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

# 统一超时档位（秒）——新增/调整超时只改这里，调用点引用常量
CDP_QUICK = 3.0    # 焦点切换 / 注入 / 轻量 evaluate
CDP_EVAL = 6.0     # 页面内容轮询 evaluate（wait_cards / login_wall）
CDP_NAV = 15.0     # goto 导航（Playwright 内部 12s + 外层 15s）
CDP_DRAIN = 8.0    # 详情抽取 / 滚动加载整段
CDP_PREP = 25.0    # 采集页准备整段（含重试）
CDP_TASK = 120.0   # 采集任务总时长


class CdpTimeoutError(TimeoutError):
    """CDP 调用绝对超时（引擎假死/渲染冻结）。调用方按脏页处理、不复用页面。"""


async def cdp_call(coro: Awaitable[T], *, timeout: float = CDP_QUICK,
                   label: str = "cdp", skip: bool = False) -> Optional[T]:
    """统一执行一个 CDP 协程并施加绝对超时。

    - 正常完成 → 返回结果。
    - 超时 → skip=False 抛 CdpTimeoutError（调用方按脏页处理）；
            skip=True 返回 None 并打 WARNING（含 label，便于 grep 定位）。
    - 底层异常（页面关闭/崩溃等）原样上抛，由调用方既有逻辑处理。
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError as e:
        if skip:
            log.warning("CDP 调用超时跳过 label=%s timeout=%.1fs（页面可能冻结，视为脏）",
                        label, timeout)
            return None
        raise CdpTimeoutError(
            f"CDP 调用超时 label={label} timeout={timeout}s") from e
