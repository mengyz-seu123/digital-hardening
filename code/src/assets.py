import argparse, json, shutil, time
from pathlib import Path
import hardware as h
from fault_runner import Runner, compile_sim
from graph_features import build


def load(folder, name='baseline', stream=True):
    folder=Path(folder);cfg=json.loads((folder/'config.json').read_text())
    bits=json.loads((folder/(name+'_bits.json')).read_text())
    return Runner(cfg,folder,name,folder/(name+'.vvp'),bits,stream=stream)


def prepare(origin, destination):
    origin=Path(origin);destination=Path(destination);destination.mkdir(parents=True,exist_ok=False)
    accepted=json.loads((origin/'acceptance.json').read_text());assert accepted['status']=='passed'
    cfg=accepted['config'];h.dump(destination/'config.json',cfg)
    names=['frozen.json','frozen.v','comb.v','logical_bits.json','baseline.v','baseline_mapped.v',
        'baseline_mapped.json','baseline_mapped_bits.json','baseline_hardware.json','cells.v','acceptance.json']
    for name in names:shutil.copyfile(origin/name,destination/name)
    logical=json.loads((destination/'logical_bits.json').read_text())
    phy=json.loads((origin/'baseline_bits.json').read_text())
    mapped=json.loads((destination/'baseline_mapped_bits.json').read_text())
    compile_sim(cfg,destination,'original',cfg['sources'],[],'top')
    compile_sim(cfg,destination,'baseline',[destination/'baseline.v'],phy)
    compile_sim(cfg,destination,'baseline_mapped',[destination/'cells.v',destination/'baseline_mapped.v'],mapped)
    runners=[load(destination,name,True) for name in ['original','baseline','baseline_mapped']]
    originals,base,mapped_runner=runners
    standalone=[load(destination,name,False) for name in ['baseline','baseline_mapped']]
    profiles=[];matched=0;independent_starts=0;begin=time.time()
    try:
        for seed in range(101,117):
            for runner in runners:
                r=runner.run(seed,domain='normal',profile=int(runner is base))
                assert r['label']=='NO_EFFECT',r
                if runner is base:profiles.append(r)
        for seed in [101,123]:
            cycle=cfg['cycle_min'] if seed==101 else (cfg['cycle_min']+cfg['cycle_max'])//2
            for i in range(len(logical)):
                a=base.run(seed,i,cycle,domain='stream_audit')
                b=mapped_runner.run(seed,i,cycle,domain='stream_audit')
                assert a['label']!='INVALID' and b['label']!='INVALID',(a,b)
                assert (a['label'],a['value_before'],a['value_after'])==(b['label'],b['value_before'],b['value_after']),(a,b)
                matched+=1
                if i<8 or i>=len(logical)-3:
                    for runner,row in zip(standalone,[a,b]):
                        fresh=runner.run(seed,i,cycle,domain='standalone_audit')
                        assert fresh['result']==row['result'],(fresh,row)
                        independent_starts+=1
        graph=build(destination,profiles)
        result=dict(status='passed',design_id=cfg['design_id'],logical_bits=len(logical),
            mapped_canonical_pairs=matched,stream_standalone_checks=independent_starts,
            elapsed=time.time()-begin,graph_nodes=graph['nodes'],graph_edges=len(graph['edges']),
            acceptance_origin=str(origin),upstream_files={f:h.sha(f) for f in cfg['sources']})
        h.dump(destination/'asset_audit.json',result)
        print('ASSET_PASS',json.dumps(result),flush=True)
        return result
    finally:
        for r in runners:r.stream.close()


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--run',required=True);ap.add_argument('--design',required=True)
    a=ap.parse_args();root=h.PROJECT/'runs'/a.run
    origin=h.PROJECT/'runs'/('acceptance-20260910-03' if a.design=='eth_fcs8' else 'acceptance-20260910-02')/a.design
    root.mkdir(exist_ok=True)
    prepare(origin,root/'assets'/a.design)
