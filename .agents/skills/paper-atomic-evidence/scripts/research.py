"""Run the installed local SearchTools research adapter from any directory."""
import os
from pathlib import Path
import runpy
import sys

root = Path(os.environ.get('SEARCHTOOLS_ROOT', '/home/qyw/文档/ChatGPT/科研学习相关工具搭建研究'))
if not (root / 'searchtools' / 'research_cli.py').is_file():
    raise SystemExit('Set SEARCHTOOLS_ROOT to the SearchTools checkout')
sys.path.insert(0, str(root))
runpy.run_module('searchtools.research_cli', run_name='__main__')
