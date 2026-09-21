import copy, json, re
from pathlib import Path
import hardware as h


def prove(cfg, folder, name, implementation, ports, extra=None):
    folder=Path(folder);rows=json.loads((folder/'logical_bits.json').read_text())
    ref=copy.deepcopy(json.loads((folder/'frozen.json').read_text())['modules']['top'])
    parsed=folder/(name+'_proof_impl.json')
    h.yosys(folder,name+'_proof_parse','read_verilog %s; hierarchy -check -top dut; proc; flatten; write_json %s'%
        (' '.join(str(x) for x in (extra or [])+[implementation]),parsed))
    imp=json.loads(parsed.read_text())['modules']['dut']
    state_names=sorted(n for n in imp['netnames'] if re.fullmatch(r'q_\d{5}_[ABC]',n))
    assert {int(n.split('_')[1]) for n in state_names}==set(range(len(rows)))
    for r in rows:ref['ports']['debug_%d'%r['bit_id']]=dict(direction='output',bits=[r['q_net']])
    for j,n in enumerate(state_names):imp['ports']['debug_%d'%j]=dict(direction='output',bits=imp['netnames'][n]['bits'])
    rj=folder/(name+'_proof_ref.json');ij=folder/(name+'_proof_gate.json')
    h.dump(rj,dict(modules={'ref_model':ref}));h.dump(ij,dict(modules={'impl_model':imp}))
    inputs={n:p for n,p in ports.items() if p['direction']=='input'}
    outputs={n:p for n,p in ports.items() if p['direction']=='output'}
    lines=['module proof_miter(%s);'%','.join(inputs)]
    for n,p in inputs.items():lines.append(h.declaration('input',n,p['bits']))
    for n,p in outputs.items():
        for prefix in ['g_','d_']:lines.append(h.declaration('wire',prefix+n,p['bits'],kind=''))
    lines+=['wire gs_%d;'%i for i in range(len(rows))]+['wire ds_%d;'%j for j in range(len(state_names))]
    for module,prefix,instance,count,sp in [('ref_model','g_','golden',len(rows),'gs_'),('impl_model','d_','candidate',len(state_names),'ds_')]:
        conns=['.%s(%s)'%(n,n if n in inputs else prefix+n) for n in ports]
        conns+=['.debug_%d(%s%d)'%(i,sp,i) for i in range(count)]
        lines.append('%s %s(%s);'%(module,instance,','.join(conns)))
    lines.append('always @* if(!rst) begin')
    for n in outputs:
        gate=cfg.get('output_gates',{}).get(n)
        lines.append(('if(g_%s) '%gate if gate else '')+'assert(g_%s==d_%s);'%(n,n))
    conditional=[]
    for j,n in enumerate(state_names):
        i=int(n.split('_')[1]);gate=any('fcs_reg[' in a for a in rows[i]['aliases'])
        if gate:conditional.append(i)
        lines.append(('if(g_fcs_valid) ' if gate else '')+'assert(gs_%d==ds_%d);'%(i,j))
    assert len(set(conditional))==32,'CRC output-storage mask must cover exactly 32 bits'
    lines+=['end','endmodule'];miter=folder/(name+'_state_miter.v');miter.write_text('\n'.join(lines)+'\n')
    script=('read_json %s; read_json %s; read_verilog -formal %s; prep -top proof_miter -flatten; '
        'opt; async2sync; dffunmap; sat -seq 2 -tempinduct -maxsteps 4 -set rst 0 -set-at 1 rst 1 '
        '-set-def-inputs -set-init-undef -prove-asserts -verify -timeout 60')%(rj,ij,miter)
    out=h.yosys(folder,name+'_state_formal',script,150)
    assert 'SUCCESS' in out,out[-1500:]
    return dict(status='passed',scope='temporal induction with explicit state correspondence; reset at first step; FCS data meaningful only when valid',
        reference_sha256=h.sha(folder/'frozen.v'),log_sha256=h.sha(folder/(name+'_state_formal.log')),
        asserted_state_relations=len(state_names),conditional_output_state_bits=32)
