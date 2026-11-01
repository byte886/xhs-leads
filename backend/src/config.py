"""统一配置中心（架构改造 P1）：路径 / 端口 / 账号注册表 / 环境变量覆盖。

设计原则：
- 读取优先级：环境变量 > data/config.json > 默认值
- 路径一律 ~ 相对存储、读取时 expanduser（可移植：换用户/换机不失效）
- 账号注册表 data/accounts.json：多账户引擎池的数据源（每账号独立 user_data_dir）
- 引擎端口支持动态探测（生产独立 App 形态），XHS_PROFILE_PORT 可强制指定
- 向后兼容：XHS_BROWSER_MODE / XHS_PROFILE_PORT / XHS_PROFILE_DIR 环境变量仍生效
  （映射到 default 账号），现有调用方零改动。

用法示例：
    from src import config
    port = config.engine_port()                    # 引擎调试端口
    acc = config.get_account("default")            # 账号注册表读取
    config.add_account("collect_a", role="collect")
"""
import json
import os
import pathlib
import socket
import sys
import time
import uuid

# ---------------------------------------------------------------------------
# 基础路径
# ---------------------------------------------------------------------------
ROOT = pathlib.Path(__file__).resolve().parents[2]          # 项目根（源码/安装根）
# 数据目录：打包态（独立 .app）由 launcher 注入 XHS_LEADS_DATA（应用支持目录），
# 开发态默认 ROOT/data。数据与代码分离是壳（B 批）的硬约束：升级换包不丢数据。
_APP_DATA = os.environ.get("XHS_LEADS_DATA")
DATA_DIR = pathlib.Path(_APP_DATA).expanduser() if _APP_DATA else ROOT / "data"
ACCOUNTS_FILE = DATA_DIR / "accounts.json"
CONFIG_FILE = DATA_DIR / "config.json"

# 默认账号目录（R 批 v1.4：归一为数据目录内 profile；历史 ~/.xhs-leads-profile 保留兼容）
DEFAULT_ACCOUNT_DIR_NAME = "profile"
# 多账户统一根目录
ACCOUNTS_ROOT = "~/.xhs-leads/accounts"


def home_relative(p: str) -> str:
    """把 $HOME 下的绝对路径规范化为 ~ 相对形式（可移植：换用户/换机不失效）。

    仅转换 $HOME 前缀（含整个 HOME 本身）；其余绝对路径（如 /Applications/...）保持原样。
    """
    if not p:
        return p
    home = os.path.expanduser("~")
    try:
        if p == home:
            return "~"
        if p.startswith(home + os.sep):
            return "~" + p[len(home):]
    except Exception:  # noqa: BLE001
        pass
    return p


def expand(p: str | None) -> str:
    """~ 相对路径 → 绝对路径（空值返回空串）。"""
    if not p:
        return ""
    return os.path.expanduser(p)


# ---------------------------------------------------------------------------
# 通用配置（config.json）：引擎/端口/行为参数
# ---------------------------------------------------------------------------
_DEFAULTS = {
    "engine_port": 9223,          # 引擎调试端口（XHS_PROFILE_PORT 可覆盖）
    "front_port": 8791,           # 前端服务端口（uvicorn 监听；XHS_FRONT_PORT 可覆盖）
    "auto_front_port": True,      # front_port 被占用时自动探测空闲端口
    "browser_mode": "engine",     # engine=独立引擎（B 方案）/ daily=连日常 Chrome（调试遗留）
    "headless": True,             # 引擎无头化（R 批 v1.4）：无头无窗口；False=后台可见窗口兜底
    "chrome_path": "",            # 用户自定义 Chrome 路径（~ 相对）
    "profile_dir": "",            # 兼容字段：default 账号 user_data_dir 旧配置
    "collect_min_interval": 90.0, # 废弃（R-6.15 移除任务间节流；保留字段兼容旧配置）
    "request_interval": [1.0, 1.8],  # 拟人节奏随机区间
}


