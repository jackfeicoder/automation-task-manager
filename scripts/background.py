"""Entry point for optional Windows logon startup (pythonw.exe)."""
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0,str(ROOT))
(ROOT/'data').mkdir(exist_ok=True)
stream=(ROOT/'data/server.log').open('a',encoding='utf-8',buffering=1)
sys.stdout=stream
sys.stderr=stream
import runpy
runpy.run_module('backend.app',run_name='__main__')
