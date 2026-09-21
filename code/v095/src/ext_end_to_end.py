import sys,json,time,statistics,traceback
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'v095/src'),str(ROOT/'v040/src'),str(ROOT/'v030/src'),str(ROOT/'src')]
import hardware as h,stage_service as ss,group_guidance as gg,group_route as gr,main_policy as mp,card_cover
ASSET=ROOT/'v095/results/ext-asset-01/ext_motor_axis_ctrl';POOL=ROOT/'v095/results/ext-pool-01';HW=ROOT/'v095/results/ext-hardware-01';OUT=ROOT/'v095/results/ext-study-01'
SEEDS=list(range(51001,51006));METHODS=['Grouped-Route','Certificate-Route','Staged-Dual','Directed-Lazy-0.5','Cardinality-Cover']

def dataset():
    cat=json.loads((HW/'catalog.json').read_text());cat['asset']=str(ASSET);obs=json.loads((POOL/'obs.json').read_text());records=json.loads((HW/'records.json').read_text());assert len(obs)==cat['N'] and all(len(x)==32 for x in obs);stages=[dict(screen_cost=float(r['screen_seconds']),proof_cost=float(r['proof_seconds']),startup_path=None,record=r) for r in records];masks=np.zeros((len(cat['plans']),cat['N']),float)
    for j,p in enumerate(cat['plans']):masks[j,p['selected']]=1.
    cf=float(np.median([x['cost_seconds'] for arm in obs for x in arm]));cs=float(np.median([x['screen_seconds'] for x in records[1:]]));return cat,obs,stages,masks,cf,cs,{}

def run_one(name,data,seed):
    if name=='Grouped-Route':svc=gg.Service(data,seed);result,trace=gr.run('Cover-LP',data[3],svc,seed,quota=1.)
    elif name=='Certificate-Route':svc=ss.StagedService(data,seed);result,trace=mp.run('Cover-LP',data[3],svc,seed,quota=1.)
    elif name=='Staged-Dual':svc=ss.StagedService(data,seed);result,trace=mp.run('Dual',data[3],svc,seed,quota=1.)
    elif name=='Directed-Lazy-0.5':svc=ss.StagedService(data,seed);result,trace=mp.run('Directed-Lazy',data[3],svc,seed,quota=.5)
    elif name=='Cardinality-Cover':svc=ss.StagedService(data,seed);result,trace=card_cover.run('Cardinality-First',data[3],svc,seed,quota=1.)
    else:raise ValueError(name)
    return result,trace,ss.audit(data,result,svc),svc

def main():
    OUT.mkdir(parents=True,exist_ok=False);data=dataset();started=time.time();rows=[];h.dump(OUT/'protocol.json',dict(status='frozen_transfer',methods=METHODS,seeds=SEEDS,epsilon=.02,M=32,batch=8,area_ratio=.2,query_policy_changed_from_v040=False,no_post_label_tuning=True,same_finite_pool_contract_as_primary=True))
    for name in METHODS:
        for seed in SEEDS:
            folder=OUT/name/str(seed);folder.mkdir(parents=True)
            try:
                result,trace,audit,svc=run_one(name,data,seed);row=dict(method=name,seed=seed,status='passed',plan_id=audit['plan_id'],regret=audit['regret'],gap=audit['gap'],area=audit['area'])
                for k in ['total_seconds','service_seconds','controller_seconds','fault_seconds','screen_seconds','proof_seconds','fault_queries','screen_queries','proof_queries']:row[k]=result[k]
                h.dump(folder/'result.json',dict(result=result,audit=audit))
                with (folder/'trace.jsonl').open('w') as f:
                    for t in trace:f.write(json.dumps(t)+'\n')
            except Exception as exc:row=dict(method=name,seed=seed,status='failed',error=str(exc),traceback=traceback.format_exc());h.dump(folder/'failure.json',row)
            rows.append(row);print('V095_EXT_STUDY',name,seed,row['status'],row.get('total_seconds'),row.get('plan_id'),flush=True)
    h.dump(OUT/'results.json',rows);summary=[]
    for name in METHODS:
        rr=[r for r in rows if r['method']==name];ok=[r for r in rr if r['status']=='passed'];z=dict(method=name,trials=len(rr),passed=len(ok),all_passed=len(ok)==len(rr))
        if ok:
            for k in ['total_seconds','fault_queries','screen_queries','proof_queries','fault_seconds','screen_seconds','proof_seconds','controller_seconds','regret','gap','area']:z[k]=statistics.mean(r[k] for r in ok)
            z['unique_plans']=sorted({r['plan_id'] for r in ok})
        summary.append(z)
    report=dict(status='completed' if all(r['status']=='passed' for r in rows) else 'completed_with_failures',runs=len(rows),elapsed=time.time()-started,summary=summary);h.dump(OUT/'summary.json',report);print('V095_EXT_STUDY_COMPLETE',json.dumps(summary,sort_keys=True),flush=True)
if __name__=='__main__':main()
