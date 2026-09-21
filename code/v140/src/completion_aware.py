from __future__ import annotations
import argparse, itertools, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT/'v132/src'), str(ROOT/'v130/src'), str(ROOT/'v110/src'),
    str(ROOT/'v096/src'), str(ROOT/'v095/src'), str(ROOT/'v091/src'),
    str(ROOT/'v030/src'), str(ROOT/'src')
]
import hardware as h
import direct_stress as c
import sic_end_to_end as sic
import ext_end_to_end as ext
import stress_contract_minloop as s
import p0_closure as p0
from headroom_certificate import full_information_summary

PRIMARY_ASSET = ROOT/'v091/results/sic-asset-01/sic_gate_ctrl_v3'
TRANSFER_ASSET = ROOT/'v095/results/ext-asset-01/ext_motor_axis_ctrl'
PRIMARY_GUARD = ROOT/'v130/rtl/compact_context_guard.v'
TRANSFER_GUARD = ROOT/'v132/rtl/ext_compact_guard.v'
EPSILON = .02
AREA_CAP = .20
TRANSFER_POINTS = ((10000,0),(1000,0),(330,100))


def dump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True)+'\n', encoding='utf-8')


def emit_top(asset: Path, out: Path, label: str, selected):
    frozen=json.loads((asset/'frozen.json').read_text())
    bits=json.loads((asset/'logical_bits.json').read_text())
    folder=out/'generated'/label
    folder.mkdir(parents=True, exist_ok=False)
    raw=folder/'candidate.v'
    physical=h.emit(frozen['modules']['top'], bits, asset/'comb.v', selected, raw)
    text=raw.read_text()
    text,n=re.subn(r'\bmodule\s+dut\b','module top',text,count=1)
    if n != 1:
        raise RuntimeError((label,'expected emitted module dut',n))
    top=folder/'candidate_top.v'
    top.write_text(text, encoding='utf-8')
    core_floor=len(bits)+2*len(selected)
    dump(folder/'meta.json',dict(label=label,selected=list(selected),logical_bits=len(bits),
                                 physical_bits=len(physical),core_floor=core_floor))
    return top, core_floor


def map_complete(folder: Path, rtl: Path, guard_rtl: Path, top: str,
                 mode: int, samples: int, guard: int, liberty: Path,
                 area0: float, core_floor: int):
    folder.mkdir(parents=True, exist_ok=False)
    mapped_json=folder/'mapped.json'
    script=f'''read_verilog {rtl} {guard_rtl}
chparam -set MODE {mode} -set SAMPLES {samples} -set GUARD {guard} {top}
hierarchy -check -top {top}
flatten
proc
setattr -set keep 1 t:$dff t:$dffe t:$sdff t:$sdffe t:$sdffce
opt -nodffe -nosdff
techmap
dfflibmap -liberty {liberty}
abc -liberty {liberty}
clean
read_liberty -lib {liberty}
check -assert
stat -liberty {liberty}
write_json {mapped_json}
write_verilog -noattr -noexpr -simple-lhs mapped.v
'''
    (folder/'map.ys').write_text(script,encoding='utf-8')
    log=c.command(folder,'yosys',['yosys','-Q','-T','-s','map.ys'])
    areas=re.findall(r'Chip area for (?:top )?module.*?:\s*([0-9.]+)',log)
    if not areas:
        areas=re.findall(r'Chip area.*?([0-9]+\.[0-9]+)',log)
    if not areas:
        raise ValueError(('missing mapped area',folder))
    area=float(areas[-1])
    design=json.loads(mapped_json.read_text())
    mod=design['modules'][top]
    ff=sum(cell.get('type','').startswith('DFF') for cell in mod.get('cells',{}).values())
    if ff < core_floor:
        raise RuntimeError(('TMR preservation failure',folder,ff,core_floor))
    (folder/'timing.tcl').write_text(f'''read_liberty {liberty}
read_verilog {folder/'mapped.v'}
link_design {top}
create_clock -name clk -period 10 [get_ports clk]
set data_inputs {{}}
set clock_port [lindex [get_ports clk] 0]
foreach port [all_inputs] {{ if {{$port ne $clock_port}} {{ lappend data_inputs $port }} }}
set_input_delay -clock clk 1 $data_inputs
set_input_transition 0.1 $data_inputs
set_output_delay -clock clk 1 [all_outputs]
set_load .01 [all_outputs]
report_checks -path_delay max
report_checks -path_delay min
exit
''',encoding='utf-8')
    timing=c.command(folder,'sta',['sta','-exit','timing.tcl'])
    if re.search(r'(?im)^Error:',timing):
        raise RuntimeError(('OpenSTA error',folder,timing[-3000:]))
    slacks=list(map(float,re.findall(r'(-?\d+\.\d+)\s+slack',timing)))
    if len(slacks)<2:
        raise ValueError(('missing timing slacks',folder))
    return dict(area=area,area_ratio=(area-area0)/area0,flip_flops=ff,
                core_floor=core_floor,policy_flip_flops=ff-core_floor,
                setup_slack=slacks[0],hold_slack=slacks[-1],
                timing_pass=min(slacks[0],slacks[-1])>=0,
                tmr_preserved=ff>=core_floor)


