import json, time, hashlib, statistics, collections
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
import hardware as h
from fault_runner import Runner
from primary_bench import (SCENARIOS,DV_DT_SCENARIOS,THERMAL_SCENARIOS,PROTECTION_SCENARIOS,
                              DESAT_BLANK_SCENARIOS,MILLER_SCENARIOS,MILLER_LOW_SCENARIOS,MILLER_HIGH_SCENARIOS)
ASSET=ROOT/'results/primary/assets/primary_controller';OUT=ROOT/'results/primary/fault_pool'


def stress_class(s):
    if s in MILLER_SCENARIOS:return 'miller_commutation'
    if s in DESAT_BLANK_SCENARIOS:return 'desat_blanking'
    if s in THERMAL_SCENARIOS:return 'thermal'
    if s in PROTECTION_SCENARIOS:return 'protection'
    if s in DV_DT_SCENARIOS:return 'dvdt_window'
    return 'normal'


def event_cycle(s,occ):
    if s in MILLER_LOW_SCENARIOS:return 7+occ
    if s in MILLER_HIGH_SCENARIOS:return 56+occ
    if s==16:return [12,20][occ%2]
    if s==17:return [18,24][occ%2]
    if s==18:return [12,24][occ%2]
    if s==23:return [18,24][occ%2]
    if s==6:return 30
    if s==8:return 79
    if s==9:return 71
    if s in (10,14):return 102
    if s==13:return 55
    if s in DV_DT_SCENARIOS:return [7,40,56,88][occ%4]
    return 12+((s*7+occ*19)%150)


def main():
    OUT.mkdir(parents=True,exist_ok=False)
    cfg=json.loads((ASSET/'config.json').read_text());physical=json.loads((ASSET/'baseline_bits.json').read_text())
    N=len(physical);runner=Runner(cfg,OUT,'primary_pool',ASSET/'baseline.vvp',physical)
    scenario_ids=list(range(24))+list(range(16,24));assert len(scenario_ids)==32
    seen=collections.Counter();events=[]
    for e,s in enumerate(scenario_ids):
        occ=seen[s];seen[s]+=1;seed=20160+e*24+s
        assert seed%24==s
        events.append(dict(e=e,seed=seed,cycle=event_cycle(s,occ),scenario=SCENARIOS[s],scenario_id=s,
                           occurrence=occ,stress_class=stress_class(s)))
    obs=[];start=time.time();invalid=0
    for i in range(N):
        rows=[]
        for ev in events:
            r=runner.run(ev['seed'],target=i,cycle=ev['cycle'],domain='primary_pool')
            if r['label']=='INVALID':invalid+=1;raise RuntimeError(('invalid',i,ev,r))
            eid=hashlib.sha256(('%s|%d|%d|%d'%(cfg['design_id'],i,ev['seed'],ev['cycle'])).encode()).hexdigest()[:20]
            rows.append(dict(y=int(r['label']=='FAIL'),cost_seconds=r['runtime'],event_id=eid,label=r['label'],
                             seed=ev['seed'],cycle=ev['cycle'],scenario=ev['scenario'],scenario_id=ev['scenario_id'],
                             stress_class=ev['stress_class'],result=r.get('result',{})))
        obs.append(rows)
        if i%10==9:print('PRIMARY_POOL_PROGRESS',i+1,'/',N,flush=True)
    rates=[sum(r['y'] for r in rows)/32 for rows in obs]
    aliases=json.loads((ASSET/'logical_bits.json').read_text());ranked=sorted(range(N),key=lambda i:(-rates[i],i))
    classes=sorted({x['stress_class'] for x in events});by_class={}
    for cls in classes:
        vals=[]
        for rows in obs:
            rr=[x for x in rows if x['stress_class']==cls];vals.append(sum(x['y'] for x in rr)/len(rr) if rr else 0.)
        by_class[cls]=dict(mean_failure_rate=statistics.mean(vals),nonzero_bits=sum(x>0 for x in vals),max_failure_rate=max(vals),
                           events_per_bit=sum(1 for x in events if x['stress_class']==cls))
    hazard_keys=['shootthrough','deadtime_violation','missed_shutdown','late_shutdown','false_trip','desat_blanking_violation',
                 'miller_clamp_violation','parasitic_turnon_proxy','thermal_derate_violation','unsafe_gate_pulse']
    hazard_events={k:sum(int(bool(r.get('result',{}).get(k,0))) for arm in obs for r in arm) for k in hazard_keys}
    summary=dict(status='passed',design_id=cfg['design_id'],logical_bits=N,events_per_bit=32,total_events=N*32,invalid=invalid,
                 fail_events=sum(sum(r['y'] for r in x) for x in obs),nonzero_bits=sum(x>0 for x in rates),
                 mean_failure_rate=statistics.mean(rates),max_failure_rate=max(rates),elapsed=time.time()-start,by_stress_class=by_class,
                 hazard_event_counts=hazard_events,
                 top_bits=[dict(bit=i,rate=rates[i],aliases=aliases[i].get('aliases',[]),source_cell=aliases[i].get('source_cell')) for i in ranked[:25]],
                 event_schedule=events,
                 claim_scope='Finite M=32 internal single-storage-upset campaign under SiC gate-driver digital safety workloads. DESAT blanking and Miller cases are application-level digital proxies; no physical FIT model.')
    h.dump(OUT/'obs.json',obs);h.dump(OUT/'summary.json',summary)
    print('PRIMARY_POOL_PASS',json.dumps({k:summary[k] for k in ['total_events','fail_events','nonzero_bits','mean_failure_rate','max_failure_rate','elapsed']}),flush=True)
    print('PRIMARY_POOL_CLASSES',json.dumps(by_class,sort_keys=True),flush=True)
    print('PRIMARY_POOL_HAZARDS',json.dumps(hazard_events,sort_keys=True),flush=True)
    for x in summary['top_bits'][:15]:print('PRIMARY_TOP',x,flush=True)
if __name__=='__main__':main()
