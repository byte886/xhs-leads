#!/usr/bin/env bash
# 一键打包 macOS 壳（B 批）
#
# 用法：bash scripts/packaging/build_mac.sh [选项]
#   --no-frontend-build     复用现有 frontend/dist，不重建前端
#   --format <F>            产物形态，默认 all
#                             dir   只留 dist/XHSLeads.app（绿色目录，开发自查）
#                             zip   只出 XHSLeads-mac-Intel.zip（绿色免安装）
#                             dmg   只出 XHSLeads-mac-Intel.dmg（安装镜像：拖进「应用程序」）
#                             all   zip + dmg 都出
#   -h, --help              显示本说明
set -euo pipefail
cd "$(dirname "$0")/../.."

FORMAT="all"
SKIP_FRONTEND=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-frontend-build) SKIP_FRONTEND=1; shift ;;
    --format)
      [[ $# -ge 2 ]] || { echo "--format 需要值：dir|zip|dmg|all" >&2; exit 2; }
      FORMAT="$2"; shift 2 ;;
    --format=*) FORMAT="${1#*=}"; shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "未知参数：$1（可用 --no-frontend-build / --format dir|zip|dmg|all）" >&2; exit 2 ;;
  esac
done
case "$FORMAT" in
  dir|zip|dmg|all) ;;
  *) echo "--format 只接受 dir|zip|dmg|all（收到：$FORMAT）" >&2; exit 2 ;;
esac
echo ">> 产物形态：$FORMAT"

# 1) 前端构建产物（frontend/dist）
if [[ "$SKIP_FRONTEND" -eq 0 ]]; then
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

# 2.1) 品牌图标：从设计资产生成 icns，同时用于 .app 与内置引擎 CFT 的 Dock/窗口图标
echo ">> 生成品牌图标 icns（build_resources/icon/XHSLeadsIcon.icns）"
ICON_PNG="build_resources/icon/XHSLeadsIcon-1024.png"
ICONSET="$(mktemp -d)/xhs_icon.iconset"
mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z "$s" "$s" "$ICON_PNG" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z "$((s*2))" "$((s*2))" "$ICON_PNG" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
sips -z 1024 1024 "$ICON_PNG" --out "$ICONSET/icon_512x512@2x.png" >/dev/null
iconutil -c icns "$ICONSET" -o build_resources/icon/XHSLeadsIcon.icns
rm -rf "$(dirname "$ICONSET")"

# 3) PyInstaller 打包 .app
echo ">> PyInstaller 打包 .app"
backend/.venv/bin/python -m PyInstaller --noconfirm --clean scripts/packaging/xhs_leads.spec

# 4) post-copy：内置引擎 CFT 直拷进 .app（PyInstaller datas 无法处理签名 app bundle
# 二进制，故打包完成后绕过其管线直接拷入 Resources/engine/）
echo ">> 内置引擎 CFT 写入 .app"
mkdir -p dist/XHSLeads.app/Contents/Resources/engine
rsync -a --delete build_resources/engine/ dist/XHSLeads.app/Contents/Resources/engine/

# 4.1) 引擎 CFT 应用图标替换为品牌图标（Dock/窗口图标）
echo ">> 内置引擎 CFT 图标替换为品牌图标"
cp build_resources/icon/XHSLeadsIcon.icns \
  dist/XHSLeads.app/Contents/Resources/engine/chrome-mac-x64/Google\ Chrome\ for\ Testing.app/Contents/Resources/app.icns

echo ">> 产物: dist/XHSLeads.app"
ls -d dist/XHSLeads.app

# 4.2) 产物自检：随包资产（扩展三件套/前端/引擎）缺一项就判打包失败。
# 历史上只拷 extension/lib 漏了 manifest.json，打包版因此静默没了「采集窗口」标签组。
echo ">> 产物资产自检"
backend/.venv/bin/python scripts/packaging/check_pack.py dist/XHSLeads.app

# 4.3) ad-hoc 重签：必须放在所有文件改动之后。
# PyInstaller 产出的 .app 自带 ad-hoc 签名，但步骤 4/4.1 往 bundle 里拷引擎、换图标，
# 签名清单随即失效（spctl 报 "a sealed resource is missing or invalid"）。签名失效时
# macOS 判「已损坏，无法打开」，系统设置里的「仍要打开」是灰的 —— 收件人只能靠命令行
# xattr -d com.apple.quarantine 才能打开，这正是实际踩到的坑。
# 重签后 Gatekeeper 报「无法验证开发者」，用户在系统设置点一次「仍要打开」即可，无需命令行。
# 注：codesign 会连嵌套的 CFT 一并转 ad-hoc（实测不影响引擎运行、采集窗照常创建）。
echo ">> ad-hoc 重签（让签名清单与最终文件对齐）"
codesign --force --sign - dist/XHSLeads.app
codesign --verify --deep --strict dist/XHSLeads.app && echo "   签名校验通过"

# 5) 分发件：zip（绿色免安装）与 dmg（安装镜像）
if [[ "$FORMAT" == "zip" || "$FORMAT" == "all" ]]; then
  ZIP_OUT="${HOME}/Desktop/XHSLeads-mac-Intel.zip"
  echo ">> 压缩绿色包 zip → ${ZIP_OUT}"
  rm -f "${ZIP_OUT}"
  zip -ry -q "${ZIP_OUT}" dist/XHSLeads.app
  ls -lh "${ZIP_OUT}"
fi

if [[ "$FORMAT" == "dmg" || "$FORMAT" == "all" ]]; then
  DMG_OUT="${HOME}/Desktop/XHSLeads-mac-Intel.dmg"
  echo ">> 制作安装镜像 dmg → ${DMG_OUT}"
  STAGE="$(mktemp -d)"
  # ditto 而非 cp：保留符号链接/扩展属性，避免破坏 .app 内的签名与框架链接
  ditto dist/XHSLeads.app "$STAGE/XHSLeads.app"
  ln -s /Applications "$STAGE/Applications"   # 镜像里给「应用程序」快捷方式，拖进去即安装
  rm -f "${DMG_OUT}"
  hdiutil create -volname "XHS Leads 小红书获客助手" -srcfolder "$STAGE" \
    -ov -format UDZO -quiet "${DMG_OUT}"
  rm -rf "$STAGE"
  echo ">> 校验镜像可挂载"
  hdiutil verify "${DMG_OUT}" | tail -1
  ls -lh "${DMG_OUT}"
fi

echo ">> 完成（形态 ${FORMAT}）"
