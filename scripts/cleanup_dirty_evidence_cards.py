#!/usr/bin/env python3
"""Clean up low-quality bibliography and negative evidence cards in SearchTools SQLite database,
and align claim with summary for existing valid cards.
"""
import re
import sqlite3
from pathlib import Path

DB_PATH = Path('/home/qyw/.local/share/searchtools/workspace.sqlite3')

def main():
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    rows = cur.execute("SELECT id, claim, summary, quote, score FROM evidence_cards").fetchall()
    print(f"Total cards before cleanup: {len(rows)}")

    deleted_ids = []
    updated_claims = []

    for r in rows:
        cid = r['id']
        score = float(r['score'] or 0)
        summary = (r['summary'] or '').strip()
        low_sum = summary.lower()
        quote = (r['quote'] or '').strip()

        # 1. Detection of noise / bibliography / non-evidence
        is_dirty = False
        if score < 5.0:
            is_dirty = True
        elif any(k in low_sum for k in ('bibliography', 'is a list of references', 'does not contain', 'does not provide', 'not applicable', 'irrelevant')):
            is_dirty = True
        elif len(summary) < 20 or len(quote) < 20:
            is_dirty = True
        elif re.search(r'\[\d+\].*\[\d+\].*\[\d+\]', quote, re.DOTALL):
            is_dirty = True

        if is_dirty:
            deleted_ids.append(cid)
            continue

        # 2. Alignment of claim with atomic assertion
        old_claim = (r['claim'] or '').strip()
        needs_alignment = (
            '?' in old_claim or '？' in old_claim or
            len(old_claim) > 60 or
            not old_claim or
            '如何' in old_claim or '怎样' in old_claim
        )
        if needs_alignment and summary:
            first_sent = re.split(r'[。！？.!?\n]', summary)[0].strip()
            if len(first_sent) > 200:
                first_sent = first_sent[:197] + '...'
            if len(first_sent) >= 8:
                updated_claims.append((first_sent, cid))

    if deleted_ids:
        cur.executemany("DELETE FROM evidence_cards WHERE id=?", [(cid,) for cid in deleted_ids])
        print(f"Successfully pruned {len(deleted_ids)} dirty/bibliography cards.")

    if updated_claims:
        cur.executemany("UPDATE evidence_cards SET claim=? WHERE id=?", updated_claims)
        print(f"Successfully aligned claims for {len(updated_claims)} valid cards.")

    conn.commit()

    remaining = cur.execute("SELECT COUNT(*) FROM evidence_cards").fetchone()[0]
    print(f"Total valid evidence cards remaining: {remaining}")
    conn.close()

if __name__ == '__main__':
    main()