def _read_json(path: pathlib.Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def _write_json(path: pathlib.Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")


def get_config() -> dict:
    """合并后的配置（默认 < config.json < 环境变量覆盖点另行处理）。"""
    cfg = dict(_DEFAULTS)
    cfg.update(_read_json(CONFIG_FILE))
    return cfg


def set_config(key: str, value) -> dict:
    """写单个配置项（持久化 config.json），返回更新后的合并配置。"""
    cfg = _read_json(CONFIG_FILE)
    cfg[key] = value
    _write_json(CONFIG_FILE, cfg)
    return get_config()


def find_free_port(preferred: int, lo: int = 8800, hi: int = 8899) -> int:
    """端口策略：preferred 空闲则用之；否则在 [lo, hi] 探测第一个空闲端口。

    生产独立 App 形态下多开/冲突自愈；测试脚本仍可显式指定。
    """
    if _port_free(preferred):
        return preferred
    for p in range(lo, hi + 1):
        if _port_free(p):
            return p
    return preferred  # 全占用：返回首选，让启动时报错提示


def _port_free(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            return s.connect_ex(("127.0.0.1", port)) != 0
    except Exception:  # noqa: BLE001
        return False


def engine_port() -> int:
    """引擎调试端口：XHS_PROFILE_PORT > config.engine_port。"""
    env = os.environ.get("XHS_PROFILE_PORT")
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    return int(get_config().get("engine_port", 9223))


def front_port() -> int:
    """前端服务端口：XHS_FRONT_PORT > config.front_port；auto 时探测空闲。"""
    env = os.environ.get("XHS_FRONT_PORT")
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    cfg = get_config()
    preferred = int(cfg.get("front_port", 8791))
    if cfg.get("auto_front_port", True):
        return find_free_port(preferred)
    return preferred


def chrome_path() -> str:
    """用户自定义 Chrome 路径（~ 相对 → 绝对；config > 旧 cdp_config.json）。"""
    cfg = get_config()
    p = cfg.get("chrome_path") or _read_json(DATA_DIR / "cdp_config.json").get("chrome_path") or ""
    return expand(p) if p else ""


# ---------------------------------------------------------------------------
# 运行模式（R 批：dev / prod 区分）
# ---------------------------------------------------------------------------
# 打包进壳（PyInstaller）后改代码必须重打包、日志落盘、前端为静态产物——不便调试；
# 故显式区分：dev = 源码直跑（vite HMR + 控制台日志，改码即生效）；prod = 壳内交付形态。
# 优先级：XHS_LEADS_MODE 环境变量 > auto 判定（PyInstaller 打包态 → prod，否则 dev）。
_MODE_ENV = os.environ.get("XHS_LEADS_MODE", "").strip().lower()


def mode() -> str:
    """当前运行模式：'dev' 或 'prod'。XHS_LEADS_MODE=dev|prod 可显式覆盖。"""
    if _MODE_ENV in ("dev", "prod"):
        return _MODE_ENV
    return "prod" if getattr(sys, "_MEIPASS", None) else "dev"


def is_dev() -> bool:
    """是否开发/测试态（源码直跑，方便调试）。"""
    return mode() == "dev"


def is_prod() -> bool:
    """是否生产/交付态（打包进壳，静态产物）。"""
    return mode() == "prod"


def frontend_base_url() -> str:
    """前端访问入口（URL 前缀，供启动提示/调试文档使用）：
    - dev：vite dev server（HMR 热改，/api 已由 vite proxy 反代到本后端端口）
    - prod：空 = 后端同源托管 frontend/dist（壳内呈现，外部零浏览器窗口）
    """
    return f"http://127.0.0.1:{FRONTEND_DEV_PORT}" if is_dev() else ""


# vite dev server 固定端口（frontend/vite.config.ts server.port；与后端 front_port 无关）
FRONTEND_DEV_PORT = 5173


# ---------------------------------------------------------------------------
# 账号注册表（accounts.json）—— 多账户引擎池数据源
# ---------------------------------------------------------------------------
def _accounts_data() -> dict:
    """原始注册表 {accounts: {id: {...}}, active: id}。"""
    return _read_json(ACCOUNTS_FILE)


def list_accounts() -> dict:
    """账号列表：{account_id: {role, user_data_dir, status, ...}}。"""
    return _accounts_data().get("accounts", {})


def active_account() -> str:
    """当前激活账号（默认 default）。"""
    data = _accounts_data()
    return data.get("active") or "default"


def default_user_data_dir() -> str:
    """default 账号的 user_data_dir：XHS_PROFILE_DIR > 旧 cdp_config > 默认目录。

    R 批（v1.4）归一：默认 = 数据目录内 profile（打包态 ~/Library/Application Support/xhs-leads/data/profile，
    开发态 ROOT/data/profile）；历史 ~/.xhs-leads-profile 数据保留不动（登录态已失效，R 批重扫走新目录）。
    """
    env = os.environ.get("XHS_PROFILE_DIR")
    if env:
        return expand(env)
    old = _read_json(DATA_DIR / "cdp_config.json").get("profile_dir")
    if old:
        return expand(old)
    return str(DATA_DIR / "profile")


def get_account(account_id: str) -> dict | None:
    """读单个账号（不存在返回 None）。default 账号缺失时按默认目录补读。"""
    acc = list_accounts().get(account_id)
    if acc:
        return acc
    if account_id == "default":
        return {
            "role": "mixed",
            "user_data_dir": home_relative(default_user_data_dir()),
            "status": "ok",
            "cookie_expires_at": None,
        }
    return None


def add_account(account_id: str, role: str = "mixed",
                user_data_dir: str = "", status: str = "ok") -> dict:
    """新增账号。user_data_dir 缺省 = ~/.xhs-leads/accounts/<account_id>（~ 相对）。

    role：collect（采集号）/ publish（发布号）/ mixed（通用）。
    """
    if not account_id or account_id.strip() in ("", "."):
        raise ValueError("account_id 不能为空")
    account_id = account_id.strip()
    if not user_data_dir:
        user_data_dir = home_relative(os.path.join(os.path.expanduser(ACCOUNTS_ROOT), account_id))
    data = _accounts_data()
    data.setdefault("accounts", {})
    data["accounts"][account_id] = {
        "role": role,
        "user_data_dir": user_data_dir,
        "status": status,
        "cookie_expires_at": None,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _write_json(ACCOUNTS_FILE, data)
    return data["accounts"][account_id]


def update_account(account_id: str, **fields) -> dict | None:
    """更新账号字段（role / user_data_dir / status / cookie_expires_at ...）。"""
    data = _accounts_data()
    acc = data.setdefault("accounts", {}).get(account_id)
    if acc is None:
        return None
    for k, v in fields.items():
        if v is not None:
            acc[k] = v
    acc["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _write_json(ACCOUNTS_FILE, data)
    return acc


def remove_account(account_id: str) -> bool:
    """删除账号（default 不可删）。"""
    if account_id == "default":
        return False
    data = _accounts_data()
    accounts = data.get("accounts", {})
    if account_id not in accounts:
        return False
    del accounts[account_id]
    if data.get("active") == account_id:
        data["active"] = "default"
    _write_json(ACCOUNTS_FILE, data)
    return True


def set_active(account_id: str) -> bool:
    """切换激活账号。"""
    if account_id not in list_accounts():
        return False
    data = _accounts_data()
    data["active"] = account_id
    _write_json(ACCOUNTS_FILE, data)
    return True


def new_account_id(prefix: str = "acc") -> str:
    """生成新账号 id（避免与现有冲突）。"""
    accounts = list_accounts()
    for _ in range(50):
        aid = f"{prefix}_{uuid.uuid4().hex[:6]}"
        if aid not in accounts:
            return aid
    return f"{prefix}_{int(time.time())}"
