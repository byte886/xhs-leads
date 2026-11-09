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
import re
import shutil
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
# 多账户统一根目录（R-6.59）：<数据目录>/accounts，注册表存相对路径。
# 跨平台由 DATA_DIR 决定（macOS 打包态在 Application Support，Windows 打包态在 %APPDATA%），
# 搬数据目录或换平台都不用改注册表。
ACCOUNTS_DIR_NAME = "accounts"
# 历史多账户根目录：早期账号落在这里，仅保留读取与进程扫描兼容，不再作为新账号默认位置。
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


def accounts_dir() -> pathlib.Path:
    """账号 profile 统一根：<数据目录>/accounts（跨平台随 DATA_DIR 走）。"""
    return DATA_DIR / ACCOUNTS_DIR_NAME


def resolve_account_dir(raw: str | None) -> str:
    """注册表里的 user_data_dir → 绝对路径（空值返回空串）。

    兼容三种写法，新账号一律用第三种：
    - 绝对路径（/Users/... 或 C:\\Users\\...）：原样返回 —— 自定义位置、老账号
    - ~ 开头：按当前用户家目录展开 —— 早期写法（~/.xhs-leads/accounts/x）
    - 其他相对路径：相对数据目录解析 —— 新写法（accounts/x），搬目录/换平台不用改注册表
    """
    if not raw:
        return ""
    if raw.startswith("~"):
        return os.path.expanduser(raw)
    if os.path.isabs(raw):
        return raw
    return str(DATA_DIR / raw)


