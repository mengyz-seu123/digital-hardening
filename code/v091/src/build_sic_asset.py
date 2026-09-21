import sys,json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'v091/src'),str(ROOT/'v030/src'),str(ROOT/'src')]
import hardware as h
from fault_runner import Runner
from sic_stress_bench import make_tb,SCENARIOS,SCENARIO_COUNT
from fault_certificate import prove as fault_prove
OUT=ROOT/'v091/results/sic-asset-01'; DESIGN='sic_gate_ctrl_v3'
RTL=ROOT/'v091/rtl/sic_gate_driver_ctrl_v3.v'


def compile_tb(cfg,folder,name,sources,physical,module='dut'):
    tb=make_tb(cfg,physical,folder/(name+'_tb.v'),module)
    exe=folder/(name+'.vvp')
    h.command(folder,name+'_compile',['iverilog','-g2012','-s','tb','-o',str(exe)]+list(map(str,sources))+[str(tb)])
    h.dump(folder/(name+'_bits.json'),physical)
    return exe


def normal_sweep(cfg,folder,name,exe,physical,seeds):
    r=Runner(cfg,folder,name,exe,physical,stream=False);rows=[]
    for seed in seeds:
        row=r.run(seed,domain='sic_v3_no_internal_upset');rows.append(row)
        if row['label']!='NO_EFFECT':
            raise RuntimeError(('normal safety failure',name,seed,SCENARIOS[seed%SCENARIO_COUNT],row))
    return rows


def build():
    OUT.mkdir(parents=True,exist_ok=False);folder=OUT/DESIGN
    cfg=dict(design_id=DESIGN,family_id='sic-smart-gate-driver-public-model-v3',bench='sic_gate_stress_v3',
             sources=[str(RTL)],window=192,cycle_min=8,cycle_max=170,transactions=1,output_gates={},
             population='24 balanced SiC gate-driver workloads spanning dynamic-CMTI/dVdt, DESAT blanking and short-circuit protection race, Miller-clamp commutation, OC, UVLO, thermal derating and hot timing margin')
    start=time.time();mod,bits=h.prepare(cfg,folder);h.dump(folder/'config.json',cfg)
    baseline_phys=h.emit(mod,bits,folder/'comb.v',[],folder/'baseline.v')
    seeds=list(range(18000,18048))
    assert [s%SCENARIO_COUNT for s in seeds[:SCENARIO_COUNT]]==list(range(SCENARIO_COUNT))
    orig=compile_tb(cfg,folder,'original',[RTL],[],'top')
    base=compile_tb(cfg,folder,'baseline',[folder/'baseline.v'],baseline_phys)
    normal_sweep(cfg,folder,'original',orig,[],seeds)
    normal_sweep(cfg,folder,'baseline',base,baseline_phys,seeds)
    equiv=h.formal(cfg,folder,'baseline_equiv',folder/'baseline.v',mod['ports'])
    mapped,mp,hw=h.map_hardware(folder,'baseline',folder/'baseline.v',baseline_phys,mod['ports'])
    cells=h.library_models(folder);mexe=compile_tb(cfg,folder,'baseline_mapped',[cells,mapped],mp)
    mapped_rows=normal_sweep(cfg,folder,'baseline_mapped',mexe,mp,seeds)
    print('SIC_V3_BASE_PASS',len(bits),hw['area'],hw['worst_setup_slack_ns'],flush=True)

    br=Runner(cfg,folder,'baseline',base,baseline_phys,stream=False);witness=None
    probe_seeds=list(range(18000,18024));probe_cycles=[8,12,18,24,30,34,40,55,60,80,100,130,160]
    for bit in range(len(bits)):
        for seed in probe_seeds:
            for cyc in probe_cycles:
                row=br.run(seed,target=bit,cycle=cyc,domain='sic_v3_positive_scan')
                if row['label']=='FAIL':
                    witness=dict(bit=bit,seed=seed,scenario=SCENARIOS[seed%SCENARIO_COUNT],cycle=cyc,row=row);break
            if witness:break
        if witness:break
    if witness is None:raise RuntimeError('No internal state upset produced a safety violation')
    print('SIC_V3_POSITIVE_WITNESS',witness['bit'],witness['scenario'],witness['cycle'],flush=True)
    positive=folder/'positive_tmr';positive.mkdir();sel=[witness['bit']]
    cphys=h.emit(mod,bits,folder/'comb.v',sel,positive/'candidate.v')
    cexe=compile_tb(cfg,positive,'candidate',[positive/'candidate.v'],cphys)
    pa=next(r for r in cphys if r['bit_id']==sel[0] and r['replica']=='A')
    protected=Runner(cfg,positive,'candidate',cexe,cphys,stream=False).run(
        witness['seed'],target=pa['physical_id'],cycle=witness['cycle'],domain='sic_v3_tmr_positive_control')
    if protected['label']!='NO_EFFECT':raise RuntimeError(('TMR failed positive control',protected))
    cequiv=h.formal(cfg,folder,'positive_equiv',positive/'candidate.v',mod['ports'])
    cmapped,cmp_,chw=h.map_hardware(positive,'candidate',positive/'candidate.v',cphys,mod['ports'])
    ccells=h.library_models(positive);cmexe=compile_tb(cfg,positive,'candidate_mapped_sim',[ccells,cmapped],cmp_)
    normal_sweep(cfg,positive,'candidate_mapped_sim',cmexe,cmp_,seeds[:24])
    cert=fault_prove(folder,positive,OUT/'positive-fault-proof')
    if cert['status']!='passed':raise RuntimeError(('fault certificate failed',cert))
    result=dict(status='passed',design_id=DESIGN,logical_bits=len(bits),baseline_hardware=hw,
                baseline_equivalence=equiv,positive_equivalence=cequiv,positive_witness=witness,
                protected_control=protected,positive_candidate_hardware=chw,fault_certificate=cert,
                normal_scenarios=len(SCENARIOS),normal_seeds=len(seeds),mapped_normal_seeds=len(mapped_rows),
                protection_timing=dict(clock_ns=10,desat_blanking_cycles=25,desat_blanking_ns=250,late_trip_cycles=50,late_trip_ns=500,severe_shutdown_deadline_cycles=100,severe_shutdown_deadline_ns=1000),
                physical_claim='CMTI/dVdt, Miller and temperature cases are digital workload/timing proxies; no analog gate waveform or physical FIT mapping is claimed',
                elapsed=time.time()-start)
    h.dump(folder/'asset_sic_v3.json',result)
    print('SIC_V3_ASSET_PASS',json.dumps({k:result[k] for k in ['status','logical_bits','normal_scenarios','elapsed']}),flush=True)
if __name__=='__main__':build()
