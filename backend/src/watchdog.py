"""进程级看门狗（R-6.53z8e）。

只回答一个问题：**主进程还在吗**。不查询任何业务状态，不参与窗口/登录/采集逻辑。

为什么必须是独立进程 + 管道：

- 主进程被 SIGKILL、崩溃、或卡死时，它自己的 `atexit`/signal 清理不会执行，
  引擎（CFT Chrome）残留成孤儿，用户看到「窗口关了但进程还在」。
- 管道读端 EOF 是**内核事件**：父进程不管以何种方式消失，写端都一定关闭，
  子进程的阻塞读立刻返回。零轮询、零延迟、零业务耦合。

与「业务轮询」的边界（用户口径「轮形的都先去掉」）：

- 业务状态同步一律走事件（SSE / page 事件 / asyncio.Event），本模块不碰。
- 这里只做进程存活看门，属 OS 层职责，是「关窗/崩溃后不留残骸」的最后一道闸。
"""
import os
import subprocess

_WRITE_FD = -1  # 主进程持有的写端；进程无论怎么死，操作系统都会关闭它


def default_markers() -> list[str]:
    """引擎进程的识别串：命中任一即视为本应用的引擎子进程。

    用两条独立特征而不是 PID：PID 需要主进程额外上报、易失效；
    这两条串只出现在本应用启动的 CFT 上，不会误杀用户日常 Chrome。
    """
    marks: list[str] = []
    try:
        from src.engine_pool import builtin_cft_candidates  # noqa: PLC0415

        cft = builtin_cft_candidates()
        if cft and "chrome-mac-x64" in cft:
            marks.append(cft.split("chrome-mac-x64")[0] + "chrome-mac-x64")
    except Exception:  # noqa: BLE001（引擎模块不可用时退回目录特征）
        pass
    try:
        from src import config  # noqa: PLC0415

        root = os.path.expanduser(config.ACCOUNTS_ROOT)
        if root:
            marks.append(root)
    except Exception:  # noqa: BLE001
        pass
    return [m for m in marks if m]


def _watch_until_eof(fd: int) -> None:
    """阻塞读管道，直到写端关闭（父进程消失）或读出错。"""
    try:
        while os.read(fd, 8):
            pass
    except OSError:  # 管道已被对端关闭/描述符失效
        pass


def _reap(markers: list[str]) -> None:
    """清理残留引擎进程（幂等，主进程正常退出后重复调用也无害）。"""
    for m in markers:
        if not m:
            continue
        try:
            subprocess.run(  # noqa: S603（固定参数、无 shell、路径来自本应用自身）
                ["pkill", "-9", "-f", m],
                check=False,
                timeout=10,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:  # noqa: BLE001（清理失败不影响退出）
            pass


def _child_main(read_fd: int, markers: list[str]) -> None:
    """子进程主体：等父进程死 → 清理 → 退出。永不返回。"""
    try:
        os.setsid()  # 独立会话：不再接收终端的 Ctrl-C，由管道 EOF 决定生死
    except Exception:  # noqa: BLE001
        pass
    _watch_until_eof(read_fd)
    _reap(markers)
    os._exit(0)


def start(markers: list[str] | None = None) -> bool:
    """派生看门狗子进程。返回是否成功（失败只影响残留清理，不影响主流程）。"""
    global _WRITE_FD
    if _WRITE_FD >= 0:
        return True  # 已启动，幂等
    if not hasattr(os, "fork"):  # 非 POSIX：不提供看门狗
        return False
    marks = markers if markers is not None else default_markers()
    if not marks:
        return False
    try:
        r, w = os.pipe()
        pid = os.fork()
    except Exception:  # noqa: BLE001
        return False
    if pid == 0:
        try:
            os.close(w)
            _child_main(r, marks)
        finally:
            os._exit(0)
    os.close(r)
    _WRITE_FD = w
    print(f"[xhs-leads][watchdog] 看门狗已启动（pid={pid}，监视 {len(marks)} 条特征）", flush=True)
    return True


def stop() -> None:
    """关闭写端：子进程读到 EOF 后清理残留并退出。主进程退出前调用（可选）。"""
    global _WRITE_FD
    if _WRITE_FD < 0:
        return
    try:
        os.close(_WRITE_FD)
    except OSError:
        pass
    _WRITE_FD = -1
