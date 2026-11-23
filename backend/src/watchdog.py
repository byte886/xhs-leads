"""进程级看门狗。

只回答一个问题：**主进程还在吗**。不查询任何业务状态，不参与窗口/登录/采集逻辑。

为什么必须是独立进程 + 管道：

- 主进程被 SIGKILL、崩溃、或卡死时，它自己的 `atexit`/signal 清理不会执行，
  引擎（CFT Chrome）残留成孤儿，用户看到「窗口关了但进程还在」。
- 管道读端 EOF 是**内核事件**：父进程不管以何种方式消失，写端都一定关闭，
  子进程的阻塞读立刻返回。零轮询、零延迟、零业务耦合。

为什么自带独立日志文件：

- 主进程被强杀时，它自己的日志处理器来不及 flush，最后一行往往就是缺失的。
- 看门狗把「何时发现父进程消失 / 命中哪些 PID / 杀掉成功与否 / 自身耗时」
  写进 `<data>/watchdog.log`，该文件的 fd 与主进程日志互不相干，主进程死透了
  也照样完整。定位「某次异常退出到底清没清干净」只看这一个文件。
"""
import os
import subprocess
import sys
from datetime import datetime

_WRITE_FD = -1  # 主进程持有的写端；进程无论怎么死，操作系统都会关闭它
_CHILD_PROC = None  # Windows：看门狗子进程句柄（写端 = proc.stdin，靠它 EOF 判父进程消失）
_WIN_CHILD_FLAG = "XHS_WD_CHILD"  # launcher 见到即转来看门狗子进程分支
_WIN_MARKS_ENV = "XHS_WD_MARKS"   # 特征串以 JSON 传，避开命令行空格/引号
_WIN_LOG_ENV = "XHS_WD_LOG"
_CREATE_NO_WINDOW = 0x08000000  # Windows 子进程不弹黑框
_WIN_FD_MARK = -2  # _WRITE_FD 占位：表示写端是 Popen.stdin 而不是 fd
_LOG_KEEP_BYTES = 512 * 1024  # 看门狗日志轮转阈值


def default_markers() -> list[str]:
    """引擎进程的识别串：命中任一即视为本应用的引擎子进程。

    用两条独立特征而不是 PID：PID 需要主进程额外上报、易失效；
    这两条串只出现在本应用启动的 CFT 上，不会误杀用户日常 Chrome。
    """
    marks: list[str] = []
    try:
        from src.engine_types import cft_platform_dirname  # noqa: PLC0415
        from src.engine_pool import builtin_cft_candidates  # noqa: PLC0415

        cft = builtin_cft_candidates()
        dirname = cft_platform_dirname()  # chrome-mac-x64 / chrome-win64
        if cft and dirname in cft:
            marks.append(cft.split(dirname)[0] + dirname)
    except Exception:  # noqa: BLE001（引擎模块不可用时退回目录特征）
        pass
    try:
        from src import config  # noqa: PLC0415

        # 新统一根（数据目录内）+ 历史根（早期账号）：两个都要认，否则老账号进程会漏管
        roots = [str(config.accounts_dir()), os.path.expanduser(config.ACCOUNTS_ROOT)]
        marks.extend([r for r in roots if r])
    except Exception:  # noqa: BLE001
        pass
    return [m for m in marks if m]


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(log_path: str, event: str, msg: str) -> None:
    """写一行看门狗日志：同时进独立文件与控制台（文件是主进程死后唯一可信源）。"""
    line = f"{_stamp()} [watchdog][pid={os.getpid()}][{event}] {msg}"
    if log_path:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
        except Exception:  # noqa: BLE001（无日志不能阻断清理）
            pass
    print(line, flush=True)


def _rotate(log_path: str) -> None:
    """超过阈值就把当前日志滚成 .1（覆盖旧备份），避免无限增长。"""
    if not log_path:
        return
    try:
        if os.path.getsize(log_path) > _LOG_KEEP_BYTES:
            os.replace(log_path, log_path + ".1")
    except Exception:  # noqa: BLE001
        pass


