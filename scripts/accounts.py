# -*- coding: utf-8 -*-
"""多账号注册表管理（R-6.57）。

每个账号 = 一个独立 user_data_dir（独立登录态）+ 一个独占 CDP 调试端口。
本脚本是账号管理的命令行入口；前端账号管理 UI 另行排期。

用法：
    backend/.venv/bin/python scripts/accounts.py list
    backend/.venv/bin/python scripts/accounts.py list --data-dir "$HOME/Library/Application Support/xhs-leads/data"
    backend/.venv/bin/python scripts/accounts.py add work_a --role collect
    backend/.venv/bin/python scripts/accounts.py port work_a
    backend/.venv/bin/python scripts/accounts.py remove work_a

注意：--data-dir 必须与实际运行的后端一致，否则看的是另一份 accounts.json。
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def _preparse_data_dir():
    """在 import config 之前把 --data-dir 注入环境变量（config 在 import 时定 DATA_DIR）。

    支持任意位置：`list --data-dir X` / `--data-dir X list` / `--data-dir=X list`。
    """
    argv = sys.argv
    val = ""
    i = 1
    while i < len(argv):
        if argv[i] == "--data-dir" and i + 1 < len(argv):
            val = argv[i + 1]
            del argv[i:i + 2]
            break
        if argv[i].startswith("--data-dir="):
            val = argv[i].split("=", 1)[1]
            del argv[i]
            break
        i += 1
    if val:
        os.environ["XHS_LEADS_DATA"] = os.path.expanduser(val)


_preparse_data_dir()

from src import config  # noqa: E402


def cmd_list(args):
    port_map = config.account_port_map()
    accounts = config.list_accounts()
    if not accounts:
        print("（注册表为空，仅有内置 default）")
    print(f"数据目录: {config.DATA_DIR}")
    # default 的 profile 以注册表登记值为准（引擎实际用它）；缺登记才回退默认目录
    default_dir = (accounts.get("default") or {}).get("user_data_dir") \
        or config.home_relative(config.default_user_data_dir())
    print(f"{'账号':<16}{'角色':<10}{'端口':<8}{'状态':<8}profile（相对数据目录）")
    print("-" * 90)
    print(f"{'default':<16}{'mixed':<10}{port_map.get('default', '-'):<8}{'ok':<8}{default_dir}")
    for aid, acc in accounts.items():
        if aid == "default":
            continue
        print(f"{aid:<16}{acc.get('role', 'mixed'):<10}{port_map.get(aid, '-'):<8}"
              f"{acc.get('status', 'ok'):<8}{acc.get('user_data_dir', '')}")
    print(f"\n激活账号: {config.active_account()}")


def cmd_add(args):
    if config.get_account(args.account_id) and args.account_id in config.list_accounts():
        print(f"账号已存在: {args.account_id}")
        return 1
    acc = config.add_account(args.account_id, role=args.role)
    port = config.account_debug_port(args.account_id)
    print(f"已创建账号 {args.account_id}")
    print(f"  profile: {config.resolve_account_dir(acc.get('user_data_dir'))}")
    print(f"  CDP 端口: {port}")
    print("  下一步：启动后端后打开该账号窗口扫码登录")
    return 0


def cmd_remove(args):
    if args.account_id == "default":
        print("default 账号不可删除")
        return 1
    acc = config.get_account(args.account_id)
    if acc is None:
        print(f"账号不存在: {args.account_id}")
        return 1
    if not config.remove_account(args.account_id):
        print("删除失败")
        return 1
    print(f"已从注册表移除 {args.account_id}")
    print(f"  profile 目录保留（不自动删）: {config.resolve_account_dir(acc.get('user_data_dir'))}")
    return 0


def cmd_port(args):
    print(config.account_debug_port(args.account_id))
    return 0


def main():
    ap = argparse.ArgumentParser(description="多账号注册表管理")
    ap.add_argument("--data-dir", default="", help="数据目录（须与运行中的后端一致）")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list", help="列出账号与端口")
    p_add = sub.add_parser("add", help="新增账号")
    p_add.add_argument("account_id")
    p_add.add_argument("--role", default="mixed", choices=["collect", "publish", "mixed"])
    p_rm = sub.add_parser("remove", help="移除账号")
    p_rm.add_argument("account_id")
    p_port = sub.add_parser("port", help="打印账号调试端口")
    p_port.add_argument("account_id")

    args = ap.parse_args()
    handlers = {"list": cmd_list, "add": cmd_add, "remove": cmd_remove, "port": cmd_port}
    if args.cmd not in handlers:
        ap.print_help()
        return 0
    return handlers[args.cmd](args) or 0


if __name__ == "__main__":
    sys.exit(main())
