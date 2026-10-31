#!/usr/bin/env bash
# 一键打包 macOS .app（B 批壳）
# 用法：bash scripts/packaging/build_mac.sh [--no-frontend-build]
set -euo pipefail
cd "$(dirname "$0")/../.."

# 1) 前端构建产物（frontend/dist）——默认先 build；--no-frontend-build 可跳过（仅重打壳）
if [[ "${1:-}" != "--no-frontend-build" ]]; then
  echo ">> 构建前端 (Vite → frontend/dist)"
  (cd frontend && npm run build)
else
  echo ">> 跳过前端构建（复用现有 frontend/dist）"
fi

# 2) PyInstaller 打包 .app
echo ">> PyInstaller 打包 .app"
backend/.venv/bin/python -m PyInstaller --noconfirm --clean scripts/packaging/xhs_leads.spec

echo ">> 产物: dist/XHSLeads.app"
ls -d dist/XHSLeads.app
