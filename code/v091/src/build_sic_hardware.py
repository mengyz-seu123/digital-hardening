import sys,json,time,shutil,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'v091/src'),str(ROOT/'v030/src'),str(ROOT/'src')]
import hardware as h, catalogue3
from sic_stress_bench import make_tb,SCENARIOS,SCENARIO_COUNT
from fault_runner import Runner
from fault_certificate import prove as fault_prove
ASSET=ROOT/'v091/results/sic-asset-01/sic_gate_ctrl_v3'
OUT=ROOT/'v091/results/sic-hardware-01';DESIGN='sic_gate_ctrl_v3'


def compile_mapped(cfg,folder,name,mapped,physical):
    cells=h.library_models(folder);tb=make_tb(cfg,physical,folder/(name+'_tb.v'),'dut')
    exe=folder/(name+'.vvp')
    h.command(folder,name+'_compile',['iverilog','-g2012','-s','tb','-o',str(exe),str(cells),str(mapped),str(tb)])
    h.dump(folder/(name+'_bits.json'),physical);return exe


def normal_accept(cfg,folder,name,exe,physical):
    r=Runner(cfg,folder,name,exe,physical,stream=False);rows=[]
    seeds=list(range(19200,19200+SCENARIO_COUNT))
    for seed in seeds:rows.append(r.run(seed,domain='sic_v3_candidate_screen'))
    return all(x['label']=='NO_EFFECT' for x in rows),rows


def main():
    OUT.mkdir(parents=True,exist_ok=False)
    cfg=json.loads((ASSET/'config.json').read_text());frozen=json.loads((ASSET/'frozen.json').read_text());mod=frozen['modules']['top']
    bits=json.loads((ASSET/'logical_bits.json').read_text());basehw=json.loads((ASSET/'baseline_hardware.json').read_text());area0=basehw['area']
    catalogue3.PERIODS[DESIGN]=10;catalogue3.ASSETS[DESIGN]=ASSET
    cat=catalogue3.generate(DESIGN,ASSET)
    cat['role']='SiC v3 protection-race case study; deterministic label-free generator frozen before v3 fault labels'
    cat['asset']=str(ASSET);h.dump(OUT/'catalog.json',cat)
    h.dump(OUT/'protocol.json',dict(status='frozen',fault_labels_used=False,candidate_generator='v030 catalogue3.generate unchanged',
        caveat='candidate construction uses RTL/hardware metadata only and is executed before sic-pool-01',area_budget=.2,
        normal_screen_seeds=list(range(19200,19200+SCENARIO_COUNT)),normal_screen_scenarios=SCENARIOS,candidate_count=len(cat['plans'])))
    records=[];start=time.time()
    baseline=dict(plan_id=cat['plans'][0]['plan_id'],selected=[],area_ratio=0.,sta=dict(feasible=True,setup_slack_ns=basehw['worst_setup_slack_ns'],
                  setup_hold_slacks_ns=basehw['setup_hold_reported_slacks_ns']),feasible=True,cost_seconds=0.,screen_seconds=0.,proof_seconds=0.,
                  hardware=basehw,fault_certificate=dict(status='passed',elapsed=0.,scope='baseline'),normal_acceptance=True)
    records.append(baseline)
    for j,plan in enumerate(cat['plans'][1:],1):
        folder=OUT/'hardware'/plan['plan_id'];folder.mkdir(parents=True)
        sel=plan['selected'];t0=time.time();phys=h.emit(mod,bits,ASSET/'comb.v',sel,folder/'candidate.v')
        mapped,mp,hw=h.map_hardware(folder,'candidate',folder/'candidate.v',phys,mod['ports'])
        exe=compile_mapped(cfg,folder,'candidate_mapped_sim',mapped,mp);normal_ok,_=normal_accept(cfg,folder,'candidate_mapped_sim',exe,mp)
        screen_seconds=time.time()-t0;area_ratio=(hw['area']-area0)/area0
        screen_pass=bool(area_ratio<=.2+1e-12 and hw['timing_feasible'] and normal_ok)
        proof_seconds=0.;cert=dict(status='not_run');equiv=dict(status='not_run')
        if screen_pass:
            p0=time.time();shutil.copy2(ASSET/'frozen.v',folder/'frozen.v')
            equiv=h.formal(cfg,folder,'candidate_equiv',folder/'candidate.v',mod['ports'])
            cert=fault_prove(ASSET,folder,folder/'fault-proof');proof_seconds=time.time()-p0
            if equiv['status']!='passed' or cert['status']!='passed':raise RuntimeError(('proof failure',plan['plan_id'],equiv,cert))
        rec=dict(plan_id=plan['plan_id'],selected=sel,origin=plan['origin'],area_ratio=area_ratio,
                 sta=dict(feasible=bool(hw['timing_feasible'] and normal_ok),setup_slack_ns=hw['worst_setup_slack_ns'],
                          setup_hold_slacks_ns=hw['setup_hold_reported_slacks_ns'],hot_margin_ge_0p5ns=bool(hw['worst_setup_slack_ns']>=.5),
                          hot_margin_ge_1p0ns=bool(hw['worst_setup_slack_ns']>=1.)),
                 feasible=screen_pass and cert.get('status')=='passed',cost_seconds=screen_seconds+proof_seconds,
                 screen_seconds=screen_seconds,proof_seconds=proof_seconds,hardware=hw,fault_certificate=cert,equivalence=equiv,normal_acceptance=normal_ok)
        records.append(rec);h.dump(folder/'result.json',rec)
        print('SIC_V3_HW',j,'/',len(cat['plans'])-1,plan['plan_id'],'k',len(sel),'area',round(area_ratio,4),'screen',screen_pass,'proof',cert.get('status'),flush=True)
    h.dump(OUT/'records.json',records);passed=[r for r in records if r['feasible']]
    summary=dict(status='passed',candidate_count=len(records),feasible=len(passed),infeasible=len(records)-len(passed),
                 all_proofs_passed=all(r['fault_certificate']['status']=='passed' for r in passed),
                 mean_screen_seconds=statistics.mean(r['screen_seconds'] for r in records[1:]),
                 mean_proof_seconds=statistics.mean(r['proof_seconds'] for r in passed[1:]) if len(passed)>1 else 0.,
                 hot_margin_ge_0p5ns=sum(bool(r['sta'].get('hot_margin_ge_0p5ns',True)) for r in passed),
                 hot_margin_ge_1p0ns=sum(bool(r['sta'].get('hot_margin_ge_1p0ns',True)) for r in passed),elapsed=time.time()-start,
                 candidates=[dict(plan_id=r['plan_id'],k=len(r['selected']),area_ratio=r['area_ratio'],feasible=r['feasible'],
                                  setup_slack_ns=r['sta']['setup_slack_ns'],screen_seconds=r['screen_seconds'],proof_seconds=r['proof_seconds']) for r in records])
    h.dump(OUT/'summary.json',summary)
    print('SIC_V3_HW_COMPLETE',json.dumps({k:summary[k] for k in ['candidate_count','feasible','infeasible','all_proofs_passed','elapsed']}),flush=True)
if __name__=='__main__':main()
