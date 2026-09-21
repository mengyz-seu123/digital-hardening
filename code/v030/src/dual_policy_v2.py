import math,time,json
import numpy as np
from feasibility_gp import probabilities
METHODS=['Eager-Uniform','Eager-CLUCB','Lazy-CLUCB','Alternating-CLUCB','GP-Lazy-CLUCB','Dual-Certificate']


def geometry(masks,L,U,status,epsilon,mu):
    """All incumbent/challenger upper gaps, normalized by logical state count."""
    N=masks.shape[1];live=np.flatnonzero(status!=0);feasible=np.flatnonzero(status==1)
    assert len(feasible)>0
    gaps=(masks@U)[None,:]-(masks@L)[:,None]-(masks*(U-L))@masks.T
    gaps=np.maximum(gaps/N,0.)
    potential=(np.maximum(gaps[:,live]-epsilon,0.)**2).sum(axis=1)
    bestval=potential[feasible].min();ids=feasible[np.isclose(potential[feasible],bestval,atol=1e-14,rtol=1e-12)]
    S=int(ids[np.argmax((masks@mu)[ids])]);G=float(gaps[S,live].max())
    return S,G,gaps,potential,live


def run(method,masks,service,seed,M=32,epsilon=.02,batch=8,max_actions=100000):
    assert method in METHODS
    masks=np.asarray(masks,dtype=float);K,N=masks.shape
    n=np.zeros(N,int);f=np.zeros(N,int);status=np.full(K,-1,int);status[0]=1
    area_observations=[(0,0.,True)];synth_history=[];trace=[];rng=np.random.default_rng(seed)
    fault_cost=synthesis_cost=algorithm_seconds=0.;since_synth=0.;qcount=0;scount=0
    start=time.perf_counter();last_checkpoint=None
    def synth(j):
        nonlocal synthesis_cost,since_synth,scount
        result=service.synthesis(int(j));status[j]=int(result['feasible']);synthesis_cost+=result['cost_seconds'];since_synth=0.;scount+=1
        synth_history.append((j,status[j]))
        area_observations.append((int(j),result['area_ratio'],result['timing_feasible']))
        return result
    if method.startswith('Eager-'):
        for j in range(1,K):synth(j)
    for step in range(max_actions):
        mark=time.perf_counter()
        L=f/M;U=(f+M-n)/M;mu=(f+1)/(n+2)
        S,G,gaps,pot,live=geometry(masks,L,U,status,epsilon,mu)
        certificate=G<=epsilon+1e-12
        last_checkpoint=dict(incumbent=S,certificate_gap=G,fault_queries=qcount,synthesis_queries=scount,
            fault_cost=fault_cost,synthesis_cost=synthesis_cost,algorithm_seconds=algorithm_seconds,
            status='certified' if certificate else 'running')
        if certificate:break
        unknown=np.flatnonzero(status==-1)
        # Unknown proposals already dominated by a verified incumbent need not be purchased.
        candidates=unknown[gaps[S,unknown]>epsilon+1e-12]
        challenger=int(live[np.argmax(gaps[S,live])])
        difference=np.flatnonzero((masks[challenger]!=masks[S])&(n<M))
        available=np.flatnonzero(n<M)
        if len(difference)==0:difference=available
        choose_synth=False;js=None;bit=None;reason=''
        if len(candidates):
            # Common optimistic ordering for all non-dual policies.
            js=int(candidates[np.argmax((masks@mu)[candidates])])
        q_model=None
        if method in ['Dual-Certificate','GP-Lazy-CLUCB']:
            q_model=probabilities(masks,area_observations,service.area_prior_per_bit,service.nominal_tmr_bits)
        if method=='GP-Lazy-CLUCB' and len(candidates):
            js=int(candidates[np.argmax(q_model[candidates]*gaps[S,candidates])])
        if method=='Dual-Certificate':
            excess=np.maximum(gaps[S]-epsilon,0.);excess[status==0]=0
            positive=np.maximum(masks-masks[S],0.);negative=np.maximum(masks[S]-masks,0.)
            contraction=(positive*(1-mu)+negative*mu)/(M*N)
            # First-order certificate-potential contraction. Not advertised as exact KG.
            gains=2*excess@contraction
            gains[n>=M]=-1
            bit=int(np.argmax(gains)) if len(available) else None
            fscore=max(0,float(gains[bit]))/max(service.fault_cost_estimate,1e-9) if bit is not None else 0.
            sscore=-1.;js_best=None
            for j in candidates:
                # Real infeasibility removes one challenger; real feasibility adds an incumbent.
                drop=(max(float(gaps[S,j])-epsilon,0.))**2
                probability=float(q_model[j])
                improve=max(0.,float(pot[S]-pot[j]))
                val=((1-probability)*drop+probability*improve)/max(service.synthesis_cost_estimate,1e-9)
                if val>sscore:sscore=val;js_best=int(j)
            choose_synth=js_best is not None and (sscore>=fscore or not len(available))
            js=js_best;reason='certificate_potential_synthesis' if choose_synth else 'certificate_potential_fault'
        elif method=='Alternating-CLUCB':
            choose_synth=js is not None and (scount==0 or since_synth>0 or not len(available));reason='fixed_alternation'
        elif method in ['Lazy-CLUCB','GP-Lazy-CLUCB']:
            choose_synth=js is not None and (scount==0 or since_synth>=service.synthesis_cost_estimate or not len(available));reason='equal_service_cost_lazy'
        else:reason='uniform' if method=='Eager-Uniform' else 'symmetric_difference'
        if not len(available) and js is not None:choose_synth=True
        if choose_synth:
            algorithm_seconds+=time.perf_counter()-mark
            result=synth(js)
            trace.append(dict(step=step,action='synthesis',candidate=js,feasible=result['feasible'],reason=reason,**last_checkpoint))
            continue
        if not len(available):raise RuntimeError('Unresolved finite certificate with exhausted labels and no unknown candidate')
        if method=='Eager-Uniform':
            # Even this simple baseline never samples bits common to every relevant candidate.
            active=live[gaps[S,live]>epsilon+1e-12]
            relevant=np.flatnonzero(np.any(masks[active]!=masks[S],axis=0)&(n<M))
            if not len(relevant):relevant=available
            eligible=relevant[n[relevant]==n[relevant].min()];bit=int(rng.choice(eligible))
        elif method!='Dual-Certificate':
            eligible=difference[n[difference]==n[difference].min()];bit=int(rng.choice(eligible))
        count=min(batch,M-int(n[bit]));algorithm_seconds+=time.perf_counter()-mark
        obs,cost=service.fault(int(bit),int(n[bit]),count)
        assert len(obs)==count and all(y in (0,1) for y in obs)
        n[bit]+=count;f[bit]+=sum(obs);qcount+=count;fault_cost+=cost;since_synth+=cost
        trace.append(dict(step=step,action='fault',bit=int(bit),count=count,failures=int(sum(obs)),reason=reason,**last_checkpoint))
    else:raise RuntimeError('Action guard exceeded')
    result=dict(last_checkpoint,method=method,seed=seed,algorithm_seconds=algorithm_seconds,
        total_service_seconds=fault_cost+synthesis_cost,estimated_total_seconds=fault_cost+synthesis_cost+algorithm_seconds,
        actual_policy_replay_wall_seconds=time.perf_counter()-start,queries_per_bit=n.tolist(),failures_per_bit=f.tolist(),
        known_feasible=np.flatnonzero(status==1).tolist(),known_infeasible=np.flatnonzero(status==0).tolist(),
        unqueried_candidates=np.flatnonzero(status==-1).tolist(),pool_size_per_bit=M,epsilon=epsilon,
        certificate_scope='deterministic finite-risk-pool and finite-catalogue certificate, conditional on hardware fault correspondence')
    return result,trace


def tests():
    rng=np.random.default_rng(190);
    for _ in range(200):
        N=7;M=8;K=12
        masks=(rng.random((K,N))<.3).astype(float);masks[0]=0
        pool=(rng.random((N,M))<rng.random((N,1))).astype(int)
        n=rng.integers(0,M+1,N);f=np.array([pool[i,:n[i]].sum() for i in range(N)])
        true=pool.mean(axis=1);L=f/M;U=(f+M-n)/M
        assert np.all(L<=true) and np.all(true<=U)
        status=rng.choice([-1,0,1],K);status[0]=1
        S,G,gaps,_,live=geometry(masks,L,U,status,.02,(f+1)/(n+2))
        for T in live:assert (masks[T]-masks[S])@true/N<=gaps[S,T]+1e-12
    return dict(status='passed',random_certificate_checks=200)

if __name__=='__main__':print(json.dumps(tests()))
