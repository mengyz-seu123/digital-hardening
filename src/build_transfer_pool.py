import json, time, hashlib, statistics, collections
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
import hardware as h
from fault_runner import Runner
from transfer_bench import SCENARIOS
ASSET=ROOT/'results/transfer/assets/ext_motor_axis_ctrl';OUT=ROOT/'results/transfer/fault_pool'
M=32

def stress_class(s):
    if s in {3,4,5,7,8,11}:return 'protection'
    if s in {1,2,9,10}:return 'switching'
    return 'control'

def event_cycle(s,occ):
    base={0:24,1:24,2:31,3:48,4:68,5:30,6:58,7:49,8:54,9:40,10:38,11:83}[s]
    # Repeated occurrences move to a disjoint controller cycle while preserving
    # the same semantic workload class.
    return max(8,min(130,base+6*occ))

def main():
    OUT.mkdir(parents=True,exist_ok=False);cfg=json.loads((ASSET/'config.json').read_text());physical=json.loads((ASSET/'baseline_bits.json').read_text());N=len(physical);runner=Runner(cfg,OUT,'ext_power_pool',ASSET/'baseline.vvp',physical)
    # M=32 is required by the frozen finite-pool finite-pool service/certificate. Two
    # complete 12-scenario passes are followed by eight predeclared power-safety
    # repeats; this schedule is frozen before transfer fault labels are observed.
    scenario_ids=list(range(12))*2+[3,4,5,7,8,11,1,10];assert len(scenario_ids)==M
    seen=collections.Counter();events=[]
    for e,s in enumerate(scenario_ids):
        occ=seen[s];seen[s]+=1;seed=44400+e*12+s;assert seed%12==s
        events.append(dict(e=e,seed=seed,cycle=event_cycle(s,occ),scenario=SCENARIOS[s],scenario_id=s,occurrence=occ,stress_class=stress_class(s)))
    obs=[];start=time.time();invalid=0
    for i in range(N):
        arm=[]
        for ev in events:
            r=runner.run(ev['seed'],target=i,cycle=ev['cycle'],domain='ext_power_pool')
            if r['label']=='INVALID':invalid+=1;raise RuntimeError(('invalid',i,ev,r))
            eid=hashlib.sha256((f"{cfg['design_id']}|{i}|{ev['seed']}|{ev['cycle']}").encode()).hexdigest()[:20]
            arm.append(dict(y=int(r['label']=='FAIL'),cost_seconds=r['runtime'],event_id=eid,label=r['label'],seed=ev['seed'],cycle=ev['cycle'],scenario=ev['scenario'],scenario_id=ev['scenario_id'],stress_class=ev['stress_class'],result=r.get('result',{})))
        obs.append(arm)
        if i%10==9:print('TRANSFER_POOL_PROGRESS',i+1,'/',N,flush=True)
    rates=[sum(r['y'] for r in arm)/M for arm in obs];aliases=json.loads((ASSET/'logical_bits.json').read_text());ranked=sorted(range(N),key=lambda i:(-rates[i],i));classes=sorted({e['stress_class'] for e in events});by_class={}
    for cls in classes:
        vals=[]
        for arm in obs:
            rr=[x for x in arm if x['stress_class']==cls];vals.append(sum(x['y'] for x in rr)/len(rr))
        by_class[cls]=dict(mean_failure_rate=statistics.mean(vals),nonzero_bits=sum(x>0 for x in vals),max_failure_rate=max(vals),events_per_bit=sum(e['stress_class']==cls for e in events))
    hazard_keys=['shootthrough','deadtime_violation','missed_shutdown','false_trip','unsafe_restart','unsafe_gate_pulse'];hazard_events={k:sum(int(bool(r.get('result',{}).get(k,0))) for arm in obs for r in arm) for k in hazard_keys}
    summary=dict(status='passed',design_id=cfg['design_id'],logical_bits=N,events_per_bit=M,total_events=N*M,invalid=invalid,fail_events=sum(sum(r['y'] for r in arm) for arm in obs),nonzero_bits=sum(x>0 for x in rates),mean_failure_rate=statistics.mean(rates),max_failure_rate=max(rates),elapsed=time.time()-start,by_stress_class=by_class,hazard_event_counts=hazard_events,top_bits=[dict(bit=i,rate=rates[i],aliases=aliases[i].get('aliases',[]),source_cell=aliases[i].get('source_cell')) for i in ranked[:20]],event_schedule=events,claim_scope='Finite M=32 single-storage-upset campaign on independent external-spec power-controller workload; compatible with frozen finite-pool certificate implementation')
    h.dump(OUT/'obs.json',obs);h.dump(OUT/'summary.json',summary);print('TRANSFER_POOL_COMPLETE',json.dumps({k:summary[k] for k in ['total_events','fail_events','nonzero_bits','mean_failure_rate','max_failure_rate','elapsed']}),flush=True)
    for x in summary['top_bits'][:10]:print('TRANSFER_TOP',x,flush=True)
if __name__=='__main__':main()
