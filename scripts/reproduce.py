"""Rebuild both controller catalogues, joint hardening, and separability checks."""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / 'src'

def preflight():
    if sys.version_info < (3, 11):
        raise SystemExit('Python 3.11 or newer is required.')
    if sys.platform != 'linux':
        raise SystemExit('Run the full EDA pipeline on Linux x86-64 (WSL2 is supported).')
    if any(c.isspace() for c in str(ROOT)):
        raise SystemExit('Use a checkout path without spaces; the EDA scripts use unquoted paths.')
    for name in ('numpy',):
        importlib.import_module(name)
    missing = [x for x in ('yosys', 'iverilog', 'vvp', 'sta', 'ngspice') if not shutil.which(x)]
    if missing:
        raise SystemExit('Missing tools: ' + ', '.join(missing))
    for item in json.loads((ROOT / 'sources.lock').read_text())['files']:
        actual = hashlib.sha256((ROOT / item['path']).read_bytes()).hexdigest()
        if actual != item['sha256']:
            raise SystemExit('Dependency hash mismatch: ' + item['path'])
    print('Dependency preflight passed.', flush=True)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--check', action='store_true', help='check dependencies without running experiments')
    args = ap.parse_args()
    preflight()
    if args.check:
        return
    if (ROOT / 'results').exists():
        raise SystemExit('Existing results found. Use a fresh extraction for a new run.')
    def run(script, *args):
        subprocess.run([sys.executable, str(CODE / script), *map(str, args)], cwd=ROOT, check=True)
    for script in (
        'build_primary_asset.py', 'build_primary_hardware.py', 'build_primary_pool.py',
        'build_transfer_asset.py', 'build_transfer_hardware.py', 'build_transfer_pool.py',
    ):
        run(script)
    primary = ROOT / 'results/interface'
    complete = ROOT / 'results/completion'
    audit = ROOT / 'results/separability'
    liberty = ROOT / 'lib/NangateOpenCellLibrary_typical.lib'
    run('primary_interface.py', '--out', primary, '--liberty', liberty)
    run('completion.py', '--out', complete, '--primary-out', primary, '--liberty', liberty)
    result = json.loads((complete / 'summary.json').read_text())
    if result['status'] != 'completed' or not all(result[k] for k in ('h1_oracle_match', 'h2_ordinary_nonregression', 'h3_primary_nonworse', 'h4_transfer_completion_aware')):
        raise RuntimeError('Completion-aware acceptance checks failed.')
    run('separability.py', '--artifact-root', ROOT, '--out', audit)
    result = json.loads((audit / 'summary.json').read_text())
    if result['status'] != 'completed' or not result['all_separable'] or not result['count_contract_pass'] or result['total_signature_comparisons'] != 162:
        raise RuntimeError('Separability acceptance checks failed.')
    print('Joint hardening and separability checks passed.', flush=True)

if __name__ == '__main__':
    main()