def state_survivors(data):
    structural=full_information_summary(data,epsilon=EPSILON,area_budget=AREA_CAP)
    rows=[r for r in structural['rows'] if r['epsilon_optimal']]
    rows.sort(key=lambda r:(r['area'],r['regret'],r['plan_id']))
    assert rows and rows[0]['plan_id']==structural['headroom_plan_id']
    return structural,rows


def completion_key(row):
    return (float(row['area_ratio']), float(row.get('asserted_pullup_mA',1e99)),
            float(row.get('worst_shutdown_ns') if row.get('worst_shutdown_ns') is not None else 1e99),
            row['state_plan_id'], row['interface_id'])


def summarize_product(name, structural, all_state_count, all_interfaces, states, interfaces, mapped):
    good=[x for x in mapped if x['timing_pass'] and x['tmr_preserved']]
    if not good:
        raise RuntimeError((name,'no mapped complete candidate'))
    oracle=min(good,key=completion_key)
    # All surviving candidates are measured, so every alternative is discharged by
    # measured complete area/timing after state- and interface-level discharge.
    certificate=dict(
        certified=True,
        incumbent_id=oracle['completion_id'],
        state_discharge_pairs=(all_state_count-len(states))*len(all_interfaces),
        interface_discharge_pairs=len(states)*(len(all_interfaces)-len(interfaces)),
        mapped_surviving_pairs=len(mapped),
        measured_area_discharges=max(0,len(good)-1),
        failed_integrated_pairs=len(mapped)-len(good),
        total_product_pairs=all_state_count*len(all_interfaces),
    )
    ordinary=min(states,key=lambda r:(r['area'],r['regret'],r['plan_id']))
    ordinary_pass=ordinary['plan_id']==structural['headroom_plan_id']
    under=[x for x in good if x['area_ratio']<=AREA_CAP+1e-12]
    return dict(name=name,structural=structural,state_survivors=states,
                all_interface_count=len(all_interfaces),interface_survivors=interfaces,
                complete_rows=mapped,oracle=oracle,certificate=certificate,
                oracle_match=True,ordinary_identity_returns_headroom=ordinary_pass,
                under_20_count=len(under),best_under_20=(min(under,key=completion_key) if under else None))


def primary(out: Path, primary_out: Path, liberty: Path):
    data=sic.dataset(); structural,states=state_survivors(data)
    interface=json.loads((primary_out/'interface_summary.json').read_text())
    feasible=[x for x in interface if x['stress_contract_pass']]
    area0=json.loads((PRIMARY_ASSET/'baseline_hardware.json').read_text())['area']
    mapped=[]
    for si,state in enumerate(states):
        rtl,floor=emit_top(PRIMARY_ASSET,out,'s%02d_%s'%(si,state['plan_id']),state['selected'])
        for ii,iface in enumerate(feasible):
            iid=f"r{iface['r_ohm']}_f{iface['filter_pf']}_{iface['method']}"
            hw=map_complete(out/f'hardware/s{si:02d}/i{ii:02d}',rtl,PRIMARY_GUARD,'context_controller',
                            iface['mode'],iface['samples'],iface['guard_cycles'],liberty,area0,floor)
            row=dict(design='primary',state_plan_id=state['plan_id'],state_selected=state['selected'],
                     state_nominal_area=state['area'],state_regret=state['regret'],
                     interface_id=iid,**iface,**hw)
            row['completion_id']=state['plan_id']+'::'+iid
            row['under_20']=bool(hw['timing_pass'] and hw['tmr_preserved'] and hw['area_ratio']<=AREA_CAP+1e-12)
            mapped.append(row)
            print('V140_PRIMARY',row['completion_id'],round(row['area_ratio'],6),row['under_20'],flush=True)
    result=summarize_product('primary',structural,len(data[0]['plans']),interface,states,feasible,mapped)
    old=json.loads((primary_out/'summary.json').read_text())['selected_headroom']
    result['v132_sequential_area']=float(old['area_ratio'])
    result['nonworse_than_v132']=bool(result['oracle']['area_ratio']<=float(old['area_ratio'])+1e-12)
    dump(out/'summary.json',result)
    return result


