import sys,json,time,statistics,traceback,csv
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'v091/src'),str(ROOT/'v040/src'),str(ROOT/'v030/src'),str(ROOT/'src')]
import hardware as h
import stage_service as ss
import group_guidance as gg
import group_route as gr
import main_policy as mp
import card_cover
ASSET=ROOT/'v091/results/sic-asset-01/sic_gate_ctrl_v3';POOL=ROOT/'v091/results/sic-pool-01';HW=ROOT/'v091/results/sic-hardware-01';OUT=ROOT/'v091/results/sic-study-01'
SEEDS=list(range(21001,21011));METHODS=['Grouped-Route','Certificate-Route','Staged-Dual','Directed-Lazy-0.5','Cardinality-Cover']


def dataset():
    cat=json.loads((HW/'catalog.json').read_text());cat['asset']=str(ASSET)
    obs=json.loads((POOL/'obs.json').read_text());records=json.loads((HW/'records.json').read_text());assert len(obs)==cat['N']
    stages=[dict(screen_cost=float(r['screen_seconds']),proof_cost=float(r['proof_seconds']),startup_path=None,record=r) for r in records]
    masks=np.zeros((len(cat['plans']),cat['N']),float)
    for j,p in enumerate(cat['plans']):masks[j,p['selected']]=1.
    cf=float(np.median([x['cost_seconds'] for arm in obs for x in arm]));cs=float(np.median([x['screen_seconds'] for x in records[1:]]))
    return cat,obs,stages,masks,cf,cs,{}


def run_one(name,data,seed):
    if name=='Grouped-Route':svc=gg.Service(data,seed);result,trace=gr.run('Cover-LP',data[3],svc,seed,quota=1.)
    elif name=='Certificate-Route':svc=ss.StagedService(data,seed);result,trace=mp.run('Cover-LP',data[3],svc,seed,quota=1.)
    elif name=='Staged-Dual':svc=ss.StagedService(data,seed);result,trace=mp.run('Dual',data[3],svc,seed,quota=1.)
    elif name=='Directed-Lazy-0.5':svc=ss.StagedService(data,seed);result,trace=mp.run('Directed-Lazy',data[3],svc,seed,quota=.5)
    elif name=='Cardinality-Cover':svc=ss.StagedService(data,seed);result,trace=card_cover.run('Cardinality-First',data[3],svc,seed,quota=1.)
    else:raise ValueError(name)
    return result,trace,ss.audit(data,result,svc),svc


def main():
    OUT.mkdir(parents=True,exist_ok=False);data=dataset();started=time.time();rows=[]
    protocol=dict(status='frozen',methods=METHODS,seeds=SEEDS,epsilon=.02,M=32,batch=8,area_ratio=.2,main_method='Grouped-Route',
                  query_policy_changed_from_v040=False,no_post_label_tuning=True,all_methods_share_identical_fault_pool_and_hardware_stages=True,
                  source_hashes={p.name:h.sha(p) for p in [Path(__file__),Path(gr.__file__),Path(gg.__file__),Path(mp.__file__)]})
    h.dump(OUT/'protocol.json',protocol)
    for name in METHODS:
        for seed in SEEDS:
            folder=OUT/name/str(seed);folder.mkdir(parents=True)
            try:
                result,trace,audit,svc=run_one(name,data,seed)
                row=dict(method=name,seed=seed,status='passed',plan_id=audit['plan_id'],regret=audit['regret'],gap=audit['gap'],area=audit['area'])
                for k in ['total_seconds','service_seconds','controller_seconds','fault_seconds','screen_seconds','proof_seconds','fault_queries','screen_queries','proof_queries']:row[k]=result[k]
                h.dump(folder/'result.json',dict(result=result,audit=audit))
                with (folder/'trace.jsonl').open('w') as f:
                    for t in trace:f.write(json.dumps(t)+'\n')
                with (folder/'paid_ledger.jsonl').open('w') as f:
                    for t in svc.ledger:f.write(json.dumps(t)+'\n')
            except Exception as exc:
                row=dict(method=name,seed=seed,status='failed',error=str(exc),traceback=traceback.format_exc());h.dump(folder/'failure.json',row)
            rows.append(row);print('SIC_V3_STUDY',name,seed,row['status'],row.get('total_seconds'),row.get('plan_id'),flush=True)
            h.dump(OUT/'progress.json',dict(done=len(rows),total=len(METHODS)*len(SEEDS),elapsed=time.time()-started))
    h.dump(OUT/'results.json',rows);summary=[]
    for name in METHODS:
        rr=[r for r in rows if r['method']==name];ok=[r for r in rr if r['status']=='passed'];z=dict(method=name,trials=len(rr),passed=len(ok),all_passed=len(ok)==len(rr))
        if ok:
            for k in ['total_seconds','fault_queries','screen_queries','proof_queries','fault_seconds','screen_seconds','proof_seconds','controller_seconds','regret','gap','area']:z[k]=statistics.mean(r[k] for r in ok)
            z['unique_plans']=sorted({r['plan_id'] for r in ok})
        summary.append(z)
    grouped=next(x for x in summary if x['method']=='Grouped-Route' and x['all_passed']);comparisons=[]
    for z in summary:
        if z['method']=='Grouped-Route' or not z['all_passed']:continue
        comparisons.append(dict(comparator=z['method'],cost_reduction=1-grouped['total_seconds']/z['total_seconds'],
                                fault_query_reduction=1-grouped['fault_queries']/z['fault_queries'] if z['fault_queries'] else None))
    report=dict(status='completed' if all(r['status']=='passed' for r in rows) else 'completed_with_failures',runs=len(rows),elapsed=time.time()-started,summary=summary,comparisons=comparisons)
    h.dump(OUT/'summary.json',report)
    with (OUT/'summary.csv').open('w',newline='') as f:
        cols=['method','trials','passed','all_passed','total_seconds','fault_queries','screen_queries','proof_queries','fault_seconds','screen_seconds','proof_seconds','controller_seconds','regret','gap','area','unique_plans']
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader();[w.writerow({k:r.get(k) for k in cols}) for r in summary]
    print('SIC_V3_STUDY_COMPLETE',json.dumps(comparisons,sort_keys=True),flush=True)
if __name__=='__main__':main()
