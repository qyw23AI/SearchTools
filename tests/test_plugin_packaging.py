import tempfile
import unittest
from pathlib import Path
import zipfile

from scripts.build_plugin import build


class PluginPackagingTests(unittest.TestCase):
    def test_locales_are_enumerable_by_zotero(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = build(Path(tmp)/'bridge.xpi')
            with zipfile.ZipFile(package) as archive:
                for name in ('locale/', 'locale/en-US/', 'locale/zh-CN/'):
                    self.assertTrue(archive.getinfo(name).is_dir())
                for locale in ('en-US','zh-CN'):
                    self.assertIn('evidence-bridge-pane-header', archive.read(f'locale/{locale}/evidence-bridge.ftl').decode())
                self.assertIsNone(archive.testzip())
