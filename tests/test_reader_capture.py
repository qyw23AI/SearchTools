import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from searchtools.core import Store
from searchtools.locate import fitz, locate_quote_in_pdf
from searchtools.reader_capture import validate_position, verify_selection_location


class CaptureTests(unittest.TestCase):
    def test_rejects_missing_invalid_and_nonfinite_coordinates(self):
        for position in (None, {'pageIndex': -1, 'rects': [[0, 0, 1, 1]]},
                         {'pageIndex': 0, 'rects': []},
                         {'pageIndex': 0, 'rects': [[0, 0, float('nan'), 1]]}):
            with self.assertRaises(ValueError):
                validate_position(position)

    @unittest.skipUnless(fitz, 'PDF fixture runtime required')
    def test_native_capture_is_attributed_verified_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'test.pdf'
            quote = 'Controller A consumed less electrical energy during walking experiments.'
            with fitz.open() as doc:
                doc.new_page().insert_text((50, 60), quote)
                doc.save(path)
            location = locate_quote_in_pdf(path, quote)
            data = {'zotero_key': 'PARENTAA', 'attachment_key': 'ATTACHAA',
                    'request_id': 'click-1', 'quote': quote, 'claim': 'Walking energy decreased.',
                    'title': 'untrusted client title',
                    'reader_selection': {'position': {'pageIndex': 0, 'rects': location['rects']}}}
            store = Store(Path(tmp)/'workspace')
            with patch('searchtools.zotero.Library') as library, patch('searchtools.locate.resolve_attachment_pdf', return_value=path):
                lib = library.return_value
                lib.raw_item.return_value = {'data': {'itemType': 'attachment', 'parentItem': 'PARENTAA'}}
                lib.summary.return_value = {'title': 'Authoritative library title', 'creators': ['Researcher'], 'year': '2025'}
                card = store.capture_reader_selection(data)
                self.assertEqual(card['title'], 'Authoritative library title')
                self.assertEqual(card['locate']['status'], 'exact')
                self.assertEqual(card['provenance']['reader_selection'], data['reader_selection'])
                self.assertEqual(card['score'], 0)
                self.assertEqual(store.capture_reader_selection(data)['id'], card['id'])
                self.assertEqual(len(store.list_evidence_cards()), 1)
                self.assertEqual(store.locate_card(card['id'])['status'], 'exact')
                wrong_page = dict(data, request_id='click-2', reader_selection={'position': {'pageIndex': 1, 'rects': location['rects']}})
                with self.assertRaisesRegex(ValueError, '物理页码'):
                    store.capture_reader_selection(wrong_page)
                wrong_rect = dict(data, request_id='click-3', reader_selection={'position': {'pageIndex': 0, 'rects': [[1,1,10,10]]}})
                with self.assertRaisesRegex(ValueError, '矩形'):
                    store.capture_reader_selection(wrong_rect)
                lib.raw_item.return_value = {'data': {'itemType': 'attachment', 'parentItem': 'OTHERAAA'}}
                with self.assertRaisesRegex(ValueError, '归属'):
                    store.capture_reader_selection(dict(data, request_id='click-4'))
                self.assertEqual(len(store.list_evidence_cards()), 1)

    def test_broad_page_hint_cannot_substitute_for_unique_source(self):
        with self.assertRaisesRegex(ValueError, '唯一回验'):
            verify_selection_location({'pageIndex':0, 'rects': [[0,0,1,1]]}, {'status':'ambiguous'})
