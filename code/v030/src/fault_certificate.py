import copy, json, math, time
from pathlib import Path
import hardware as h


def cut_mapped(netlist_json, bits, name, folder):
    raw=json.loads(Path(netlist_json).read_text())
    mod=copy.deepcopy(raw['modules']['dut'])
    mod['attributes']={}
    for net in mod['netnames'].values(): net['attributes']={}
    q=[];nxt=[];extra={};removed=set()
    for r in bits:
        cn=r['mapped_cell'];c=mod['cells'][cn]
        assert c['type'] in ['DFF_X1','$dff']
        clock='CK' if c['type']=='DFF_X1' else 'CLK'
        assert c['connections'][clock]==mod['ports']['clk']['bits']
        if c['type']=='$dff': assert int(c['parameters']['CLK_POLARITY'],2)==1
        qb=c['connections']['Q'];db=c['connections']['D']
        assert len(qb)==len(db)==1
        q+=qb;nxt+=db;removed.add(cn)
        qn=c['connections'].get('QN',[])
        if qn:
            assert len(qn)==1
            extra['invert_'+cn]=dict(type='$not',hide_name=0,attributes={},
                parameters={'A_SIGNED':'0','A_WIDTH':'1','Y_WIDTH':'1'},
                port_directions={'A':'input','Y':'output'},connections={'A':qb,'Y':qn})
    assert len(removed)==len(bits)
    mod['cells']={n:c for n,c in mod['cells'].items() if n not in removed and c['type']!='$scopeinfo'}
    mod['cells'].update(extra)
    assert not any('ff' in c['type'].lower() for c in mod['cells'].values())
    mod['ports']['state']=dict(direction='input',bits=q)
    mod['ports']['next_state']=dict(direction='output',bits=nxt)
    path=Path(folder)/(name+'.json');h.dump(path,dict(modules={name:mod}))
    return path


