from pathlib import Path
import collections, copy, hashlib, json, re, subprocess, time

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT
LIB = ROOT/'lib/NangateOpenCellLibrary_typical.lib'
from state_inventory import inventory


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, obj):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True)+'\n')
    tmp.replace(path)


def append(path, obj):
    with Path(path).open('a') as f: f.write(json.dumps(obj, sort_keys=True)+'\n')


def command(folder, name, argv, timeout=180):
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    start = time.time(); status = 'finished'
    try:
        p = subprocess.run(argv, cwd=str(PROJECT), stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout)
        code, output = p.returncode, p.stdout.decode(errors='replace')
    except subprocess.TimeoutExpired as exc:
        code, output, status = -999, (exc.stdout or b'').decode(errors='replace'), 'timeout'
    (folder/(name+'.log')).write_text(output)
    append(folder/'commands.jsonl', dict(name=name, argv=list(map(str,argv)),
        start=start, end=time.time(), returncode=code, status=status, log=name+'.log'))
    if code: raise RuntimeError('%s: %s (code %s): %s' % (folder.name,name,code,output[-1600:]))
    return output


def yosys(folder, name, script, timeout=180):
    path = Path(folder)/(name+'.ys'); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(script+'\n')
    return command(folder,name,['yosys','-Q','-T','-s',str(path)],timeout)


def declaration(direction, name, bits, kind='wire'):
    width = '' if len(bits)==1 else '[%d:0] ' % (len(bits)-1)
    return '%s %s %s%s;' % (direction,kind,width,name)


def prepare(cfg, folder):
    """Use Yosys for ALL combinational semantics, not a handwritten cell translator."""
    folder = Path(folder); folder.mkdir(parents=True,exist_ok=False)
    yosys(folder,'freeze','read_verilog -D SIMULATION -defer %s; hierarchy -check -top top; proc; flatten; opt; '
          'check -assert; write_json %s; write_verilog -noattr %s' %
          (' '.join(cfg['sources']), folder/'frozen.json', folder/'frozen.v'))
    raw = json.loads((folder/'frozen.json').read_text()); mod = raw['modules']['top']
    rows = inventory(mod)
    assert len({r['q_net'] for r in rows})==len(rows), 'aliased state not supported'
    for name,cell in mod['cells'].items():
        typ = cell['type'].lower()
        if any(x in typ for x in ['mem','latch','ff']) and 'dff' not in typ:
            raise ValueError('Unsupported state cell: '+cell['type'])
    comb = copy.deepcopy(mod)
    comb['attributes'] = {}
    comb['cells'] = {n:c for n,c in comb['cells'].items()
                     if 'dff' not in c['type'] and c['type']!='$scopeinfo'}
    for net in comb['netnames'].values(): net['attributes'] = {}
    for r in rows:
        i=r['bit_id']; c=mod['cells'][r['source_cell']]; conn=c['connections']
        comb['ports']['st_%d'%i] = dict(direction='input',bits=[r['q_net']])
        comb['ports']['nx_%d'%i] = dict(direction='output',bits=[conn['D'][r['source_offset']]])
        for key in ['EN','SRST']:
            if key in conn:
                assert len(conn[key])==1
                comb['ports']['%s_%d'%(key.lower(),i)] = dict(direction='output',bits=conn[key])
    dump(folder/'comb.json',dict(modules={'comb_core':comb},creator='DF-Harden  FF separation'))
    yosys(folder,'comb','read_json %s; check -assert; write_verilog -noattr %s' %
          (folder/'comb.json',folder/'comb.v'))
    dump(folder/'logical_bits.json',rows)
    dump(folder/'design.json',dict(cfg=cfg,logical_bits=len(rows),rtl_hashes={p:sha(p) for p in cfg['sources']},
         frozen_sha256=sha(folder/'frozen.json'),cell_types=dict(collections.Counter(c['type'] for c in mod['cells'].values()))))
    return mod, rows


