import hashlib, json, re, subprocess, time, zlib
from pathlib import Path
import hardware as h
from benches import make_tb


def compile_sim(cfg, folder, name, sources, physical, module='dut'):
    folder = Path(folder)
    tb = make_tb(cfg, physical, folder/(name+'_tb.v'), module)
    exe = folder/(name+'.vvp')
    h.command(folder, name+'_compile', ['iverilog','-g2012','-s','tb','-o',str(exe)] + list(map(str,sources)) + [str(tb)])
    h.dump(folder/(name+'_bits.json'), physical)
    return exe


class Runner:
    def __init__(self, cfg, folder, name, executable, physical, stream=False):
        self.cfg, self.folder, self.name = cfg, Path(folder), name
        self.exe, self.physical = Path(executable), physical
        self.exe_hash = h.sha(self.exe)
        self.map_hash = hashlib.sha256(json.dumps(physical,sort_keys=True).encode()).hexdigest()
        self.rtl_hashes = {p:h.sha(p) for p in cfg['sources']}
        self.calls = 0
        from stream_io import Stream
        self.stream = Stream(self.exe) if stream else None

    def run(self, seed, target=-1, cycle=None, window=None, mode=0, domain='control', profile=0):
        cycle = self.cfg['cycle_min'] if cycle is None else cycle
        window = self.cfg['window'] if window is None else window
        argv = ['vvp',str(self.exe),'+seed=%d'%seed,'+target=%d'%target,'+cycle=%d'%cycle,
                '+window=%d'%window,'+mode=%d'%mode,'+profile=%d'%profile]
        if self.cfg['bench']=='crc':
            for j in range(4):
                data=bytes((i*73+seed*19+(i^(seed>>8)))&255 for i in range(j*16,(j+1)*16))
                argv.append('+fcs%d=%08x'%(j,zlib.crc32(data)))
        begin=time.time()
        try:
            if self.stream is not None:
                fcs=[a.split('=')[1] for a in argv if a.startswith('+fcs')] or ['0']*4
                line=' '.join(map(str,[seed,target,cycle,window,mode,profile]))+' '+' '.join(fcs)
                code,out=0,self.stream.exchange(line)
            else:
                p=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=20)
                code,out=p.returncode,p.stdout.decode(errors='replace')
        except subprocess.TimeoutExpired as exc:
            code,out=-999,(exc.stdout or b'').decode(errors='replace')
        matches=re.findall(r'^RESULT (.*)$',out,re.M)
        result={k:int(v) for k,v in re.findall(r'(\w+)=(-?\d+)',matches[0])} if len(matches)==1 else {}
        invalid=code!=0 or len(matches)!=1 or (target>=0 and result.get('applied')!=1)
        failures=[k for k in ['errors','unexpected','timeout'] if result.get(k,0)]
        label='INVALID' if invalid else ('FAIL' if failures else ('ALARM' if result.get('alarms',0) else 'NO_EFFECT'))
        r=None if target<0 else self.physical[target]
        row=dict(design_id=self.cfg['design_id'],family_id=self.cfg['family_id'],hardware=self.name,
            domain=domain,seed=seed,cycle=cycle,observation_end=window,mode=mode,physical_id=target,
            bit_id=None if r is None else r['bit_id'],replica=None if r is None else r['replica'],
            label=label,failure_subtype=failures,result=result,injection_applied=result.get('applied')==1,
            value_before=result.get('before'),value_after=result.get('after'),runtime=time.time()-begin,
            returncode=code,phase='clock_low_after_settle_before_posedge',executable_sha256=self.exe_hash,
            bit_map_sha256=self.map_hash,rtl_hashes=self.rtl_hashes,toolchain_id='oss-cad-suite-20260908',
            workload_id=self.cfg['bench']+'-independent-scoreboard-v020',golden_id='original/seed=%d'%seed,
            raw_stdout_sha256=hashlib.sha256(out.encode()).hexdigest())
        if profile: row['features']=[{k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',s)} for s in re.findall(r'^FEATURE (.*)$',out,re.M)]
        if invalid: row['raw_output']=out
        h.append(self.folder/(domain+'.jsonl'),row)
        self.calls+=1
        return row