def prove(asset, candidate, destination, negative=False, reference=None, reference_map=None, precondition=False):
    asset=Path(asset);candidate=Path(candidate);folder=Path(destination)
    folder.mkdir(parents=True,exist_ok=False);start=time.time()
    base_map_path=Path(reference_map) if reference_map else asset/'baseline_mapped_bits.json'
    base_bits=json.loads(base_map_path.read_text())
    if candidate==asset:
        can_bits=json.loads((asset/'baseline_mapped_bits.json').read_text());canjson=asset/'baseline_mapped.json';canv=asset/'baseline_mapped.v'
    else:
        can_bits=json.loads((candidate/'candidate_mapped_bits.json').read_text())
        canjson=candidate/'candidate_mapped.json';canv=candidate/'candidate_mapped.v'
    basejson=Path(reference) if reference else asset/'baseline_mapped.json';bn=len(base_bits);cn=len(can_bits)
    assert [r['bit_id'] for r in base_bits]==list(range(bn))
    selected={r['bit_id'] for r in can_bits if r['replica']=='B'}
    assert cn==bn+2*len(selected)
    basecut=cut_mapped(basejson,base_bits,'base_cut',folder)
    cancut=cut_mapped(canjson,can_bits,'can_cut',folder)
    mod=json.loads(basejson.read_text())['modules']['dut'];ports=mod['ports']
    inputs={n:p for n,p in ports.items() if p['direction']=='input'}
    outputs={n:p for n,p in ports.items() if p['direction']=='output'}
    width=max(1,(cn-1).bit_length())
    lines=['module fault_miter(%s, logical_state, fault_id, error_present);'%','.join(inputs)]
    for n,p in inputs.items(): lines.append(h.declaration('input',n,p['bits']))
    lines+=['input wire [%d:0] logical_state;'%(bn-1),
            'input wire [%d:0] fault_id;'%(width-1),'input wire error_present;',
            'wire [%d:0] candidate_state, candidate_next;'%(cn-1),
            'wire [%d:0] baseline_next;'%(bn-1)]
    for n,p in outputs.items():
        for prefix in ['b_','c_']:lines.append(h.declaration('wire',prefix+n,p['bits'],kind=''))
    for j,r in enumerate(can_bits):
        active="(error_present && fault_id==%d)"%j if r['bit_id'] in selected else "1'b0"
        if negative and j==0: active="1'b1" # known-invalid correspondence control
        lines.append('assign candidate_state[%d]=logical_state[%d] ^ %s;'%(j,r['bit_id'],active))
    for module,prefix,state,nextstate in [('base_cut','b_','logical_state','baseline_next'),('can_cut','c_','candidate_state','candidate_next')]:
        conn=['.%s(%s)'%(n,n if n in inputs else prefix+n) for n in ports]
        conn+=['.state(%s)'%state,'.next_state(%s)'%nextstate]
        lines.append('%s impl_%s(%s);'%(module,prefix,','.join(conn)))
    lines.append('always @* begin')
    for n in outputs:lines.append('assert(b_%s==c_%s);'%(n,n))
    for j,r in enumerate(can_bits):
        guard='if (!error_present || fault_id!=%d) '%j if r['bit_id'] in selected else ''
        lines.append(guard+'assert(baseline_next[%d]==candidate_next[%d]);'%(r['bit_id'],j))
    lines+=['end','endmodule'];miter=folder/'fault_miter.v';miter.write_text('\n'.join(lines)+'\n')
    script=('read_liberty -ignore_miss_func %s; read_json %s; read_json %s; '
        'read_verilog -formal %s; prep -top fault_miter -flatten; opt; chformal -lower; '
        'sat -set-def-inputs -prove-asserts -verify -timeout 90')%(h.LIB,basecut,cancut,miter)
    if precondition:
        script=script.replace('sat -set-def-inputs','techmap; opt; abc -g simple; opt_clean; sat -set-def-inputs')
    status='passed';error=None
    try:out=h.yosys(folder,'fault_correspondence',script,180);assert 'SUCCESS' in out
    except Exception as exc:
        error=str(exc)
        log=(folder/'fault_correspondence.log').read_text()
        status='counterexample' if 'proof did fail' in log else ('timeout' if 'TIMEOUT' in log else 'tool_error')
    result=dict(status=status,negative_control=negative,elapsed=time.time()-start,proof_engine='abc-preconditioned-sat' if precondition else 'direct-sat',
        baseline_bits=bn,candidate_bits=cn,selected=sorted(selected),
        baseline_netlist_sha256=h.sha(asset/'baseline_mapped.v'),candidate_netlist_sha256=h.sha(canv),
        baseline_map_sha256=h.sha(base_map_path),reference_json_sha256=h.sha(basejson),reference_kind='canonical' if reference else 'mapped',
        property='combinational output equality plus one-step closure of single-protected-replica fault relation for all defined states and inputs',
        induction='single error remains confined to same protected replica or is corrected; unprotected upset is shared by corresponding logical state',
        assumptions=['one storage upset','DFF_X1 zero-delay logical semantics','no voter/combination/clock/reset faults','fault-free initial correspondence established separately'],
        statistical_claim=False,error=error,log_sha256=h.sha(folder/'fault_correspondence.log'))
    h.dump(folder/'certificate.json',result);print('FAULT_CERTIFICATE',json.dumps(result),flush=True)
    return result


if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--design',default='uart_rx8');ap.add_argument('--tag',required=True)
    a=ap.parse_args();root=h.PROJECT/'runs/pilot-area-repair-20260910-01'
    cp=[json.loads(s) for s in (root/'queries'/a.design/'checkpoints.jsonl').read_text().splitlines()]
    cp=next(x for x in cp if x['method']=='DF-Harden' and x['seed']==11 and x['nominal_budget']==128)
    from evaluation import plan_id
    asset=root/'assets'/a.design;candidate=root/'hardware'/a.design/plan_id(cp['selected'])
    out=h.PROJECT/'v030/results'/a.tag
    good=prove(asset,candidate,out/'positive');bad=prove(asset,asset,out/'negative',True)
    assert good['status']=='passed' and bad['status']=='counterexample'
