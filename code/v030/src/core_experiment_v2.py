import argparse,json,time,statistics,hashlib
from pathlib import Path
import numpy as np
import hardware as h
from catalogue3 import P,ASSETS
from dual_policy_v2 import METHODS,run
CFG=P/'configs/core-study-01';DATA=P/'results/core-data-01'

def load_data(design):
    cat=json.loads((CFG/(design+'.json')).read_text());N=cat['N'];M=32
    obs=[None]*N
    for p in sorted((DATA/'pool'/design).glob('chunk_*/pool.json')):
        record=json.loads(p.read_text());assert record['status']=='passed'
        for row in record['rows']:
            assert obs[row['bit_id']] is None;obs[row['bit_id']]=row['observations']
    assert all(r is not None and len(r)==M for r in obs),('Incomplete pool',design)
    measured=[];paths=[]
    for plan in cat['plans']:
        file=DATA/'hardware'/design/plan['plan_id']/'result.json'
        recovery=P/'results/core-data-recovery-01/hardware'/design/plan['plan_id']/'result.json'
        if recovery.exists():file=recovery
        assert file.exists(),('Incomplete hardware',file)
        r=json.loads(file.read_text());assert r['status']=='completed'
        if r['feasible']:assert r['fault_certificate']['status']=='passed'
        measured.append(r);paths.append(dict(path=str(file),sha256=h.sha(file)))
    masks=np.zeros((len(cat['plans']),N))
    for i,p in enumerate(cat['plans']):masks[i,p['selected']]=1
    asset=Path(cat['asset']);normal=asset/'acceptance_normal.jsonl' if design in ['sha256','divu16'] else asset/'normal.jsonl'
    rr=[json.loads(x) for x in normal.read_text().splitlines()]
    cf=statistics.median(r['runtime'] for r in rr if r['hardware']=='baseline')
    if design!='sha256':cf*=2
    cc=[json.loads(x) for x in (asset/'commands.jsonl').read_text().splitlines()]
    cs=sum(r['end']-r['start'] for r in cc if r['name'] in ['baseline_map','baseline_write','baseline_sta'])
    if cs<=0:
        origin=Path(json.loads((asset/'asset_audit.json').read_text())['acceptance_origin'])
        commands=[json.loads(x) for x in (origin/'commands.jsonl').read_text().splitlines()]
        cs=sum(r['end']-r['start'] for r in commands if r['name'] in ['baseline_map','baseline_write','baseline_sta'])
    assert cs>0 and cf>0
    return cat,obs,measured,masks,cf,cs,paths

class Service:
    def __init__(self,obs,hardware,seed,cf,cs,cat):
        self.area_prior_per_bit=cat['nominal_cost_per_bit']/cat['area0']
        self.nominal_tmr_bits=cat['initial_tmr_bit_count_estimate']
        self._obs=obs;self._hardware=hardware;self._seen=set();self._count=np.zeros(len(obs),int)
        rng=np.random.default_rng(seed+82701)
        self._permutations=np.array([rng.permutation(len(x)) for x in obs])
        self.fault_cost_estimate=cf;self.synthesis_cost_estimate=cs
        self._initial_cs=cs;self._actual_cs=[]
    def fault(self,i,k,b):
        assert k==self._count[i] and k+b<=len(self._obs[i]);self._count[i]+=b
        records=[self._obs[i][int(j)] for j in self._permutations[i,k:k+b]]
        return [r['y'] for r in records],sum(r['cost_seconds'] for r in records)
    def synthesis(self,j):
        assert j not in self._seen and j!=0;self._seen.add(j)
        r=self._hardware[j];self._actual_cs.append(r['cost_seconds'])
        self.synthesis_cost_estimate=(self._initial_cs+sum(self._actual_cs))/(1+len(self._actual_cs))
        return dict(feasible=r['feasible'],cost_seconds=r['cost_seconds'],area_ratio=r['area_ratio'],timing_feasible=r['sta']['feasible'])

def experiment(design,dest):
    dest=Path(dest);dest.mkdir(parents=True,exist_ok=False)
    cat,obs,hardware,masks,cf,cs,paths=load_data(design);records=[]
    h.dump(dest/'provenance.json',dict(catalogue_sha256=h.sha(CFG/(design+'.json')),hardware=paths,
        policy_source_sha256=h.sha(P/'src/dual_policy_v2.py'),service_source_sha256=h.sha(Path(__file__)),
        cf_initial=cf,cs_initial=cs,scope='query-service replay; hidden tables are backend-only by audited code, not OS sandboxing'))
    for seed in [701,702,703,704,705]:
        for method in METHODS:
            service=Service(obs,hardware,seed,cf,cs,cat)
            result,trace=run(method,masks,service,seed)
            assert result['status']=='certified'
            # Reference truth is consulted only after the policy has terminated.
            truth=np.array([sum(v['y'] for v in arm)/len(arm) for arm in obs])
            value=masks@truth/cat['N'];feasible=np.array([r['feasible'] for r in hardware])
            optimum=float(value[feasible].max());got=int(result['incumbent'])
            regret=optimum-float(value[got]);assert feasible[got] and regret<=.02+1e-10
            result.update(design_id=design,role=cat['role'],N=cat['N'],candidate_count=len(masks),
                selected=cat['plans'][got]['selected'],plan_id=cat['plans'][got]['plan_id'],
                finite_reference_regret=regret,finite_reference_optimum=optimum,
                actual_area_ratio=hardware[got]['area_ratio'],period_ns=cat['period_ns'],
                physical_measurement=str(Path(paths[got]['path']).parent),
                cold_start_shared_cost=hardware[0]['cost_seconds']+json.loads((P/'results/canonical-bridge-02'/design/'bridge.json').read_text())['elapsed'])
            file=dest/(method+'_'+str(seed)+'.json');h.dump(file,result)
            with file.with_suffix('.jsonl').open('w') as f:
                for row in trace:f.write(json.dumps(row,sort_keys=True)+'\n')
            records.append(result)
    h.dump(dest/'summary.json',dict(status='completed',design_id=design,records=records,
        finite_reference_best_plan=cat['plans'][int(np.argmax(np.where(feasible,value,-1)))],
        reference_definition='best actually verified-feasible plan in the shared label-free catalogue on the fixed finite risk pool'))
    print('EXPERIMENT_DONE',design,flush=True)
    for method in METHODS:
        rows=[r for r in records if r['method']==method]
        print('METHOD',design,method,'seconds',round(statistics.mean(r['total_service_seconds'] for r in rows),3),
            'faults',round(statistics.mean(r['fault_queries'] for r in rows),1),
            'synthesis',round(statistics.mean(r['synthesis_queries'] for r in rows),1),
            'regret',round(statistics.mean(r['finite_reference_regret'] for r in rows),5),flush=True)
    return records

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--designs',nargs='+',required=True);ap.add_argument('--tag',required=True);a=ap.parse_args()
    root=P/'results'/a.tag;root.mkdir(exist_ok=False)
    for design in a.designs:experiment(design,root/design)