def emit(mod, rows, comb_file, selected, path):
    selected=set(selected); assert selected<=set(range(len(rows)))
    ports=mod['ports']; lines=[Path(comb_file).read_text(), 'module dut(%s);'%','.join(ports)]
    for n,p in ports.items():
        assert p['direction'] in ['input','output']
        lines.append(declaration(p['direction'],n,p['bits']))
    connections=['.%s(%s)'%(n,n) for n in ports]; physical=[]
    for r in rows:
        i=r['bit_id']; cell=mod['cells'][r['source_cell']]; conn=cell['connections']; par=cell['parameters']
        assert conn['CLK']==ports['clk']['bits'] and int(par['CLK_POLARITY'],2)==1
        lines += ['wire d_%d;'%i,'(* keep=1 *) wire v_%d;'%i]
        connections += ['.st_%d(v_%d)'%(i,i),'.nx_%d(d_%d)'%(i,i)]
        for key in ['EN','SRST']:
            if key in conn:
                lines.append('wire %s_%d;'%(key.lower(),i))
                connections.append('.%s_%d(%s_%d)'%(key.lower(),i,key.lower(),i))
        names=[]
        for rep in (['A','B','C'] if i in selected else ['A']):
            name='q_%05d_%s'%(i,rep); names.append(name)
            lines.append('(* keep=1, dont_touch=1 *) reg %s;'%name)
            init=None
            for net in mod['netnames'].values():
                if r['q_net'] in net['bits'] and 'init' in net.get('attributes',{}):
                    pos=net['bits'].index(r['q_net']); val=net['attributes']['init'][-1-pos]
                    if val in '01':
                        if init is not None: assert init==val
                        init=val
            if init is not None: lines.append("initial %s=1'b%s;"%(name,init))
            assign='%s <= d_%d;'%(name,i); reset=''; enable=''
            if 'SRST' in conn:
                sig='srst_%d'%i
                if not int(par['SRST_POLARITY'],2): sig='!'+sig
                value=(int(par['SRST_VALUE'],2)>>r['source_offset'])&1
                reset="if (%s) %s<=1'b%d; else "%(sig,name,value)
            if 'EN' in conn:
                sig='en_%d'%i
                if not int(par['EN_POLARITY'],2): sig='!'+sig
                enable='if (%s) '%sig
            body=(enable+'begin '+reset+assign+' end') if cell['type']=='$sdffce' else reset+enable+assign
            lines.append('always @(posedge clk) begin %s end'%body)
            physical.append(dict(r,physical_id=len(physical),replica=rep,path='dut.'+name))
        vote=names[0] if len(names)==1 else '((%s&%s)|(%s&%s)|(%s&%s))'%(names[0],names[1],names[0],names[2],names[1],names[2])
        lines.append('assign v_%d=%s;'%(i,vote))
    lines += ['comb_core logic_inst(%s);'%','.join(connections),'endmodule']
    Path(path).write_text('\n'.join(lines)+'\n')
    return physical


def library_models(folder):
    out=Path(folder)/'cells.v'
    if not out.exists():
        yosys(folder,'library_models','read_liberty -ignore_miss_func %s; write_verilog %s'%(LIB,out))
    return out


