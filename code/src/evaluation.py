import argparse, concurrent.futures, hashlib, json, math, shutil, time, traceback
from pathlib import Path
import numpy as np
import hardware as h
from assets import load
from fault_runner import Runner, compile_sim
from build_design_data import event, IDS
from oracle import Oracle


def wilson(f,n):
    z=1.959963984540054;p=f/n;den=1+z*z/n
    center=(p+z*z/(2*n))/den;half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [center-half,center+half]


def dense_reference(root, design):
    folder=root/'reference'/design;folder.mkdir(parents=True,exist_ok=False)
    asset=root/'assets'/design;original=load(asset,'original',True);base=load(asset,'baseline',True)
    original.folder=folder;base.folder=folder;cfg=base.cfg;counts=[];begin=time.time()
    try:
        for bit in range(len(base.physical)):
            failures=alarms=0
            for j in range(128):
                seed,rng=event(design,'reference',bit,j)
                assert original.run(seed,domain='golden')['label']=='NO_EFFECT'
                row=base.run(seed,bit,rng.randint(cfg['cycle_min'],cfg['cycle_max']),domain='reference_events')
                assert row['label']!='INVALID',row
                failures+=row['label']=='FAIL';alarms+=row['label']=='ALARM'
            counts.append(dict(bit_id=bit,n=128,f=failures,alarms=alarms))
        costs=json.loads((root/'costs'/design/'costs_v2.json').read_text())
        oracle=Oracle([c['proxy_area'] for c in costs['costs']],.2*costs['baseline_area'])
        selected,value=oracle([c['f']/c['n'] for c in counts])
        reference=dict(design_id=design,counts=counts,selected=np.flatnonzero(selected).tolist(),
            proxy_value=value,events=len(counts)*128,elapsed=time.time()-begin,
            scope='finite independent reference-event solution, not a physical or global optimum')
        h.dump(folder/'reference.json',reference)
        for j in range(512):
            seed,_=event(design,'evaluation',0,j)
            assert original.run(seed,domain='evaluation_golden')['label']=='NO_EFFECT'
        h.dump(folder/'evaluation_golden.json',dict(status='passed',seeds=512))
        print('REFERENCE_READY',design,len(counts)*128,'selected',reference['selected'],flush=True)
        return reference
    finally:original.stream.close();base.stream.close()


def plan_id(selected):
    return 'baseline' if not selected else 'tmr_'+hashlib.sha256(json.dumps(sorted(selected)).encode()).hexdigest()[:12]


