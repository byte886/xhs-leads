"""外部 URL 构造与参数检查（R-6.53z6）。

背景：此前登录页 URL 是硬编码常量，且拼上了实测无作用的 `redirectPath`
（`https://www.xiaohongshu.com/login?redirectPath=https%3A%2F%2Fwww.xiaohongshu.com%2F`）；
各处采集 URL 则是裸字符串拼接，参数为空/None 时会把 `None`/`null` 拼进 query。
本模块把「生成 URL 前先做参数检查」固化成唯一入口。

实测结论（未登录态，全新 context）：
- `https://www.xiaohongshu.com/explore` 未登录即可进浏览页（内容流正常）；
- `https://www.xiaohongshu.com/website-login` 跳到 oia.xiaohongshu.com/oia（营销页，不能用）；
- `https://www.xiaohongshu.com/login` 干净可用，不需要任何参数。
"""
from urllib.parse import quote

# 这些值拼进 URL 就是脏数据（历史上 `x=null` 一类后缀的根因）
_ILLEGAL_VALUES = frozenset({"none", "null", "undefined", "nan"})
# 能破坏 URL 结构的字符：换行/制表/引号/尖括号/反斜杠
_ILLEGAL_CHARS = frozenset("\r\n\t\"'<>\\")


def _checked_pair(key: str, raw: object) -> str | None:
    """单参数检查：脏值返回 None（不拼），非法字符直接抛错。"""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in _ILLEGAL_VALUES:
        return None
    if _ILLEGAL_CHARS & set(text):
        raise ValueError(f"URL 参数含非法字符: {key}={text!r}")
    return f"{quote(str(key), safe='')}={quote(text, safe='')}"


def build_url(base: str, path: str = "", params: dict | None = None) -> str:
    """构造外部 URL；只拼合法非空参数，值统一 quote。

    - `None` / 空串 / `"none"` / `"null"` / `"undefined"` 直接跳过，不产生空参数；
    - 含换行、引号、尖括号等破坏 URL 的字符 → 抛 ValueError（宁可报错也不发脏请求）。
    """
    url = f"{base.rstrip('/')}/{path.lstrip('/')}" if path else base.rstrip("/")
    pairs = [p for p in (_checked_pair(k, v) for k, v in (params or {}).items()) if p]
    return f"{url}?{'&'.join(pairs)}" if pairs else url
