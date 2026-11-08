#!/usr/bin/env python3
"""Clean up card and evidence-temp tags from Zotero SQLite database.
"""
import sqlite3
import subprocess
import time
from pathlib import Path

ZOTERO_SQLITE = Path('/home/qyw/Zotero/zotero.sqlite')

def main():
    if not ZOTERO_SQLITE.exists():
        print(f"Zotero DB not found: {ZOTERO_SQLITE}")
        return

    # 1. Gracefully stop Zotero
    print("Stopping Zotero...")
    subprocess.run(["pkill", "-TERM", "-f", "zotero-bin"], capture_output=True)
    time.sleep(2)

    # 2. Open DB and clean tags
    conn = sqlite3.connect(ZOTERO_SQLITE)
    cur = conn.cursor()

    sql_find = "SELECT tagID, name FROM tags WHERE name LIKE '#card-%' OR name LIKE 'card-%' OR name LIKE '#evidence-%' OR name LIKE 'evidence-%'"
    matching = cur.execute(sql_find).fetchall()
    print(f"Found {len(matching)} legacy card/evidence tags in Zotero DB:")
    for t in matching:
        print(f"  [{t[0]}] {t[1]}")

    if matching:
        tag_ids = [t[0] for t in matching]
        placeholders = ','.join('?' for _ in tag_ids)
        cur.execute(f"DELETE FROM itemTags WHERE tagID IN ({placeholders})", tag_ids)
        cur.execute(f"DELETE FROM tags WHERE tagID IN ({placeholders})", tag_ids)
        conn.commit()
        print(f"Successfully deleted {len(tag_ids)} tags and their item associations.")
    else:
        print("No dirty tags found.")

    conn.close()

    # 3. Clear addon startup cache to force reloading the new 1.2.3 XPI
    cache_file = Path.home() / ".zotero/zotero/default/addonStartup.json.lz4"
    if cache_file.exists():
        cache_file.unlink()
        print("Cleared addonStartup.json.lz4 cache.")

    # 4. Restart Zotero
    print("Restarting Zotero with clean tags and updated plugin...")
    cmd = "WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-0} DISPLAY=${DISPLAY:-:0} nohup /home/qyw/.local/bin/zotero >/dev/null 2>&1 &"
    subprocess.Popen(cmd, shell=True)
    time.sleep(3)
    print("Zotero restarted successfully!")

if __name__ == '__main__':
    main()