def evaluate_variant(root, design, selected):
    asset=root/'assets'/design;folder=root/'hardware'/design/plan_id(selected)
    folder.mkdir(parents=True,exist_ok=False);begin=time.time();streams=[]
    cfg=json.loads((asset/'config.json').read_text());mod=json.loads((asset/'frozen.json').read_text())['modules']['top']
    bits=json.loads((asset/'logical_bits.json').read_text());basehw=json.loads((asset/'baseline_hardware.json').read_text())
    try:
        if selected:
            for name in ['frozen.json','frozen.v','logical_bits.json']:shutil.copyfile(asset/name,folder/name)
            phy=h.emit(mod,bits,asset/'comb.v',selected,folder/'candidate.v')
            proofs=[h.formal(cfg,folder,'candidate',folder/'candidate.v',mod['ports'])]
            mapped,mphy,hw=h.map_hardware(folder,'candidate',folder/'candidate.v',phy,mod['ports'])
            proofs.append(h.formal(cfg,folder,'candidate_mapped',mapped,mod['ports'],[asset/'cells.v']))
            exe=compile_sim(cfg,folder,'candidate_mapped',[asset/'cells.v',mapped],mphy)
            canonical_exe=compile_sim(cfg,folder,'candidate',[folder/'candidate.v'],phy)
            runner=Runner(cfg,folder,'candidate_mapped',exe,mphy,stream=True)
            canonical=Runner(cfg,folder,'candidate',canonical_exe,phy,stream=True)
            fresh=Runner(cfg,folder,'candidate_mapped',exe,mphy,stream=False)
        else:
            runner=load(asset,'baseline_mapped',True);canonical=load(asset,'baseline',True)
            fresh=load(asset,'baseline_mapped',False);hw=basehw;proofs=['reused accepted baseline proofs']
            for r in [runner,canonical,fresh]:r.folder=folder
        streams=[runner,canonical]
        area_ratio=(hw['area']-basehw['area'])/basehw['area']
        feasible=area_ratio<=.2+1e-9 and hw['timing_feasible']
        rows=[];seen=set();canonical_differences=0;window_changes=0
        for j in range(512):
            seed,rng=event(design,'evaluation',0,j)
            target=int(rng.random()*len(runner.physical))
            cycle=cfg['cycle_min']+int(rng.random()*(cfg['cycle_max']-cfg['cycle_min']+1))
            assert runner.run(seed,domain='normal_evaluation')['label']=='NO_EFFECT'
            row=runner.run(seed,target,cycle,domain='evaluation_events');assert row['label']!='INVALID',row
            rows.append(row);seen.add(target)
            if j<32:
                other=canonical.run(seed,target,cycle,domain='canonical_audit')
                assert other['label']!='INVALID',other
                canonical_differences+=other['label']!=row['label']
                extended=runner.run(seed,target,cycle,window=cfg['window']*2,domain='window_audit')
                assert extended['label']!='INVALID',extended
                window_changes+=extended['label']!=row['label']
            if j<8:
                check=fresh.run(seed,target,cycle,domain='standalone_audit')
                assert check['result']==row['result'],(check,row)
        missing=sorted(set(range(len(runner.physical)))-seen)
        for target in missing:
            seed,_=event(design,'evaluation',0,3000+target)
            check=runner.run(seed,target,(cfg['cycle_min']+cfg['cycle_max'])//2,domain='coverage_audit')
            assert check['label']!='INVALID',check
        failures=sum(r['label']=='FAIL' for r in rows);alarms=sum(r['label']=='ALARM' for r in rows)
        exposure=len(runner.physical)/len(bits);ci=wilson(failures,len(rows))
        protected=[r for r in rows if r['bit_id'] in selected]
        result=dict(status='completed',design_id=design,plan_id=plan_id(selected),selected=selected,
            feasible=bool(feasible),hardware=hw,area_increase_ratio=area_ratio,formal=proofs,
            evaluation_events=512,failures=failures,alarms=alarms,invalid=0,
            conditional_failure_rate=failures/512,conditional_wilson95=ci,exposure_ratio=exposure,
            exposure_adjusted_risk=exposure*failures/512,exposure_adjusted_wilson95=[exposure*x for x in ci],
            physical_bits=len(runner.physical),randomly_covered_physical_bits=len(seen),
            extra_coverage_audit_events=len(missing),coverage_including_audit=1.,
            protected_replica_evaluation_events=len(protected),protected_replica_failures=sum(r['label']=='FAIL' for r in protected),
            canonical_mapped_label_differences_32=canonical_differences,window_label_changes_32=window_changes,
            standalone_replays=8,normal_evaluation_runs=512,elapsed=time.time()-begin,
            audits_excluded_from_independent_denominator=True)
        h.dump(folder/'result.json',result)
        print('HARDWARE_DONE',design,plan_id(selected),'feasible',feasible,'risk',round(result['exposure_adjusted_risk'],6),flush=True)
        return result
    except Exception as e:
        result=dict(status='failed',design_id=design,plan_id=plan_id(selected),selected=selected,feasible=False,
            error=str(e),traceback=traceback.format_exc(),elapsed=time.time()-begin)
        h.dump(folder/'result.json',result);print('HARDWARE_FAILED',design,plan_id(selected),str(e)[-300:],flush=True);return result
    finally:
        for r in streams:r.stream.close()


def run_design(root, design, workers):
    summary=json.loads((root/'queries'/design/'summary.json').read_text());assert summary['status']=='completed'
    checkpoints=[json.loads(s) for s in (root/'queries'/design/'checkpoints.jsonl').read_text().splitlines()]
    assert len(checkpoints)==80
    refpath=root/'reference'/design/'reference.json'
    if refpath.exists():reference=json.loads(refpath.read_text())
    else:reference=dense_reference(root,design)
    plans={tuple(sorted(r['selected'])) for r in checkpoints};plans.add(());plans.add(tuple(reference['selected']))
    h.dump(root/'hardware'/design/'plan_registry.json',dict(plans=[list(x) for x in sorted(plans)],
        checkpoints_sha256=h.sha(root/'queries'/design/'checkpoints.jsonl'),reference_sha256=h.sha(refpath)))
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        jobs=[]
        for plan in sorted(plans,key=lambda x:(len(x),x)):
            existing=root/'hardware'/design/plan_id(plan)/'result.json'
            if existing.exists():results.append(json.loads(existing.read_text()))
            else:jobs.append(pool.submit(evaluate_variant,root,design,list(plan)))
        for job in concurrent.futures.as_completed(jobs):results.append(job.result())
    result=dict(design_id=design,status='completed' if all(r['status']=='completed' for r in results) else 'completed_with_failures',
        plans=len(results),feasible=sum(r.get('feasible',False) for r in results),results=results)
    h.dump(root/'hardware'/design/'summary.json',result)
    print('DESIGN_EVALUATED',design,len(results),'feasible',result['feasible'],flush=True)
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--run',required=True);ap.add_argument('--design',required=True)
    ap.add_argument('--workers',type=int,default=2);a=ap.parse_args();assert 1<=a.workers<=4
    root=h.PROJECT/'runs'/a.run;protocol=root/'evaluation_protocol.json'
    if not protocol.exists():
        h.dump(protocol,dict(frozen_before_evaluation_labels=True,events_per_unique_hardware=512,
            independent_reference_per_bit=128,area_ratio_limit=.2,clock_ns=10,setup_min=0,hold_min=0,
            normal_run_required_for_every_evaluation_seed=True,window_multiplier=2,window_audit_events=32,
            standalone_replays=8,missing_physical_bits_tested_in_separate_audit=True,
            deployment_policy='Reject over-area, over-timing or unverified candidates; retain candidate result and explicitly score unchanged baseline deployment.',
            infeasible_cases_never_dropped_from_method_averages=True,
            repetitions_and_audits_excluded_from_independent_denominator=True,
            interval='Wilson95 per hardware on the pseudorandom event model; exposure scaling is not physical FIT',
            simulation_contract_sha256=h.sha(h.PROJECT/'src/benches.py'),
            label_runner_sha256=h.sha(h.PROJECT/'src/fault_runner.py')))
        snapshot=root/'evaluation_source';snapshot.mkdir()
        for p in (h.PROJECT/'src').iterdir():
            if p.is_file():shutil.copyfile(p,snapshot/p.name)
        h.dump(root/'evaluation_source_manifest.json',{p.name:h.sha(p) for p in snapshot.iterdir()})
    run_design(root,a.design,a.workers)
