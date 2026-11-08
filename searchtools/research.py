"""Persistent research runs and caller-reviewed evidence bindings."""
import hashlib
import json
import time
import uuid


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Research:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS research_records(
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, kind TEXT NOT NULL,
                payload TEXT NOT NULL, revision TEXT NOT NULL, created REAL NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS research_run_kind ON research_records(run_id,kind)')

    def records(self, run_id, kind):
        return [dict(json.loads(r['payload']), id=r['id'], revision=r['revision'])
                for r in self.store.rows('SELECT * FROM research_records WHERE run_id=? AND kind=? ORDER BY created', (run_id, kind))]

    def get(self, identity):
        rows = self.store.rows('SELECT * FROM research_records WHERE id=?', (identity,))
        if not rows:
            raise ValueError('Research record not found')
        r = rows[0]
        return dict(json.loads(r['payload']), id=r['id'], revision=r['revision'], kind=r['kind'], run_id=r['run_id'])

    def save(self, run_id, kind, payload):
        request_id = payload.get('request_id')
        identity = fingerprint([run_id, kind, request_id]) if request_id else uuid.uuid4().hex
        revision = fingerprint(payload)
        with self.store.db() as db:
            existing = db.execute('SELECT revision FROM research_records WHERE id=?', (identity,)).fetchone()
            if existing:
                if existing['revision'] != revision:
                    raise ValueError('Request ID already used with different content')
                return self.get(identity)
            db.execute('INSERT INTO research_records VALUES(?,?,?,?,?,?)',
                       (identity, run_id, kind, json.dumps(payload, ensure_ascii=False), revision, time.time()))
        return self.get(identity)

    def packet(self, binding):
        atom = self.get(binding['atom_id'])
        card = self.store.get_evidence_card(binding['card_id'])
        if not card:
            raise ValueError('Card not found')
        location = self.store.locate_card(card['id'], force=True)
        packet = {'binding_id': binding['id'], 'atom': atom, 'card': {
            k: card.get(k) for k in ('id', 'quote', 'claim', 'title', 'attachment_key', 'zotero_key')},
            'source_context': (card.get('provenance') or {}).get('source_context', ''),
            'location': location, 'lower_verdict': binding.get('lower_verdict', 'unreviewed')}
        packet['packet_revision'] = fingerprint(packet)
        return packet

    def review_state(self, run_id, atom_id):
        bindings = [b for b in self.records(run_id, 'binding') if b['atom_id'] == atom_id]
        state = []
        for binding in bindings:
            packet = self.packet(binding)
            reviews = [r for r in self.records(run_id, 'review') if r['binding_id'] == binding['id']]
            review = reviews[-1] if reviews else None
            current = review and review['packet_revision'] == packet['packet_revision']
            state.append({'binding_id': binding['id'], 'packet_revision': packet['packet_revision'],
                          'review_id': review['id'] if current else None})
        return state

    def accepted(self, run_id):
        accepted = []
        for binding in self.records(run_id, 'binding'):
            packet = self.packet(binding)
            reviews = [r for r in self.records(run_id, 'review') if r['binding_id'] == binding['id']]
            if reviews:
                review = reviews[-1]
                if (review['packet_revision'] == packet['packet_revision'] and
                        review['decision'] == 'accept' and review['relation'] == 'supports' and
                        packet['location'].get('status') == 'exact'):
                    state = self.review_state(run_id, binding['atom_id'])
                    summaries = [r for r in self.records(run_id, 'atom_summary') if r.get('atom_id') == binding['atom_id']]
                    if summaries and summaries[-1].get('overall_verdict') == 'supported' and summaries[-1].get('review_set_revision') == fingerprint(state):
                        accepted.append(binding)
        return accepted

    def card_reviews(self, card_id):
        result = []
        rows = self.store.rows("SELECT id FROM research_records WHERE kind='binding'")
        for row in rows:
            binding = self.get(row['id'])
            if binding['card_id'] != card_id:
                continue
            packet = self.packet(binding)
            reviews = [r for r in self.records(binding['run_id'], 'review') if r['binding_id'] == binding['id']]
            review = reviews[-1] if reviews else None
            result.append({'atom': packet['atom']['text'], 'run_id': binding['run_id'],
                           'status': review['decision'] if review and review['packet_revision'] == packet['packet_revision'] else 'pending',
                           'reason': review['reason'] if review else ''})
        return result

    def call(self, operation, args):
        if operation == 'capabilities':
            return {'schema_version': 'research.v1', 'operations': ['run', 'atoms', 'bindings', 'review_packets', 'submit_review', 'audit', 'read', 'export', 'record', 'search', 'finalize_atom'],
                    'reviewer': 'calling_agent', 'automatic_direction_generation': False}
        if operation == 'run' and args.get('action') == 'create':
            scope = args['scope_id']
            self.store.project(scope)
            parent = args.get('parent_run_id')
            if parent:
                self.get(parent)
            return self.save('', 'run', {'scope_id': scope, 'topic': args['topic'],
                                        'parent_run_id': parent, 'constraints': args.get('constraints', {})})
        if operation == 'list':
            return self.records('', 'run')
        run = self.get(args['run_id'])
        if run['kind'] != 'run':
            raise ValueError('Expected run record')
        rid = run['id']
        if operation == 'run':
            return run
        if operation == 'finalize_atom':
            atom = self.get(args['atom_id'])
            if atom['run_id'] != rid:
                raise ValueError('Atom outside run')
            state = self.review_state(rid, atom['id'])
            if not state or any(not item['review_id'] for item in state):
                raise ValueError('Every candidate binding needs a current upper review')
            verdict = args['overall_verdict']
            if verdict not in ('supported', 'qualified', 'contradicted', 'disputed', 'insufficient') or not args.get('reason'):
                raise ValueError('Final judgment and reasoning required')
            reviews = [self.get(item['review_id']) for item in state]
            if verdict == 'supported' and (not any(r['decision'] == 'accept' and r['relation'] == 'supports' for r in reviews) or any(r['decision'] == 'request_more' or (r['decision'] == 'accept' and r['relation'] == 'contradicts') for r in reviews)):
                raise ValueError('Unresolved evidence cannot be finalized as supported')
            return self.save(rid, 'atom_summary', {'atom_id':atom['id'], 'overall_verdict':verdict,
                'reason':args['reason'], 'review_set_revision':fingerprint(state)})
        if operation == 'record':
            kind = args['kind']
            if kind not in ('direction', 'reading', 'experiment', 'search_log'):
                raise ValueError('Invalid research record kind')
            payload = args.get('data', {})
            if not isinstance(payload, dict):
                raise ValueError('Expected structured record')
            return self.save(rid, kind, payload)
        if operation == 'search':
            from .zotero import Library
            results = Library().items(q=args.get('query', ''), tag=args.get('tag', ''), limit=100)
            self.save(rid, 'search_log', {'query': args.get('query', ''), 'source': 'zotero', 'result': results})
            return results
        if operation == 'atoms':
            if args.get('action') == 'create':
                text = str(args['text']).strip()
                if not text:
                    raise ValueError('Empty atom')
                return self.save(rid, 'atom', {'text': text, 'type': args.get('type', 'reported_result'),
                                               'original_sentence': args.get('original_sentence', text),
                                               'outline_node': args.get('outline_node', '')})
            return self.records(rid, 'atom')
        if operation == 'bindings':
            if args.get('action') == 'assign':
                atom = self.get(args['atom_id'])
                card = self.store.get_evidence_card(args['card_id'])
                if atom['run_id'] != rid or atom['kind'] != 'atom' or not card or card['project'] != run['scope_id']:
                    raise ValueError('Atom/card does not belong to this run scope')
                return self.save(rid, 'binding', {'atom_id': atom['id'], 'card_id': card['id'],
                                                  'lower_verdict': args.get('lower_verdict', 'unreviewed')})
            return self.records(rid, 'binding')
        if operation == 'review_packets':
            bindings = self.records(rid, 'binding')
            start = max(0, int(args.get('cursor', 0)))
            limit = max(1, min(20, int(args.get('limit', 5))))
            return {'packets': [self.packet(b) for b in bindings[start:start+limit]], 'total': len(bindings),
                    'next_cursor': start+limit if start+limit < len(bindings) else None}
        if operation == 'submit_review':
            binding = self.get(args['binding_id'])
            if binding['run_id'] != rid or binding['kind'] != 'binding':
                raise ValueError('Binding outside run')
            packet = self.packet(binding)
            if args['packet_revision'] != packet['packet_revision']:
                raise ValueError('Review packet changed; read current source again')
            if args['decision'] not in ('accept', 'reject', 'request_more') or args['relation'] not in ('supports', 'qualifies', 'contradicts', 'insufficient'):
                raise ValueError('Invalid review decision')
            spans = {s['paragraph_id'] for s in packet['location'].get('spans', [])}
            if not str(args.get('reason', '')).strip() or not set(args.get('span_ids', [])).issubset(spans) or (args['decision'] == 'accept' and not args.get('span_ids')):
                raise ValueError('Review requires reasoning and valid source spans')
            if args['decision'] == 'accept' and packet['location'].get('status') != 'exact':
                raise ValueError('Source must be verified before accepting')
            review = {k: args[k] for k in ('binding_id', 'packet_revision', 'decision', 'relation', 'reason', 'span_ids')}
            if args.get('request_id'):
                review['request_id'] = args['request_id']
            review['reviewer'] = 'calling_agent'
            review['reviewer_model'] = args.get('reviewer_model', 'unknown')
            return self.save(rid, 'review', review)
        if operation == 'audit':
            return {'atoms': self.records(rid, 'atom'), 'bindings': self.records(rid, 'binding'),
                    'reviews': self.records(rid, 'review'), 'review_states': [state for atom in self.records(rid, 'atom') for state in self.review_state(rid, atom['id'])], 'accepted_supports': self.accepted(rid)}
        if operation == 'read':
            from .locate import resolve_attachment_pdf
            path = resolve_attachment_pdf(args['attachment_key'])
            if path is None:
                raise ValueError('Attachment PDF unavailable')
            # Use the project interpreter with PyMuPDF if the service lacks it.
            import subprocess
            from pathlib import Path
            python = Path.home() / 'DeepTutor/.venv/bin/python'
            code = 'import fitz,json,sys; d=fitz.open(sys.argv[1]); print(json.dumps([{ "page_index":i,"text":d[i].get_text()} for i in range(len(d))],ensure_ascii=False))'
            result = subprocess.run([str(python), '-c', code, str(path)], capture_output=True, text=True, check=True, timeout=60)
            pages = json.loads(result.stdout)
            start = max(0, int(args.get('cursor', 0)))
            return {'pages': pages[start:start+3], 'total': len(pages), 'next_cursor': start+3 if start+3 < len(pages) else None,
                    'pdf_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        if operation == 'export':
            return {'run': run, 'atoms': self.records(rid, 'atom'), 'reviews': self.records(rid, 'review'),
                    'accepted_supports': self.accepted(rid), 'directions': self.records(rid, 'direction')}
        raise ValueError('Unknown research operation')
