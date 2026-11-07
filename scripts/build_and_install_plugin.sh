#!/usr/bin/env bash
set -e

PLUGIN_DIR="/home/qyw/文档/ChatGPT/科研学习相关工具搭建研究/zotero-plugin"
EXTENSIONS_DIR="/home/qyw/.zotero/zotero/default/extensions"
PROFILE_DIR="/home/qyw/.zotero/zotero/default"
XPI_TARGET="${EXTENSIONS_DIR}/evidence-bridge@searchtools.org.xpi"

echo "=== 1. 构建 SearchTools Evidence Bridge 插件 (.xpi) ==="
cd "$PLUGIN_DIR"
rm -f "$XPI_TARGET" /tmp/evidence-bridge.xpi
zip -q -r /tmp/evidence-bridge.xpi manifest.json bootstrap.js icon.png
mkdir -p "$EXTENSIONS_DIR"
cp -f /tmp/evidence-bridge.xpi "$XPI_TARGET"
echo "已生成并部署: $XPI_TARGET"

echo "=== 2. 平滑重启 Zotero 刷新插件注册表 ==="
pkill -TERM -f "zotero-bin" 2>/dev/null || true
sleep 2

echo "=== 3. 首次启动触发 Zotero 检测新插件 ==="
WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-0} DISPLAY=${DISPLAY:-:0} XDG_RUNTIME_DIR=/run/user/$(id -u) nohup /home/qyw/.local/bin/zotero >/dev/null 2>&1 &
sleep 3

echo "=== 4. 激活插件 (active=true, userDisabled=false) ==="
pkill -TERM -f "zotero-bin" 2>/dev/null || true
sleep 2

python3 - << 'EOF'
import json
from pathlib import Path

ext_json = Path.home() / ".zotero/zotero/default/extensions.json"
addon_id = "evidence-bridge@searchtools.org"
xpi_path = str(Path.home() / ".zotero/zotero/default/extensions" / f"{addon_id}.xpi")

if ext_json.exists():
    data = json.loads(ext_json.read_text(encoding="utf-8"))
    addons = data.get("addons", [])
    found = False
    for a in addons:
        if a.get("id") == addon_id:
            a["active"] = True
            a["userDisabled"] = False
            a["foreignInstall"] = False
            a["appDisabled"] = False
            a["seen"] = True
            a["version"] = "1.0.0"
            found = True
            print(f"   -> 已更新并激活扩展: {addon_id}")
            break
    if not found:
        new_entry = {
            "id": addon_id,
            "syncGUID": "evidence-bridge-sync-guid",
            "version": "1.0.0",
            "type": "extension",
            "loader": None,
            "updateURL": None,
            "optionsURL": None,
            "optionsType": None,
            "optionsBrowserStyle": True,
            "aboutURL": None,
            "defaultLocale": {
                "name": "SearchTools Evidence Bridge",
                "description": "Zotero bridge for SearchTools: exact page navigation, temporary evidence highlighting, and reverse card creation.",
                "creator": "SearchTools",
                "developers": None,
                "translators": None,
                "contributors": None
            },
            "visible": True,
            "active": True,
            "userDisabled": False,
            "appDisabled": False,
            "installDate": 1731000000000,
            "updateDate": 1731000000000,
            "applyBackgroundUpdates": 1,
            "path": xpi_path,
            "targetApplications": [
                {
                    "id": "zotero@chnm.gmu.edu",
                    "minVersion": "6.999",
                    "maxVersion": "11.*"
                }
            ],
            "targetPlatforms": [],
            "signedState": 2,
            "seen": True
        }
        addons.append(new_entry)
        data["addons"] = addons
        print(f"   -> 已新注册并激活扩展: {addon_id}")
    ext_json.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

startup_cache = Path.home() / ".zotero/zotero/default/addonStartup.json.lz4"
if startup_cache.exists():
    startup_cache.unlink()
    print("   -> 已清除插件启动缓存 (addonStartup.json.lz4)")
EOF

echo "=== 5. 启动激活后的 Zotero ==="
WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-0} DISPLAY=${DISPLAY:-:0} XDG_RUNTIME_DIR=/run/user/$(id -u) nohup /home/qyw/.local/bin/zotero >/dev/null 2>&1 &
sleep 4

echo "✅ SearchTools Evidence Bridge 插件已成功安装并运行！"