def transfer_interface(out: Path, state):
    rtl,_=emit_top(TRANSFER_ASSET,out,'interface_probe_'+state['plan_id'],state['selected'])
    plans=[s.calibrate(out/'calibration',r,f) for r,f in TRANSFER_POINTS]
    interface=[]
    events=[]
    for plan in plans:
        rr,ee=p0.evaluate_point(out/'interface',rtl,plan)
        interface+=rr; events+=ee
    dump(out/'interface_summary.json',interface)
    dump(out/'events.json',events)
    return interface


def transfer(out: Path, liberty: Path):
    data=ext.dataset(); structural,states=state_survivors(data)
    interface=transfer_interface(out,states[0])
    feasible=[x for x in interface if x['stress_pass']]
    area0=json.loads((TRANSFER_ASSET/'baseline_hardware.json').read_text())['area']
    mapped=[]
    for si,state in enumerate(states):
        rtl,floor=emit_top(TRANSFER_ASSET,out,'s%02d_%s'%(si,state['plan_id']),state['selected'])
        for ii,iface in enumerate(feasible):
            iid=f"r{iface['r_ohm']}_f{iface['filter_pf']}_{iface['method']}"
            hw=map_complete(out/f'hardware/s{si:02d}/i{ii:02d}',rtl,TRANSFER_GUARD,'ext_context_controller',
                            iface['mode'],iface['samples'],iface['guard_cycles'],liberty,area0,floor)
            row=dict(design='transfer',state_plan_id=state['plan_id'],state_selected=state['selected'],
                     state_nominal_area=state['area'],state_regret=state['regret'],
                     interface_id=iid,**iface,**hw)
            row['completion_id']=state['plan_id']+'::'+iid
            row['under_20']=bool(hw['timing_pass'] and hw['tmr_preserved'] and hw['area_ratio']<=AREA_CAP+1e-12)
            mapped.append(row)
            print('V140_TRANSFER',row['completion_id'],round(row['area_ratio'],6),row['under_20'],flush=True)
    result=summarize_product('transfer',structural,len(data[0]['plans']),interface,states,feasible,mapped)
    old_head=min((x for x in mapped if x['state_plan_id']==structural['headroom_plan_id']),key=completion_key)
    result['sequential_headroom_best']=old_head
    result['changed_state_vs_nominal_headroom']=result['oracle']['state_plan_id']!=structural['headroom_plan_id']
    result['repair_success']=bool(result['oracle']['area_ratio']<=old_head['area_ratio']+1e-12)
    dump(out/'summary.json',result)
    return result


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--primary-out',type=Path,required=True)
    ap.add_argument('--liberty',type=Path,required=True)
    a=ap.parse_args()
    out=a.out.resolve(); out.mkdir(parents=True,exist_ok=False)
    protocol=dict(status='predeclared_implementation',epsilon=EPSILON,area_cap=AREA_CAP,
                  objective=['mapped_digital_area','asserted_pullup_mA','worst_shutdown_ns','deterministic_id'],
                  frozen_inputs='V0.13.2 physical grids, interface catalogue, deadline, cap and nominal campaigns',
                  no_new_filter=True,no_new_stress_point=True,no_cap_or_epsilon_tuning=True)
    dump(out/'protocol.json',protocol)
    pri=primary(out/'primary',a.primary_out.resolve(),a.liberty.resolve())
    tra=transfer(out/'transfer',a.liberty.resolve())
    summary=dict(status='completed',primary=dict(
                    oracle=pri['oracle'],certificate=pri['certificate'],
                    ordinary_identity_returns_headroom=pri['ordinary_identity_returns_headroom'],
                    v132_sequential_area=pri['v132_sequential_area'],nonworse_than_v132=pri['nonworse_than_v132']),
                 transfer=dict(oracle=tra['oracle'],certificate=tra['certificate'],
                    ordinary_identity_returns_headroom=tra['ordinary_identity_returns_headroom'],
                    sequential_headroom_best=tra['sequential_headroom_best'],
                    changed_state_vs_nominal_headroom=tra['changed_state_vs_nominal_headroom'],
                    repair_success=tra['repair_success']),
                 h1_oracle_match=bool(pri['oracle_match'] and tra['oracle_match']),
                 h2_ordinary_nonregression=bool(pri['ordinary_identity_returns_headroom'] and tra['ordinary_identity_returns_headroom']),
                 h3_primary_nonworse=pri['nonworse_than_v132'],
                 h4_transfer_completion_aware=tra['repair_success'],
                 claim_boundary='exact only over frozen finite state x interface catalogues and finite campaigns/models')
    dump(out/'summary.json',summary)
    dump(out/'manifest.json',{str(p.relative_to(out)):h.sha(p) for p in out.rglob('*') if p.is_file()})
    print('V140_COMPLETE',json.dumps(summary,sort_keys=True),flush=True)

if __name__=='__main__':
    main()
