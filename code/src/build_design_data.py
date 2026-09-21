import argparse, json, random, time
from pathlib import Path
import hardware as h
from assets import load
IDS=['axis_simple8','axis_skid8','uart_tx8','uart_rx8','eth_fcs8']


def event(design, domain, bit, index, trial=0):
    d=IDS.index(design)
    offsets={'source':10000000,'reference':100000000,'query':400000000,'evaluation':900000000}
    seed=offsets[domain]+d*20000000+trial*1000000+bit*1000+index
    return seed,random.Random(seed ^ 0x5A17)


def build(root, design):
    asset=root/'assets'/design
    assert json.loads((asset/'asset_audit.json').read_text())['status']=='passed'
    cfg=json.loads((asset/'config.json').read_text())
    mod=json.loads((asset/'frozen.json').read_text())['modules']['top']
    bits=json.loads((asset/'logical_bits.json').read_text())
    basehw=json.loads((asset/'baseline_hardware.json').read_text())
    destination=root/'source'/design;destination.mkdir(parents=True,exist_ok=False)
    original=load(asset,'original',True);runner=load(asset,'baseline',True)
    original.folder=destination;runner.folder=destination
    begin=time.time();counts=[]
    try:
        for i in range(len(bits)):
            failed=alarms=0
            for j in range(32):
                seed,rng=event(design,'source',i,j)
                assert original.run(seed,domain='golden')['label']=='NO_EFFECT'
                row=runner.run(seed,i,rng.randint(cfg['cycle_min'],cfg['cycle_max']),domain='source_events')
                assert row['label']!='INVALID',row
                failed+=row['label']=='FAIL';alarms+=row['label']=='ALARM'
            counts.append(dict(bit_id=i,n=32,f=failed,alarms=alarms))
        h.dump(destination/'counts.json',dict(design_id=design,family_id=cfg['family_id'],counts=counts,
            events=len(bits)*32,elapsed=time.time()-begin,domain='source_only',golden_checks=original.calls))
        print('SOURCE_PASS',design,len(bits)*32,round(time.time()-begin,3),flush=True)
    finally:original.stream.close();runner.stream.close()
    costs=[];costroot=root/'costs'/design;costroot.mkdir(parents=True,exist_ok=False)
    begin=time.time()
    for i in range(len(bits)):
        folder=costroot/('bit_%03d'%i);folder.mkdir()
        physical=h.emit(mod,bits,asset/'comb.v',[i],folder/'singleton.v')
        mapped,mp,hw=h.map_hardware(folder,'singleton',folder/'singleton.v',physical,mod['ports'])
        delta=hw['area']-basehw['area'];assert delta>0,(design,i,delta)
        costs.append(dict(bit_id=i,delta_area=delta,timing_feasible=hw['timing_feasible'],hardware=hw))
        if i%10==0:print('COST',design,i,len(bits),flush=True)
    h.dump(costroot/'costs.json',dict(design_id=design,baseline_area=basehw['area'],costs=costs,
        elapsed=time.time()-begin,scope='mapped singleton incremental area; multi-bit sets require independent re-mapping'))
    print('DATA_READY',design,len(bits),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--design',required=True)
    a=p.parse_args();build(h.PROJECT/'runs'/a.run,a.design)
