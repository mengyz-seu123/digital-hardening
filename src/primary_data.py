import json
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
ASSET=ROOT/'results/primary/assets/primary_controller';POOL=ROOT/'results/primary/fault_pool';HW=ROOT/'results/primary/hardware'

def dataset():
    cat=json.loads((HW/'catalog.json').read_text());cat['asset']=str(ASSET)
    obs=json.loads((POOL/'obs.json').read_text());records=json.loads((HW/'records.json').read_text());assert len(obs)==cat['N']
    stages=[dict(screen_cost=float(r['screen_seconds']),proof_cost=float(r['proof_seconds']),startup_path=None,record=r) for r in records]
    masks=np.zeros((len(cat['plans']),cat['N']),float)
    for j,p in enumerate(cat['plans']):masks[j,p['selected']]=1.
    cf=float(np.median([x['cost_seconds'] for arm in obs for x in arm]));cs=float(np.median([x['screen_seconds'] for x in records[1:]]))
    return cat,obs,stages,masks,cf,cs,{}
