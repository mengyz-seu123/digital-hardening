import json
from pathlib import Path
import numpy as np
from stage_service import StagedService as BaseService

def mean(n,f,groups,strength=4.):
    number=int(groups.max())+1
    totals=np.bincount(groups,weights=n,minlength=number)
    fails=np.bincount(groups,weights=f,minlength=number)
    prior=(fails[groups]-f+1)/(totals[groups]-n+2)
    return (f+1+strength*prior)/(n+2+strength)

class Service(BaseService):
    def __init__(self,data,seed):
        super().__init__(data,seed)
        rows=json.loads((Path(data[0]['asset'])/'logical_bits.json').read_text())
        names=sorted(set(r['source_cell'] for r in rows));index={v:i for i,v in enumerate(names)}
        self.risk_groups=np.array([index[r['source_cell']] for r in rows],int)
