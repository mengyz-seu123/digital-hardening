#!/usr/bin/env python3

import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--directory',default='experiments/downloads')
    args=ap.parse_args()
    dest=ROOT/args.directory
    dest.mkdir(parents=True,exist_ok=True)
    for item in json.loads((ROOT/'tools.lock').read_text()):
        path=dest/item['file']
        if not path.exists():
            partial=path.with_suffix(path.suffix+'.part')
            with urlopen(item['url'],timeout=120) as response, partial.open('wb') as out:
                while True:
                    chunk=response.read(1024*1024)
                    if not chunk: break
                    out.write(chunk)
            assert digest(partial)==item['sha256'], item['file']
            partial.rename(path)
        assert digest(path)==item['sha256'], item['file']
        print('VERIFIED',item['file'],flush=True)

if __name__=='__main__': main()
