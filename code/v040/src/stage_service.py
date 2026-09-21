import sys,json,copy,time,hashlib,statistics
from pathlib import Path
import numpy as np
P=Path(__file__).resolve().parents[2]/'v030'
PRIOR=P/'results/priority-evidence-20260911-094911'
sys.path[:0]=[str(P/'src'),str(P.parent/'src')]
from core_experiment_v2 import load_data

def dataset(design,pool='original'):
    cat,obs,hw,masks,cf,cs,paths=load_data(design)
    if pool!='original':
        root=Path(pool)/'pool'/design;obs=[None]*cat['N']
        for f in sorted(root.glob('chunk_*/pool.json')):
            for r in json.loads(f.read_text())['rows']:
                assert obs[r['bit_id']] is None;obs[r['bit_id']]=r['observations']
        assert all(x is not None and len(x)==32 for x in obs)
    stages=[]
    for j,h in enumerate(hw):
        proof=0.;basecase=0.;startup=None
        if h['feasible']:
            prefix='startup_preload_proofs' if design=='divu16' else 'startup_proofs'
            startup=PRIOR/prefix/design/h['plan_id']/'result.json'
            r=json.loads(startup.read_text());assert r['status']=='passed'
            basecase=r['elapsed'];proof=h['fault_certificate']['elapsed']
        cheap=h['cost_seconds']-proof
        assert cheap>0 and abs(cheap+proof+basecase-h['cost_seconds']-basecase)<1e-8
        stages.append(dict(screen_cost=cheap,proof_cost=proof+basecase,startup_path=str(startup),record=h))
    return cat,obs,stages,masks,cf,cs,paths

class StagedService:
    def __init__(self,data,seed):
        cat,obs,stages,masks,cf,cs,paths=data
        self._obs=obs;self._stages=stages;self._counts=np.zeros(len(obs),int)
        rng=np.random.default_rng(seed+82701)
        self._perms=np.array([rng.permutation(len(x)) for x in obs])
        self._screened={0};self._proven={0};self.ledger=[]
        self.area_prior_per_bit=cat['nominal_cost_per_bit']/cat['area0']
        self.nominal_tmr_bits=cat['initial_tmr_bit_count_estimate']
        self.fault_cost_estimate=cf;self.screen_cost_estimate=cs
        self.proof_cost_estimate=stages[0]['proof_cost'];self._screen_costs=[]
        self.fault_cost=0.;self.screen_cost=0.;self.proof_cost=0.
    def fault(self,i,start,count):
        assert start==self._counts[i] and 0<count<=32-start
        rows=[self._obs[i][int(j)] for j in self._perms[i,start:start+count]]
        labels=[r['y'] for r in rows];cost=sum(r['cost_seconds'] for r in rows)
        self._counts[i]+=count;self.fault_cost+=cost
        self.ledger.append(dict(action='fault',bit=i,start=start,count=count,labels=labels,cost=cost,
            event_ids=[r['event_id'] for r in rows]))
        return labels,cost
    def screen(self,j):
        assert j not in self._screened;self._screened.add(j)
        r=self._stages[j];h=r['record'];cost=r['screen_cost']
        self.screen_cost+=cost;self._screen_costs.append(cost)
        self.screen_cost_estimate=float(np.median(self._screen_costs))
        passed=h['area_ratio']<=.2+1e-9 and h['sta']['feasible']
        self.ledger.append(dict(action='screen',candidate=j,cost=cost,passed=bool(passed)))
        return dict(passed=bool(passed),area_ratio=h['area_ratio'],timing=h['sta']['feasible'],cost=cost)
    def prove(self,j):
        assert j in self._screened and j not in self._proven
        r=self._stages[j];h=r['record'];assert h['feasible']
        cost=r['proof_cost'];self.proof_cost+=cost;self._proven.add(j)
        passed=h['fault_certificate']['status']=='passed'
        self.ledger.append(dict(action='proof',candidate=j,cost=cost,passed=passed))
        return dict(passed=passed,cost=cost)
    def totals(self):
        return dict(fault_seconds=self.fault_cost,screen_seconds=self.screen_cost,
            proof_seconds=self.proof_cost,service_seconds=self.fault_cost+self.screen_cost+self.proof_cost,
            fault_queries=int(self._counts.sum()),screen_queries=len(self._screened)-1,
            proof_queries=len(self._proven)-1)

def audit(data,result,service):
    cat,obs,stages,mask,cf,cs,paths=data;N=cat['N'];M=32
    truth=np.array([sum(r['y'] for r in x)/M for x in obs])
    n=np.asarray(result['n']);f=np.asarray(result['f']);S=result['incumbent']
    L=f/M;U=(f+M-n)/M;assert np.all(L<=truth+1e-12) and np.all(truth<=U+1e-12)
    live=[j for j in range(len(mask)) if result['status_vector'][j]!=0]
    gap=max(0.,float((np.maximum(mask[live]-mask[S],0)@U-np.maximum(mask[S]-mask[live],0)@L).max()/N))
    feasible=np.array([r['record']['feasible'] for r in stages]);v=mask@truth/N
    assert stages[S]['record']['feasible'] and S in service._proven
    assert abs(gap-result['gap'])<1e-10 and gap<=result['epsilon']+1e-10
    assert v[feasible].max()-v[S]<=gap+1e-10
    assert int(n.sum())==service.totals()['fault_queries']
    for e in service.ledger:
        if e['action']=='screen':assert e['passed']==feasible[e['candidate']]
    return dict(status='passed',regret=float(v[feasible].max()-v[S]),gap=gap,
        plan_id=cat['plans'][S]['plan_id'],area=stages[S]['record']['area_ratio'])