def map_hardware(folder, name, source, physical, ports):
    folder=Path(folder); raw=folder/(name+'_raw.json'); mapped=folder/(name+'_mapped')
    script=('read_verilog %s; hierarchy -check -top dut; flatten; proc; '
            'setattr -set keep 1 t:$dff t:$dffe t:$sdff t:$sdffe t:$sdffce; '
            'opt -nodffe -nosdff; techmap; dfflibmap -liberty %s; abc -liberty %s; '
            'clean; read_liberty -lib %s; check -assert; stat -liberty %s; write_json %s')
    yosys(folder,name+'_map',script%(source,LIB,LIB,LIB,LIB,raw))
    design=json.loads(raw.read_text()); mod=design['modules']['dut']
    mod['cells']={'cell_%05d'%i:c for i,(old,c) in enumerate(sorted(mod['cells'].items())) if c['type'] != '$scopeinfo'}
    ff={n:c for n,c in mod['cells'].items() if c['type'].startswith('DFF')}
    assert len(ff)==len(physical),('FF count mismatch',len(ff),len(physical))
    mapping=[]; q_to_state={}
    for r in physical:
        state=r['path'].split('.')[-1]; qb=mod['netnames'][state]['bits']
        matches=[(n,c) for n,c in ff.items() if c['connections'].get('Q')==qb]
        assert len(matches)==1,(state,matches)
        n,c=matches[0]; assert c['type']=='DFF_X1',c['type']
        mapping.append(dict(r,path='dut.%s.IQ'%n,mapped_cell=n,mapped_cell_type=c['type']))
        q_to_state[qb[0]]=state
    drivers={}
    for n,c in mod['cells'].items():
        if n in ff: continue
        ins={b for pn,bs in c['connections'].items() if c['port_directions'][pn]=='input' for b in bs if isinstance(b,int)}
        for pn,bs in c['connections'].items():
            if c['port_directions'][pn]=='output':
                for b in bs: drivers[b]=ins
    voter_audit={}
    protected=sorted({r['bit_id'] for r in physical if r['replica']=='B'})
    for i in protected:
        stack=list(mod['netnames']['v_%d'%i]['bits']); seen=set(); found=set()
        while stack:
            b=stack.pop()
            if b in seen: continue
            seen.add(b)
            if b in q_to_state: found.add(q_to_state[b]); continue
            stack.extend(drivers.get(b,[]))
        expected={'q_%05d_%s'%(i,rep) for rep in 'ABC'}
        assert found==expected,('voter state cone mismatch',i,found,expected)
        voter_audit[str(i)]=sorted(found)
    for net in mod['netnames'].values():net.pop('signed',None)
    dump(mapped.with_suffix('.json'),design)
    yosys(folder,name+'_write','read_json %s; write_verilog -noattr -noexpr %s'%(mapped.with_suffix('.json'),mapped.with_suffix('.v')))
    areas={n:float(a) for n,a in re.findall(r'cell\s*\(\s*(\w+)\s*\)\s*\{.*?area\s*:\s*([\d.eE+-]+)',LIB.read_text(),re.S)}
    counts=collections.Counter(c['type'] for c in mod['cells'].values())
    area=sum(areas[k]*v for k,v in counts.items())
    inputs=' '.join(n+('*' if len(v['bits'])>1 else '') for n,v in ports.items() if v['direction']=='input' and n!='clk')
    tcl=folder/(name+'_sta.tcl')
    tcl.write_text('''read_liberty %s
read_verilog %s
link_design dut
create_clock -name clk -period 10 [get_ports clk]
set_input_delay -clock clk 1 [get_ports {%s}]
set_input_transition 0.1 [get_ports {%s}]
set_output_delay -clock clk 1 [all_outputs]
set_load 0.01 [all_outputs]
check_setup -verbose
report_checks -path_delay max
report_checks -path_delay min
report_worst_slack
report_tns
exit
'''%(LIB,mapped.with_suffix('.v'),inputs,inputs))
    log=command(folder,name+'_sta',['sta','-exit',str(tcl)])
    assert not re.search(r'(?im)^error',log),log[-1500:]
    slacks=[float(x) for x in re.findall(r'(-?[\d.]+)\s+slack \((?:MET|VIOLATED)\)',log)]
    assert len(slacks)>=2,('setup/hold paths not both reported',log[-2000:])
    w=re.search(r'worst slack\s+(-?[\d.]+)',log); assert w
    hw=dict(area=area,flip_flops=len(ff),cell_counts=dict(counts),worst_setup_slack_ns=float(w.group(1)),
        setup_hold_reported_slacks_ns=slacks,timing_feasible=min(slacks)>=0 and 'VIOLATED' not in log,
        timing_scope='Nangate45 typical, pre-layout ideal wires; clock 10ns, IO 1ns, load 0.01pF',
        warnings=re.findall(r'(?im)^warning.*',log),voter_cones=voter_audit,
        mapped_netlist_sha256=sha(mapped.with_suffix('.v')),liberty_sha256=sha(LIB))
    dump(folder/(name+'_hardware.json'),hw);dump(folder/(name+'_mapped_bits.json'),mapping)
    return mapped.with_suffix('.v'),mapping,hw


def formal(cfg, folder, name, implementation, ports, extra=None):
    """Same 24-step, reset-constrained bounded scope as the original smoke."""
    folder=Path(folder); inputs={n:v for n,v in ports.items() if v['direction']=='input'}
    outputs={n:v for n,v in ports.items() if v['direction']=='output'}
    lines=['module miter(%s);'%','.join(inputs)]
    for n,v in inputs.items(): lines.append(declaration('input',n,v['bits']))
    for n,v in outputs.items():
        lines.append(declaration('wire','g_'+n,v['bits'],kind=''))
        lines.append(declaration('wire','d_'+n,v['bits'],kind=''))
    for module,prefix,instance in [('top','g_','golden'),('dut','d_','candidate')]:
        conns=['.%s(%s)'%(n,n if n in inputs else prefix+n) for n in ports]
        lines.append('%s %s(%s);'%(module,instance,','.join(conns)))
    lines.append('always @* if (!rst) begin')
    for n in outputs:
        gate=cfg.get('output_gates',{}).get(n)
        lines.append(('if(g_%s) '%gate if gate else '')+'assert(g_%s==d_%s);'%(n,n))
    lines += ['end','endmodule']; miter=folder/(name+'_miter.v');miter.write_text('\n'.join(lines)+'\n')
    reference=folder/'frozen.v'
    assert reference.exists()
    sources=[str(reference)]+[str(x) for x in (extra or [])]+[str(implementation),str(miter)]
    log=yosys(folder,name+'_formal','read_verilog -formal -D SIMULATION -defer %s; prep -top miter -flatten; opt; async2sync; dffunmap; '
        'sat -seq 24 -set rst 0 -set-at 1 rst 1 -set-def-inputs -set-init-undef '
        '-prove-asserts -verify -timeout 60'%(' '.join(sources)),timeout=150)
    assert 'SUCCESS' in log,log[-1500:]
    return dict(status='passed',scope='24-step bounded equivalence to Yosys-frozen original RTL; synchronous reset at step 1, defined symbolic inputs',reference_sha256=sha(reference),log_sha256=sha(folder/(name+'_formal.log')))
