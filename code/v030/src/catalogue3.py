import collections,json,math,hashlib,re,time
from pathlib import Path
import numpy as np
import hardware as h
P=h.PROJECT/'v030';OLD=h.PROJECT/'runs/pilot-area-repair-20260910-01'
ASSETS={d:OLD/'assets'/d for d in ['axis_simple8','axis_skid8','uart_tx8','uart_rx8','eth_fcs8']}
ASSETS.update(sha256=P/'results/sha-assets-01/sha256',divu16=P/'results/div-assets-01/divu16')
PERIODS={d:(50 if d=='sha256' else 10) for d in ASSETS}

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
    from evaluation import plan_id
    for selected,info in sorted(plans.items(),key=lambda x:(len(x[0]),x[0])):
        rows.append(dict(plan_id=plan_id(selected),selected=list(selected),**info))
    return dict(design_id=design,asset=str(asset),N=n,area0=area,period_ns=PERIODS[design],plans=rows,
        initial_tmr_bit_count_estimate=k0,nominal_cost_per_bit=nominal,
        original_design_sha256=h.sha(asset/'baseline_mapped.v'),bit_map_sha256=h.sha(asset/'logical_bits.json'),
        construction='fixed state order, reversed order, source-cell-width order and seeded random prefixes; no fault labels or previous policy sets',
        candidate_scope='finite catalogue only; not all 2^N subsets',role='confirmatory_lineage' if design in ['sha256','divu16'] else 'development')

if __name__=='__main__':
    out=P/'configs/core-study-01';out.mkdir(exist_ok=False)
    for d,a in ASSETS.items():
        row=generate(d,a);h.dump(out/(d+'.json'),row);print('CATALOGUE_FROZEN',d,row['N'],len(row['plans']),row['period_ns'],flush=True)
    protocol=dict(version='core-study-01',created=time.time(),status='frozen_before_new_fault_labels',
        designs=list(ASSETS),new_lineages=['secworks','project-f'],development_designs=list(ASSETS)[:5],
        area_ratio=.2,finite_risk_pool_per_bit=32,pool_scope='fixed hidden per-bit event population; deterministic remaining-mass bounds; certificate is relative to this pool, not physical or arbitrary-workload risk',
        epsilon_normalized=.02,query_seeds=[701,702,703,704,705],fault_batch=8,
        methods=['Eager-Uniform','Eager-CLUCB','Lazy-CLUCB','Alternating-CLUCB','Dual-Certificate'],
        method_contracts={
          'Eager-Uniform':'purchase every candidate feasibility, then balanced observations on relevant difference bits',
          'Eager-CLUCB':'purchase every candidate feasibility, then worst-gap symmetric-difference least-sampled observations',
          'Lazy-CLUCB':'synthesis after a fault batch whose estimated cost matches one synthesis; optimistic-ranking candidate each synthesis; same gap sampling',
          'Alternating-CLUCB':'alternate one synthesis and eight fault labels, same optimistic ranking and gap sampling',
          'Dual-Certificate':'select actual synthesis or eight fault observations by predicted reduction of squared positive certificate gaps per measured service cost; feasibility probability only guides order, never authorizes deployment or elimination'},
        all_methods_shared=['same finite candidate family','same fault pool and ordering per trial','same proof-backed observation reuse','same exact hardware verification','same caches but logical costs charged'],
        timing_rule='new designs use max(10ns,ceil(1.25*(10ns-baseline_setup_slack)/5ns)*5ns), frozen before faults; old designs retain10ns',
        primary='total charged fault+synthesis+certificate service seconds to finite-pool epsilon certificate',
        secondary=['fault calls','synthesis calls','fresh mapped risk','candidate feasibility','proof failures'],
        independent_final_evaluation=1024,final_selection='last certified incumbent for each method/trial, unique hardware shared; plus baseline',
        prohibit=['test-label tuning','infeasible deletion from reporting','treat derived outcomes as new independent samples','claiming finite-catalogue certificate is global optimum'],
        cost_scope='replayed service ledger with independently measured physical executions, not end-to-end wall-clock speedup',
        active_workers_max=4)
    h.dump(out/'protocol.json',protocol)
    h.dump(out/'source_manifest.json',{p.name:h.sha(p) for p in (P/'src').glob('*.py')})
