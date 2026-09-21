import sys,json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'v095/src'),str(ROOT/'v030/src'),str(ROOT/'src')]
import hardware as h
from fault_runner import Runner
from ext_power_bench import make_tb,SCENARIOS,SCENARIO_COUNT
from fault_certificate import prove as fault_prove
OUT=ROOT/'v095/results/ext-asset-01';DESIGN='ext_motor_axis_ctrl';RTL=ROOT/'v095/rtl/ext_motor_axis_ctrl.v'

def compile_tb(cfg,folder,name,sources,physical,module='dut'):
    tb=make_tb(cfg,physical,folder/(name+'_tb.v'),module);exe=folder/(name+'.vvp')
    h.command(folder,name+'_compile',['iverilog','-g2012','-s','tb','-o',str(exe)]+list(map(str,sources))+[str(tb)])
    h.dump(folder/(name+'_bits.json'),physical);return exe

def normal_sweep(cfg,folder,name,exe,physical,seeds):
    r=Runner(cfg,folder,name,exe,physical,stream=False);rows=[]
    for seed in seeds:
        row=r.run(seed,domain='ext_power_no_internal_upset');rows.append(row)
        if row['label']!='NO_EFFECT':raise RuntimeError(('normal safety failure',name,seed,SCENARIOS[seed%SCENARIO_COUNT],row))
    return rows

def main():
    OUT.mkdir(parents=True,exist_ok=False);folder=OUT/DESIGN
    cfg=dict(design_id=DESIGN,family_id='external-spec-three-phase-power-controller',bench='ext_power_safety_v1',sources=[str(RTL)],window=144,cycle_min=8,cycle_max=130,transactions=1,output_gates={},
             population='12 power-controller workloads derived from public motor-control architecture requirements: complementary PWM, break-before-make deadtime, overcurrent/fault shutdown, stop/restart and explicit fault clear')
    start=time.time();mod,bits=h.prepare(cfg,folder);h.dump(folder/'config.json',cfg)
    baseline_phys=h.emit(mod,bits,folder/'comb.v',[],folder/'baseline.v')
    seeds=list(range(42000,42000+2*SCENARIO_COUNT))
    orig=compile_tb(cfg,folder,'original',[RTL],[],'top');base=compile_tb(cfg,folder,'baseline',[folder/'baseline.v'],baseline_phys)
    normal_sweep(cfg,folder,'original',orig,[],seeds);normal_sweep(cfg,folder,'baseline',base,baseline_phys,seeds)
    equiv=h.formal(cfg,folder,'baseline_equiv',folder/'baseline.v',mod['ports'])
    mapped,mp,hw=h.map_hardware(folder,'baseline',folder/'baseline.v',baseline_phys,mod['ports'])
    cells=h.library_models(folder);mexe=compile_tb(cfg,folder,'baseline_mapped',[cells,mapped],mp);normal_sweep(cfg,folder,'baseline_mapped',mexe,mp,seeds)
    print('V095_EXT_BASE_PASS',len(bits),hw['area'],hw['worst_setup_slack_ns'],flush=True)
    br=Runner(cfg,folder,'baseline',base,baseline_phys,stream=False);witness=None
    for bit in range(len(bits)):
        for seed in range(42000,42000+SCENARIO_COUNT):
            for cyc in [8,12,24,31,45,50,63,70,85,100,120]:
                row=br.run(seed,target=bit,cycle=cyc,domain='ext_power_positive_scan')
                if row['label']=='FAIL':witness=dict(bit=bit,seed=seed,scenario=SCENARIOS[seed%SCENARIO_COUNT],cycle=cyc,row=row);break
            if witness:break
        if witness:break
    if witness is None:raise RuntimeError('no positive internal-upset witness')
    positive=folder/'positive_tmr';positive.mkdir();sel=[witness['bit']]
    cphys=h.emit(mod,bits,folder/'comb.v',sel,positive/'candidate.v');cexe=compile_tb(cfg,positive,'candidate',[positive/'candidate.v'],cphys)
    pa=next(r for r in cphys if r['bit_id']==sel[0] and r['replica']=='A')
    protected=Runner(cfg,positive,'candidate',cexe,cphys,stream=False).run(witness['seed'],target=pa['physical_id'],cycle=witness['cycle'],domain='ext_power_tmr_positive')
    if protected['label']!='NO_EFFECT':raise RuntimeError(('positive TMR control failed',protected))
    cequiv=h.formal(cfg,folder,'positive_equiv',positive/'candidate.v',mod['ports'])
    cmapped,cmp_,chw=h.map_hardware(positive,'candidate',positive/'candidate.v',cphys,mod['ports']);compile_tb(cfg,positive,'candidate_mapped_sim',[h.library_models(positive),cmapped],cmp_)
    cert=fault_prove(folder,positive,OUT/'positive-fault-proof')
    if cert['status']!='passed':raise RuntimeError(cert)
    result=dict(status='passed',design_id=DESIGN,logical_bits=len(bits),baseline_hardware=hw,baseline_equivalence=equiv,positive_equivalence=cequiv,positive_witness=witness,protected_control=protected,positive_candidate_hardware=chw,fault_certificate=cert,normal_scenarios=SCENARIO_COUNT,
                external_spec='Clean-room implementation from Microchip PolarFire SoC motor-control public architecture documentation; not vendor RTL',elapsed=time.time()-start)
    h.dump(folder/'asset_ext_power.json',result);print('V095_EXT_ASSET_COMPLETE',json.dumps({k:result[k] for k in ['logical_bits','normal_scenarios','elapsed']}),flush=True)
if __name__=='__main__':main()