# ---------------------------------------------------------------------------
# 通用配置（config.json）：引擎/端口/行为参数
# ---------------------------------------------------------------------------
_DEFAULTS = {
    "engine_port": 9223,          # 引擎调试端口（XHS_PROFILE_PORT 可覆盖）
    "front_port": 8791,           # 前端服务端口（uvicorn 监听；XHS_FRONT_PORT 可覆盖）
    "auto_front_port": True,      # front_port 被占用时自动探测空闲端口
    "browser_mode": "engine",     # engine=独立引擎（B 方案）/ daily=连日常 Chrome（调试遗留）
    "headless": False,            # R-6.16 路线 A：壳=CFT 引擎窗口（CDP 可控），常态有头；engine_window 可显式切
    "collect_tab_visible": False, # R-6.18 壳窗口双 Tab：采集过程是否在壳窗口 采集窗 显示。
                                  #   True=采集 tab 前置可见（调试/演示）；False=采集后台完成、
                                  #   不抢前台（设置页「关闭采集 Tab」开关，口径：平时关）。
    "collect_min_interval": 90.0, # 废弃（R-6.15 移除任务间节流；保留字段兼容旧配置）
    "request_interval": [1.0, 1.8],  # 拟人节奏随机区间
    "collect_group_enabled": True,  # R-6.84 采集窗标签分组：把采集调试窗的标签页
                                    #   收进原生标签组「采集窗口」（名字+颜色由内置扩展设）。
                                    #   扩展目录缺失时自动跳过，行为与关闭一致。
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


# ---------------------------------------------------------------------------
# R-6.57 多账号调试端口分配
#
# 实测（2026-11-08，两个 persistent context 同带 --remote-debugging-port=9223）：
# 第二个实例启动成功、页面正常，但 9223 上只有第一个账号的 target——Chromium
# 绑定端口失败时静默跳过，不报错。后果是所有账号共享 9223，测试 connect_over_cdp
# 连到的永远是先启动的那个账号，登录态与数据串号。
# 故每账号必须独占一个端口：default 固定沿用 engine_port()（36 个 UI 测试硬编码
# 依赖 9223），其余账号从 9224 起分配并持久化到 accounts.json，重启不变。
# ---------------------------------------------------------------------------
ACCOUNT_PORT_BASE = 9224
ACCOUNT_PORT_MAX = 9249


def _port_claimed_by_other(port: int, account_id: str) -> bool:
    """该端口是否已被别的账号登记占用。"""
    for aid, acc in list_accounts().items():
        if aid != account_id and acc.get("debug_port") == port:
            return True
    return False


def _alloc_account_port(account_id: str) -> int:
    """从 ACCOUNT_PORT_BASE 起找第一个未被其他账号登记的端口。"""
    for p in range(ACCOUNT_PORT_BASE, ACCOUNT_PORT_MAX + 1):
        if not _port_claimed_by_other(p, account_id):
            return p
    raise RuntimeError(f"账号 {account_id} 无可用调试端口（{ACCOUNT_PORT_BASE}-{ACCOUNT_PORT_MAX} 已排满）")


def account_debug_port(account_id: str) -> int:
    """该账号独占的 CDP 调试端口。

    default：engine_port()（环境变量 XHS_PROFILE_PORT / config.engine_port 可覆盖）。
    其他：accounts.json 的 debug_port；缺失或与他号冲突则重新分配并回写。

    注意：端口已被本账号引擎监听时不再"探测空闲"改号——那会导致每次启动端口漂移，
    测试连错。只判断"是否被别的账号登记占用"。
    """
    if not account_id or account_id == "default":
        return engine_port()
    acc = list_accounts().get(account_id) or {}
    saved = acc.get("debug_port")
    if isinstance(saved, int) and ACCOUNT_PORT_BASE <= saved <= ACCOUNT_PORT_MAX:
        if not _port_claimed_by_other(saved, account_id):
            return saved
    port = _alloc_account_port(account_id)
    update_account(account_id, debug_port=port)
    return port


def account_port_map() -> dict:
    """全部账号 → 调试端口（含 default），供 HTTP 接口与测试脚本使用。"""
    result = {"default": engine_port()}
    for aid in list_accounts():
        if aid != "default":
            result[aid] = account_debug_port(aid)
    return result



def front_port() -> int:
    """前端服务端口：XHS_FRONT_PORT > XHS_SHELL_URL（实际监听，launcher 设定）
    > config.front_port（auto 时探测空闲）。

    R-6.22j 修复：原来每次调用 find_free_port(preferred) 重新探测「空闲端口」——
    启动时 8791 空闲则返回 8791（uvicorn 监听 8791）；之后再次调用时 8791 已被
    占用，探测跳过它返回 8800 → 与真实监听端口错位 → cleanup_tabs 的 keep_url
    不匹配壳窗实际 URL → 登录引导时误关壳窗（用户实测「点采集后被引入空白页」）。
    现以 launcher 写入的 XHS_SHELL_URL（uvicorn 真实监听）为权威，进程内稳定一致。
    """
    env = os.environ.get("XHS_FRONT_PORT")
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    sh = os.environ.get("XHS_SHELL_URL") or ""
    if sh:
        try:
            from urllib.parse import urlparse  # noqa: PLC0415

            return urlparse(sh).port
        except Exception:  # noqa: BLE001
            pass
    cfg = get_config()
    preferred = int(cfg.get("front_port", 8791))
    if cfg.get("auto_front_port", True):
        return find_free_port(preferred)
    return preferred

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
    """default 账号的 user_data_dir：XHS_PROFILE_DIR > 旧 cdp_config > accounts/default（相对数据目录）。

    R-6.59 归一：所有账号 profile 统一放 <数据目录>/accounts/<账号名>，default 不再特殊；
    历史 data/profile 由迁移搬到 accounts/default，登录态随目录一起走。
    """
    env = os.environ.get("XHS_PROFILE_DIR")
    if env:
        return expand(env)
    old = _read_json(DATA_DIR / "cdp_config.json").get("profile_dir")
    if old:
        return expand(old)
    return f"{ACCOUNTS_DIR_NAME}/default"


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


# ---------------------------------------------------------------------------
# 采集定位（同城 / 附近筛选）
# ---------------------------------------------------------------------------
# R-6.81（真机实测）：平台「同城 / 附近」筛选依赖 navigator.geolocation；CFT 是无
# Google API key 的构建，官方文档写明 geolocation features 不会工作 —— 即使权限
# 全开也只会等到 code:3（Timeout expired），平台随即静默退回「不限」。所以不能
# 指望系统定位，必须在启动时注入固定坐标（Playwright geolocation + permissions）。
# 坐标默认取运营地（襄阳樊城）；账号可用 accounts.json 的 geolocation 字段覆盖，
# 也可用 XHS_GEO_LAT / XHS_GEO_LNG 环境变量临时指定。
DEFAULT_GEOLOCATION = {"latitude": 32.042, "longitude": 112.144, "accuracy": 50.0}


def _geo_from(raw: dict) -> dict | None:
    """把 {latitude, longitude, accuracy?} 规范成 Playwright Geolocation。

    缺经纬度、非数字、或超出合法范围时返回 None（由调用方回退下一来源）。
    """
    try:
        lat = float(raw.get("latitude"))
        lng = float(raw.get("longitude"))
    except (TypeError, ValueError, AttributeError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    try:
        acc = float(raw.get("accuracy") or DEFAULT_GEOLOCATION["accuracy"])
    except (TypeError, ValueError):
        acc = DEFAULT_GEOLOCATION["accuracy"]
    return {"latitude": lat, "longitude": lng, "accuracy": acc}


def account_geolocation(account_id: str = "default") -> dict:
    """该账号的定位坐标：accounts.json 的 geolocation > 环境变量 > 默认运营地。"""
    acc = get_account(account_id) or {}
    configured = acc.get("geolocation")
    if isinstance(configured, dict):
        parsed = _geo_from(configured)
        if parsed:
            return parsed
    env = {"latitude": os.environ.get("XHS_GEO_LAT"), "longitude": os.environ.get("XHS_GEO_LNG")}
    if env["latitude"] and env["longitude"]:
        parsed = _geo_from(env)
        if parsed:
            return parsed
    return dict(DEFAULT_GEOLOCATION)


# 账号 id 同时是目录名，必须跨平台安全：小写字母/数字/下划线/短横线，不以符号开头，≤32 字符。
# 排除中文（路径编码）、空格、点、盘符与路径分隔符（Windows 非法字符）。
_ACCOUNT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


# 账号业务名：给人看的名字（如「珠宝主号」「二号店·翡翠」），可中文可空格，可随时改。
# 与 account_id 分层的原因：id 是目录名有跨平台字符约束，业务名是纯展示字段不落路径——
# 否则用户想改成中文名就得搬 80M 的 profile 目录，还会丢登录态。
ACCOUNT_NAME_MAX = 24
# 业务名只禁控制字符（换行/制表）与路径分隔符（防拼接路径时被误用），不落路径故不必限 ASCII。
_ACCOUNT_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f/\\]{1,%d}$" % ACCOUNT_NAME_MAX)


def valid_account_name(name: str) -> bool:
    """账号业务名是否可用：≤24 字符，禁控制字符与路径分隔符。"""
    return bool(_ACCOUNT_NAME_RE.match((name or "").strip()))


def valid_account_id(account_id: str) -> bool:
    """账号 id 是否可安全用作目录名（macOS/Windows 通用）。"""
    return bool(_ACCOUNT_ID_RE.match(account_id or ""))


def add_account(account_id: str, role: str = "mixed",
                user_data_dir: str = "", status: str = "ok",
                name: str = "") -> dict:
    """新增账号。user_data_dir 缺省 = accounts/<account_id>（相对数据目录，搬目录/换平台不用改）。

    role：collect（采集号）/ publish（发布号）/ mixed（通用）。
    name：业务展示名（可中文），留空则界面显示 account_id，不编造业务含义。
    """
    if not account_id or account_id.strip() in ("", "."):
        raise ValueError("account_id 不能为空")
    account_id = account_id.strip()
    if not valid_account_id(account_id):
        raise ValueError(
            f"账号 id 只能用 小写字母/数字/下划线/短横线（≤32 字符，不能中文或空格）：{account_id!r}")
    name = (name or "").strip()
    if name and not valid_account_name(name):
        raise ValueError(
            f"账号名最多 {ACCOUNT_NAME_MAX} 字符，不能含控制字符或路径分隔符：{name!r}")
    if not user_data_dir:
        user_data_dir = f"{ACCOUNTS_DIR_NAME}/{account_id}"
    data = _accounts_data()
    data.setdefault("accounts", {})
    data["accounts"][account_id] = {
        "role": role,
        "name": name,
        "nickname": "",
        "red_id": "",
        "platform_uid": "",
        "user_data_dir": user_data_dir,
        "status": status,
        "extension_enabled": False,
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


def account_name(account_id: str) -> str:
    """账号业务名：注册表 name 字段；没设过回退 account_id（不编造业务含义）。"""
    acc = get_account(account_id) or {}
    return (acc.get("name") or "").strip() or account_id


def set_account_name(account_id: str, name: str) -> bool:
    """改业务名：只动展示字段，不搬目录、不动登录态。空名 = 清除自定义名。

    账号不在注册表里返回 False；名字不合规返回 False（由调用方回给用户）。
    """
    acc = get_account(account_id)
    if acc is None:
        return False
    name = (name or "").strip()
    if name and not valid_account_name(name):
        return False
    if update_account(account_id, name=name) is None:
        # default 等历史账号可能尚未写进注册表：先补一条再写业务名
        add_account(account_id, role=acc.get("role", "mixed"),
                    user_data_dir=acc.get("user_data_dir", ""),
                    status=acc.get("status", "ok"))
        update_account(account_id, name=name)
    return True


def account_nickname(account_id: str) -> str:
    """平台昵称（登录事件由 user/me 顺带写入；未登录/无缓存为空）。"""
    return ((get_account(account_id) or {}).get("nickname") or "").strip()


def set_account_nickname(account_id: str, nickname: str) -> bool:
    """缓存平台昵称：登录成功那一刻顺手落盘，账号列表零额外请求即可显示。"""
    if get_account(account_id) is None:
        return False
    return update_account(account_id, nickname=(nickname or "").strip()) is not None


def account_red_id(account_id: str) -> str:
    """小红书号（user/me 的 ``red_id``，如 123456789）。

    这是用户在 App「我」页面能直接看到的号，**唯一且稳定**，用来核对“这个号是谁”。
    """
    return str((get_account(account_id) or {}).get("red_id") or "").strip()


def set_account_red_id(account_id: str, red_id: str) -> bool:
    """缓存小红书号（登录事件或手动识别时写入）。"""
    if get_account(account_id) is None:
        return False
    return update_account(account_id, red_id=str(red_id or "").strip()) is not None


def account_platform_uid(account_id: str) -> str:
    """平台内部 user_id（24 位 hex）。平台内唯一但用户在界面上看不到，
    仅作 red_id 缺失时的兜底标识，不用于展示主名称。"""
    return str((get_account(account_id) or {}).get("platform_uid") or "").strip()


def set_account_platform_uid(account_id: str, uid: str) -> bool:
    if get_account(account_id) is None:
        return False
    return update_account(account_id, platform_uid=str(uid or "").strip()) is not None


def account_display_name(account_id: str) -> str:
    """界面主名称 —— 全部自动派生，不需要人工起名（用户 R-6.69b 拍板）。

    优先级：**平台昵称 → 小红书号 → 内部 id**。昵称是平台给的（会变），
    red_id 稳定唯一（用户能核对），account_id 是自动生成的唯一符（如 acc_3f2a91），
    三者都来自平台或系统，不需要人手工填。
    人工备注（name）按用户要求**不参与主名称**，只在界面上作后缀补充用途。
    """
    nick = account_nickname(account_id)
    if nick:
        return nick
    red = account_red_id(account_id)
    if red:
        return f"@{red}"
    return account_id


def account_headless(account_id: str = "default") -> bool:
    """该账号是否以无头启动（R-6.58）。

    优先读账号自己的 headless 字段；从未设置过才回退全局 config.headless。
    为什么不用全局：切某个账号的无头会顺手把全局改成 true，default 下次重启就
    没窗口了——多账号下每个账号的窗口形态必须各管各的。
    """
    acc = get_account(account_id) or {}
    v = acc.get("headless")
    return bool(v) if isinstance(v, bool) else bool(get_config().get("headless", True))


def set_account_headless(account_id: str, headless: bool) -> bool:
    """只改该账号的无头设置，不动全局默认值；账号不存在返回 False。"""
    acc = get_account(account_id)
    if acc is None:
        return False
    if update_account(account_id, headless=bool(headless)) is None:
        add_account(account_id, role=acc.get("role", "mixed"),
                    user_data_dir=acc.get("user_data_dir", ""),
                    status=acc.get("status", "ok"))
        update_account(account_id, headless=bool(headless))
    return True


def account_auto_headless(account_id: str) -> bool:
    """该账号是否「登录完成后自动切无头」（R-6.64）。默认 false。"""
    return bool((get_account(account_id) or {}).get("auto_headless"))


def set_account_auto_headless(account_id: str, on: bool) -> bool:
    """设置/清除「登录后自动切无头」标记；账号不存在返回 False。"""
    if get_account(account_id) is None:
        return False
    update_account(account_id, auto_headless=bool(on))
    return True


def collect_group_enabled() -> bool:
    """采集窗标签分组开关（R-6.84）：全局、默认开。

    只影响「加载内置扩展」这一步；扩展目录不存在时 engine_pool 会静默跳过，
    因此老配置、未打包环境下的行为与关闭时一致，无需迁移字段。
    """
    return bool(get_config().get("collect_group_enabled", True))


def extension_enabled(account_id: str) -> bool:
    """该账号启动时是否自动加载未打包扩展（R-6.70）。默认 False——开关不打开，
    现有账号、窗口与测试行为零变化。老账号缺该字段时按 False 处理，无需迁移。"""
    return bool((get_account(account_id) or {}).get("extension_enabled"))


def set_extension_enabled(account_id: str, on: bool) -> bool:
    """设置/清除该账号的扩展自动加载标记；账号不存在返回 False。"""
    if get_account(account_id) is None:
        return False
    update_account(account_id, extension_enabled=bool(on))
    return True


def retire_account_dir(account_id: str) -> str:
    """删除账号时把 profile 移进 accounts/_trash/，返回移入后的路径（无目录返回空串）。

    为什么不直接删：profile 里存着登录态，误删一次就得重新扫码。移进回收区可回滚，
    也避免"只删注册表"留下孤儿目录（之前清理的那批 acc_test1/verify_b 就是这么来的）。
    """
    if account_id == "default" or not valid_account_id(account_id):
        return ""
    acc = get_account(account_id) or {}
    # 用注册表里记的 user_data_dir（accounts/<id> 之类的相对路径），
    # 不能直接拿 account_id 去 resolve——那会落到数据目录根下而不是 accounts/ 里
    udd = acc.get("user_data_dir") or f"{ACCOUNTS_DIR_NAME}/{account_id}"
    src = pathlib.Path(resolve_account_dir(udd))
    if not src.is_dir():
        return ""
    trash = accounts_dir() / "_trash"
    trash.mkdir(parents=True, exist_ok=True)
    dst = trash / f"{account_id}-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.move(str(src), str(dst))
    return str(dst)


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