def _tail_text(log_path: str, size: int = 4096) -> str:
    """读日志末尾一段（用于判断上一次运行怎么结束的）。"""
    try:
        with open(log_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            n = f.tell()
            f.seek(max(0, n - size))
            return f.read().decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return ""


def _parse_line(line: str) -> tuple[str, str] | None:
    """日志行 → (时间, 事件名)；不是看门狗行返回 None。"""
    if "[watchdog]" not in line or "][" not in line:
        return None
    ts = line.split(" [watchdog]")[0].strip()
    event = line.split("][")[-1].split("]")[0].strip()
    return ts, event


def _last_event(log_path: str) -> tuple[str, str] | None:
    """读上一次运行的最后一条事件：(时间, 事件名)。用于识别上次是否异常退出。"""
    if not log_path:
        return None
    for line in reversed(_tail_text(log_path).splitlines()):
        ev = _parse_line(line)
        if ev:
            return ev
    return None


def _watch_until_eof(fd: int) -> None:
    """阻塞读管道，直到写端关闭（父进程消失）或读出错。"""
    try:
        while os.read(fd, 8):
            pass
    except OSError:  # 管道已被对端关闭/描述符失效
        pass


def _ps_table() -> str | None:
    """一次取全量进程表（pid 命令行）。失败返回 None。

    Windows 没有 `ps`：用 psutil 遍历进程，拼成与 `ps -ax -o pid=,command=` 同格式的行，
    下游匹配逻辑（按命令行子串）完全复用。
    """
    if sys.platform == "win32":
        try:
            import psutil  # noqa: PLC0415（仅 Windows 需要）

            rows = []
            for p in psutil.process_iter(["pid", "cmdline"]):
                cmd = " ".join(p.info["cmdline"] or [])
                if cmd:
                    rows.append(f"{p.info['pid']} {cmd}")
            return "\n".join(rows)
        except Exception:  # noqa: BLE001
            return None
    try:
        return subprocess.run(  # noqa: S603,S607（固定参数、无 shell）
            ["ps", "-ax", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except Exception:  # noqa: BLE001
        return None


def _match_line(line: str, markers: list[str], me: int) -> list[tuple[int, str]]:
    """一行 ps 输出 → 命中的 (pid, marker)；不合规行/自身返回空。"""
    parts = line.strip().split(None, 1)
    if len(parts) != 2 or not parts[0].isdigit():
        return []
    pid = int(parts[0])
    if pid == me:
        return []
    return [(pid, m) for m in markers if m and m in parts[1]]


def _find_targets(markers: list[str]) -> dict[str, list[int]]:
    """按命令行特征找引擎进程 PID（一次 ps 覆盖全部特征，零周期）。

    不用 `pgrep -f <marker>`：pgrep 自己的 argv 里就含 marker，会把自己列进来。
    """
    hits: dict[str, list[int]] = {m: [] for m in markers}
    table = _ps_table()
    if table is None:
        return hits
    me = os.getpid()
    for line in table.splitlines():
        for pid, m in _match_line(line, markers, me):
            hits[m].append(pid)
    return hits


def _reap(markers: list[str], log_path: str = "") -> None:
    """清理残留引擎进程：先找列表、逐个 marker 清理、逐条落日志。"""
    hits = _find_targets(markers)
    for idx, m in enumerate(markers):
        if not m:
            continue
        _reap_marker(idx, m, hits.get(m, []), log_path)


def _reap_marker(idx: int, marker: str, pids: list, log_path: str) -> None:
    if not pids:
        _log(log_path, "reap", f"marker[{idx}] 无命中（{marker}）")
        return
    ok, fail = 0, []
    for pid in pids:
        try:
            _kill_pid(pid)
            ok += 1
        except ProcessLookupError:
            ok += 1  # 已自行退出，同样算清理完成
        except Exception as e:  # noqa: BLE001
            fail.append(f"{pid}:{e.__class__.__name__}")
    _log(
        log_path,
        "reap",
        f"marker[{idx}] 命中 {len(pids)} killed={ok} "
        f"pids={','.join(str(p) for p in pids)} 失败={fail or '无'}",
    )


def _kill_pid(pid: int) -> None:
    """Windows 用 psutil、其他平台 SIGKILL；平台差异只在这一处。"""
    if sys.platform == "win32":
        import psutil  # noqa: PLC0415（仅 Windows）

        psutil.Process(pid).kill()
        return
    os.kill(pid, 9)

def _child_main(read_fd: int, markers: list[str], log_path: str) -> None:
    """子进程主体：等父进程死 → 清理 → 退出。永不返回。"""
    t0 = datetime.now()
    try:
        os.setsid()  # 独立会话：不再接收终端的 Ctrl-C，由管道 EOF 决定生死
    except Exception:  # noqa: BLE001
        pass
    _log(log_path, "armed", f"等待父进程 EOF，监视 {len(markers)} 条特征")
    _watch_until_eof(read_fd)
    _log(log_path, "eof", "父进程已消失（正常退出 / SIGKILL / 崩溃），开始清理引擎")
    _reap(markers, log_path)
    cost = (datetime.now() - t0).total_seconds()
    _log(log_path, "exit", f"清理完成，存活 {cost:.1f}s 后退出")
    os._exit(0)


def _can_start(marks: list[str], log_path: str) -> bool:
    """前置条件：有派生手段（POSIX fork 或 Windows Popen）+ 至少一条特征串。"""
    if not hasattr(os, "fork") and sys.platform != "win32":
        _log(log_path, "start", "跳过：当前平台既无 os.fork 也不是 Windows")
        return False
    if not marks:
        _log(log_path, "start", "跳过：找不到任何引擎特征串")
        return False
    return True


def _warn_if_abnormal(log_path: str) -> None:
    """上一次运行没走到 exit/stop → 说明是异常退出，必须在本次启动时说出来。"""
    prev = _last_event(log_path)
    if prev and prev[1] not in ("exit", "stop"):
        _log(log_path, "start", f"告警：上次运行疑似异常退出（最后事件 {prev[0]} {prev[1]}）")


def _spawn(marks: list[str], log_path: str) -> tuple[int, int]:
    """派生看门狗。返回 (写端 fd/Popen 占位, 子进程 pid)；失败返回 (-1, -1)。

    POSIX：pipe + fork，写端 fd 随主进程消失被内核关闭。
    Windows：没有 fork，改用 subprocess.Popen(stdin=PIPE) —— 父进程无论怎么死，
    管道写端都会关，子进程的阻塞读立刻返回 EOF，语义与 fork 版一致。
    """
    if hasattr(os, "fork"):
        try:
            r, w = os.pipe()
            pid = os.fork()
        except Exception as e:  # noqa: BLE001
            _log(log_path, "start", f"失败：{e.__class__.__name__}: {e}")
            return -1, -1
        if pid == 0:
            try:
                os.close(w)
                _child_main(r, marks, log_path)
            finally:
                os._exit(0)  # 子进程永不返回父流程
        os.close(r)
        return w, pid
    return _spawn_win(marks, log_path)


def _spawn_win(marks: list[str], log_path: str) -> tuple[int, int]:
    """Windows 派生：Popen(stdin=PIPE)，子进程靠 stdin EOF 感知父进程消失。"""
    global _CHILD_PROC
    import json  # noqa: PLC0415（仅 Windows 路径用）

    cmd = [sys.executable]
    if not getattr(sys, "frozen", False):
        # 源码态：sys.executable 是 python.exe，还要把 launcher 脚本传进去
        cmd.append(os.path.abspath(sys.argv[0]))
    env = os.environ.copy()
    env[_WIN_CHILD_FLAG] = "1"
    env[_WIN_MARKS_ENV] = json.dumps(marks, ensure_ascii=False)
    env[_WIN_LOG_ENV] = log_path
    try:
        proc = subprocess.Popen(  # noqa: S603（固定参数、无 shell）
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception as e:  # noqa: BLE001
        _log(log_path, "start", f"失败（Popen）：{e.__class__.__name__}: {e}")
        return -1, -1
    _CHILD_PROC = proc
    return _WIN_FD_MARK, proc.pid


def child_main_stdin() -> None:
    """Windows 看门狗子进程主体：阻塞读 stdin 直到父进程消失 → 清理 → 退出。"""
    import json  # noqa: PLC0415

    t0 = datetime.now()
    try:
        marks = json.loads(os.environ.get(_WIN_MARKS_ENV) or "[]")
    except Exception:  # noqa: BLE001
        marks = []
    log_path = os.environ.get(_WIN_LOG_ENV) or ""
    _log(log_path, "armed", f"等待父进程 EOF（stdin 管道），监视 {len(marks)} 条特征")
    try:
        sys.stdin.buffer.read()  # 写端全关才返回：父进程存在期间一直阻塞
    except Exception:  # noqa: BLE001
        pass
    _log(log_path, "eof", "父进程已消失（正常退出 / 被强杀 / 崩溃），开始清理引擎")
    _reap(marks, log_path)
    cost = (datetime.now() - t0).total_seconds()
    _log(log_path, "exit", f"清理完成，存活 {cost:.1f}s 后退出")
    os._exit(0)


def start(markers: list[str] | None = None, log_path: str = "") -> bool:
    """派生看门狗子进程。返回是否成功（失败只影响残留清理，不影响主流程）。"""
    global _WRITE_FD
    if _WRITE_FD >= 0 or _CHILD_PROC is not None:
        return True  # 已启动，幂等
    marks = markers if markers is not None else default_markers()
    if not _can_start(marks, log_path):
        return False
    _rotate(log_path)
    _warn_if_abnormal(log_path)
    w, pid = _spawn(marks, log_path)
    if pid < 0:
        return False
    _WRITE_FD = w
    _log(
        log_path,
        "start",
        f"主进程 pid={os.getpid()} 看门狗 pid={pid} 日志={log_path or '(未指定)'} "
        f"markers={marks}",
    )
    return True


def stop(log_path: str = "") -> None:
    """关闭写端：子进程读到 EOF 后清理残留并退出。主进程退出前调用（可选）。"""
    global _WRITE_FD, _CHILD_PROC
    if _WRITE_FD < 0 and _CHILD_PROC is None:
        return
    _log(log_path, "stop", "主进程正常退出，通知看门狗收尾")
    if _WRITE_FD >= 0:
        try:
            os.close(_WRITE_FD)
        except OSError:
            pass
    if _CHILD_PROC is not None:
        try:
            if _CHILD_PROC.stdin is not None:
                _CHILD_PROC.stdin.close()  # 关写端 → 子进程 EOF → 清理后退出
        except Exception:  # noqa: BLE001（子进程可能已先退出）
            pass
        _CHILD_PROC = None
    _WRITE_FD = -1
