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

# 2) 内置引擎 CFT（Chrome for Testing）——从引擎数据目录同步到构建源，随包分发
# （客户机器无需安装 Chrome；CFT 独立 bundle ID，不占用用户日常 Chrome）
echo ">> 同步内置引擎 CFT → build_resources/engine"
mkdir -p build_resources/engine
rsync -a --delete "$HOME/Library/Application Support/xhs-leads/engine/chrome-mac-x64/" build_resources/engine/chrome-mac-x64/

# 3) PyInstaller 打包 .app
echo ">> PyInstaller 打包 .app"
backend/.venv/bin/python -m PyInstaller --noconfirm --clean scripts/packaging/xhs_leads.spec

# 4) post-copy：内置引擎 CFT 直拷进 .app（PyInstaller datas 无法处理签名 app bundle
# 二进制，故打包完成后绕过其管线直接拷入 Resources/engine/）
echo ">> 内置引擎 CFT 写入 .app"
mkdir -p dist/XHSLeads.app/Contents/Resources/engine
rsync -a --delete build_resources/engine/ dist/XHSLeads.app/Contents/Resources/engine/

echo ">> 产物: dist/XHSLeads.app"
ls -d dist/XHSLeads.app
