#!/usr/bin/env python3
"""Create a spec-compliant zip (forward-slash entry names) of a directory.

Windows PowerShell 5.1's Compress-Archive writes backslash separators, which
violates the zip spec and breaks extraction on macOS/Linux tools. This script
keeps entry names portable and always stores everything under one root folder.

Usage:
    python make_zip.py <src_dir> <out_zip> [root_name]
"""
import os
import sys
import zipfile


def build(src_dir: str, out_zip: str, root_name: str) -> int:
    src_dir = os.path.abspath(src_dir)
    count = 0
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for base, _dirs, files in os.walk(src_dir):
            rel = os.path.relpath(base, src_dir)
            rel_prefix = "" if rel == "." else rel.replace(os.sep, "/") + "/"
            for name in files:
                full = os.path.join(base, name)
                arc = f"{root_name}/{rel_prefix}{name}"
                z.write(full, arc)
                count += 1
    return count


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    root = sys.argv[3] if len(sys.argv) > 3 else os.path.basename(src.rstrip("/\\"))
    if not os.path.isdir(src):
        print(f"source dir missing: {src}")
        return 1
    if os.path.exists(dst):
        os.remove(dst)
    n = build(src, dst, root)
    size_mb = os.path.getsize(dst) / (1024 * 1024)
    print(f"zip: {dst} ({size_mb:.1f} MB, {n} files, root={root}/)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
