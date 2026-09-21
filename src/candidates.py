import collections, hashlib, json, re
import numpy as np
import hardware as h

ASSETS = {}
PERIODS = {}

def plan_id(selected):
    return 'baseline' if not selected else 'tmr_'+hashlib.sha256(json.dumps(sorted(selected)).encode()).hexdigest()[:12]

def generate(design,asset):
    bits=json.loads((asset/'logical_bits.json').read_text());n=len(bits)
    hw=json.loads((asset/'baseline_hardware.json').read_text());area=hw['area']
    areas={k:float(v) for k,v in re.findall(r'cell\s*\(\s*(\w+)\s*\)\s*\{.*?area\s*:\s*([\d.eE+-]+)',h.LIB.read_text(),re.S)}
    nominal=2*areas['DFF_X1']+2*areas['AND2_X1']+areas['OR2_X1']
    k0=max(1,min(n,int(.2*area/nominal)))
    groups=collections.Counter(b['source_cell'] for b in bits)
    rng=np.random.default_rng(18062031)
    orders=[list(range(n)),list(reversed(range(n))),sorted(range(n),key=lambda i:(groups[bits[i]['source_cell']],i)),
            sorted(range(n),key=lambda i:('EN' not in str(bits[i]['ff_type']),-groups[bits[i]['source_cell']],i)),
            rng.permutation(n).tolist(),rng.permutation(n).tolist()]
    plans={():{'origin':'baseline'}}
    for orderid,order in enumerate(orders):
        for fraction in [.50,.75,1.0,1.25,1.50]:
            k=max(1,min(n,int(round(k0*fraction))))
            selected=tuple(sorted(order[:k]));plans.setdefault(selected,dict(origin='label_free_prefix',order=orderid,fraction=fraction))
    for j in range(8):
        k=max(1,min(n,int(round(k0*[.75,1.,1.25,1.5][j%4]))))
        selected=tuple(sorted(rng.choice(n,k,replace=False).tolist()))
        plans.setdefault(selected,dict(origin='label_free_random',index=j))
    rows=[]
    for selected,info in sorted(plans.items(),key=lambda x:(len(x[0]),x[0])):
        rows.append(dict(plan_id=plan_id(selected),selected=list(selected),**info))
    return dict(design_id=design,asset=str(asset),N=n,area0=area,period_ns=PERIODS[design],plans=rows,
        initial_tmr_bit_count_estimate=k0,nominal_cost_per_bit=nominal,
        original_design_sha256=h.sha(asset/'baseline_mapped.v'),bit_map_sha256=h.sha(asset/'logical_bits.json'),
        construction='fixed state order, reversed order, source-cell-width order and seeded random prefixes; no fault labels or previous policy sets',
        candidate_scope='finite catalogue only; not all 2^N subsets',role='confirmatory_lineage' if design in ['sha256','divu16'] else 'development')
