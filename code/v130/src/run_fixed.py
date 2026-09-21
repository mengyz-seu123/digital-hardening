import json
import re
from pathlib import Path
import stress_contract_minloop as s

COMPACT_GUARD = Path(__file__).resolve().parents[1]/'rtl'/'compact_context_guard.v'


def emit_candidate(out: Path, label: str, selected):
    frozen=json.loads((s.ASSET/'frozen.json').read_text())
    bits=json.loads((s.ASSET/'logical_bits.json').read_text())
    folder=out/'generated'/label
    folder.mkdir(parents=True,exist_ok=False)
    raw=folder/'candidate.v'
    physical=s.h.emit(frozen['modules']['top'],bits,s.ASSET/'comb.v',selected,raw)
    text=raw.read_text()
    normalized,n=re.subn(r'\bmodule\s+dut\b','module top',text,count=1)
    if n != 1:
        raise RuntimeError(f'expected one emitted top module named dut, found {n}')
    top=folder/'candidate_top.v'
    top.write_text(normalized)
    expected_core_ff=len(bits)+2*len(selected)
    s.h.dump(folder/'selected.json',dict(label=label,selected=selected,
        physical_bits=len(physical),expected_core_ff=expected_core_ff,
        normalization='module dut -> module top only'))
    return top


def expected_core_ff(rtl: Path):
    p=str(rtl)
    if '/headroom/' in p:
        return 55+2*len(s.HEADROOM_BITS)
    if '/exact/' in p:
        return 55+2*len(s.EXACT_BITS)
    raise RuntimeError('cannot identify TMR start from '+p)


def map_integrated(folder: Path, rtl: Path, mode: int, samples: int, guard: int,
                   liberty: Path, area0: float):
    folder.mkdir(parents=True,exist_ok=False)
    mapped_json=folder/'mapped.json'
    script=f'''read_verilog {rtl} {s.GUARD_RTL}
chparam -set MODE {mode} -set SAMPLES {samples} -set GUARD {guard} context_controller
hierarchy -check -top context_controller
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
    log=s.c.command(folder,'yosys',['yosys','-Q','-T','-s','map.ys'])
    areas=re.findall(r'Chip area for (?:top )?module.*?:\s*([0-9.]+)',log)
    if not areas:
        areas=re.findall(r'Chip area.*?([0-9]+\.[0-9]+)',log)
    if not areas:
        raise ValueError('missing mapped area')
    area=float(areas[-1])
    design=json.loads(mapped_json.read_text())
    mod=design['modules']['context_controller']
    ff_count=sum(cel.get('type','').startswith('DFF') for cel in mod.get('cells',{}).values())
    core_floor=expected_core_ff(rtl)
    if ff_count < core_floor:
        raise RuntimeError(f'TMR preservation failure: mapped FF={ff_count} < core floor={core_floor}')

    (folder/'timing.tcl').write_text(f'''read_liberty {liberty}
read_verilog {folder/'mapped.v'}
link_design context_controller
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
    timing=s.c.command(folder,'sta',['sta','-exit','timing.tcl'])
    if re.search(r'(?im)^Error:',timing):
        raise RuntimeError('OpenSTA parser/analysis error: '+timing[-3000:])
    slacks=list(map(float,re.findall(r'(-?\d+\.\d+)\s+slack',timing)))
    if len(slacks)<2:
        raise ValueError('missing setup/hold slack')
    return dict(area=area,area_ratio=(area-area0)/area0,
                flip_flops=ff_count,expected_core_flip_flops=core_floor,
                policy_flip_flops=ff_count-core_floor,
                setup_slack=slacks[0],hold_slack=slacks[-1],
                timing_pass=min(slacks[0],slacks[-1])>=0,
                tmr_preserved=ff_count>=core_floor)


def integrated_normal(out: Path, rtl: Path, plan):
    exe=s.compile_exec(out/'integrated_exec',rtl,plan)
    ok=True
    traces=0
    col=s.METHODS.index(plan['method'])+1
    for phase in s.PHASES:
        for side,width in __import__('itertools').product((0,1),(8,40)):
            rr=s.replay(out/f'integrated_normal/p{phase}_s{side}_w{width}',exe,phase,
                        s.stress_words(phase,side=side,width=width))
            ok &= all(r[col]==r[1] and (r[col]&0xc000)!=0xc000 for r in rr)
            traces += 1
    return bool(ok),traces


s.GUARD_RTL=COMPACT_GUARD
s.emit_candidate=emit_candidate
s.map_integrated=map_integrated
s.integrated_normal=integrated_normal
s.main()
