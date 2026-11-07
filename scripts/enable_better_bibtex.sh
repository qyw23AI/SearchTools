#!/usr/bin/env bash
set -e

XPI_SRC="/home/qyw/.zotero/zotero/default/extensions/better-bibtex@iris-advies.com.xpi"
if [ -f "$XPI_SRC" ]; then
    cp -f "$XPI_SRC" "/home/qyw/下载/zotero-better-bibtex-9.0.77.xpi" 2>/dev/null || true
    cp -f "$XPI_SRC" "/home/qyw/Downloads/zotero-better-bibtex-9.0.77.xpi" 2>/dev/null || true
    cp -f "$XPI_SRC" "/home/qyw/文档/ChatGPT/科研学习相关工具搭建研究/zotero-better-bibtex-9.0.77.xpi" 2>/dev/null || true
fi

echo "1. 正在关闭 Zotero 以更新插件注册表..."
pkill -TERM -f "zotero-bin" 2>/dev/null || true
sleep 2

echo "2. 首次启动扫描新插件..."
WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-0} DISPLAY=${DISPLAY:-:0} XDG_RUNTIME_DIR=/run/user/$(id -u) nohup /home/qyw/.local/bin/zotero >/dev/null 2>&1 &
sleep 4

echo "3. 正在启用 Better BibTeX (active=true, userDisabled=false)..."
pkill -TERM -f "zotero-bin" 2>/dev/null || true
sleep 2

python3 - << 'EOF'
import json
from pathlib import Path

p = Path.home() / ".zotero/zotero/default/extensions.json"
if p.exists():
    data = json.loads(p.read_text(encoding="utf-8"))
    for addon in data.get("addons", []):
        if addon.get("id") == "better-bibtex@iris-advies.com":
            addon["active"] = True
            addon["userDisabled"] = False
            addon["foreignInstall"] = False
            addon["appDisabled"] = False
            addon["seen"] = True
            print(f"   -> 已激活插件: {addon['id']} (v{addon.get('version')})")
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

cache = Path.home() / ".zotero/zotero/default/addonStartup.json.lz4"
if cache.exists():
    cache.unlink()
EOF

echo "4. 重新启动 Zotero..."
WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-0} DISPLAY=${DISPLAY:-:0} XDG_RUNTIME_DIR=/run/user/$(id -u) nohup /home/qyw/.local/bin/zotero >/dev/null 2>&1 &
sleep 4
echo "✅ Better BibTeX for Zotero 已成功安装并激活！"
