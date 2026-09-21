"""Check imports, dependency hashes, and receiver/metric boundaries without EDA."""
import ast
import hashlib
import importlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / 'code'
sys.dont_write_bytecode = True
sys.path[:0] = [str(p) for p in sorted(CODE.glob('*/src'))] + [str(CODE / 'src')]
for item in json.loads((ROOT / 'sources.lock').read_text())['files']:
    assert hashlib.sha256((ROOT / item['path']).read_bytes()).hexdigest() == item['sha256'], item['path']
for p in CODE.rglob('*.py'):
    ast.parse(p.read_text(encoding='utf-8'), filename=str(p))
for module in ('build_sic_asset', 'build_sic_hardware', 'build_sic_pool', 'build_ext_asset', 'build_ext_hardware', 'build_ext_pool', 'completion_aware', 'separability_audit'):
    importlib.import_module(module)
import hardware
assert hardware.LIB == ROOT / 'lib/NangateOpenCellLibrary_typical.lib'
assert hardware.LIB.is_file()
suite = unittest.defaultTestLoader.loadTestsFromName('test_direct_stress')
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise SystemExit(1)
print('Source imports, dependency hashes, and metric tests passed.')
