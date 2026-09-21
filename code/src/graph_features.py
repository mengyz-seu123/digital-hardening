import collections, json, math
from pathlib import Path
import hardware as h


def build(folder, profiles):
    folder=Path(folder);mod=json.loads((folder/'frozen.json').read_text())['modules']['top']
    rows=json.loads((folder/'logical_bits.json').read_text());n=len(rows)
    out=folder/'primitive_comb.json'
    h.yosys(folder,'primitive_comb','read_verilog %s; hierarchy -top comb_core; proc; flatten; techmap; opt; write_json %s'%(folder/'comb.v',out))
    comb=json.loads(out.read_text())['modules']['comb_core'];ports=comb['ports']
    states={ports['st_%d'%i]['bits'][0]:i for i in range(n)}
    drivers={}
    for cell in comb['cells'].values():
        ins=[b for p,bs in cell['connections'].items() if cell['port_directions'][p]=='input' for b in bs if isinstance(b,int)]
        for p,bs in cell['connections'].items():
            if cell['port_directions'][p]=='output':
                for b in bs:drivers[b]=ins
    def upstream(roots):
        todo=list(roots);seen=set();found=set()
        while todo:
            b=todo.pop()
            if b in seen:continue
            seen.add(b)
            if b in states:found.add(states[b])
            elif isinstance(b,int):todo.extend(drivers.get(b,[]))
        return found
    edges=set()
    for i in range(n):
        roots=list(ports['nx_%d'%i]['bits'])
        for key in ['en_%d'%i,'srst_%d'%i]:
            if key in ports:roots+=ports[key]['bits']
        for src in upstream(roots):edges.add((src,i))
        if rows[i]['ff_type'] in ('$dffe','$sdffe','$sdffce'):edges.add((i,i))
    incoming=[set() for _ in rows];outgoing=[set() for _ in rows]
    for a,b in edges:incoming[b].add(a);outgoing[a].add(b)
    direct=set()
    for name,port in mod['ports'].items():
        if port['direction']=='output':direct.update(upstream(ports[name]['bits']))
    distance=[n+1]*n;queue=collections.deque()
    for i in sorted(direct):distance[i]=0;queue.append(i)
    while queue:
        i=queue.popleft()
        for j in incoming[i]:
            if distance[j]>distance[i]+1:distance[j]=distance[i]+1;queue.append(j)
    names=['log_fanin','log_fanout','output_proximity','direct_output','self_feedback']
    types=['$dff','$dffe','$sdff','$sdffe','$sdffce'];names+=types
    names+=['enable','sync_reset','toggle_rate','write_rate','unknown_rate']
    features=[]
    for i,r in enumerate(rows):
        cell=mod['cells'][r['source_cell']]
        samples=[(p['features'][i],p['observation_end']) for p in profiles]
        rates=[sum(s[k]/w for s,w in samples)/len(samples) for k in ['toggles','writes','unknowns']]
        features.append([math.log1p(len(incoming[i])),math.log1p(len(outgoing[i])),1/(1+distance[i]),
            int(i in direct),int((i,i) in edges)]+[int(r['ff_type']==t) for t in types]+
            [int('EN' in cell['connections']),int('SRST' in cell['connections'])]+rates)
    result=dict(edges=sorted(edges),features=features,feature_names=names,nodes=n,feature_scope='structural and fault-free only')
    h.dump(folder/'graph.json',result);return result
