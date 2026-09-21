from pathlib import Path
import bisect, hashlib, json, re, subprocess, time

PERIOD=10.0
CYCLES=600
METHODS=("baseline", "q4", "q8", "shared_tmr")
DEADLINE=1000.0

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')

def command(folder, name, argv, timeout=180):
    start = time.monotonic()
    p = subprocess.run(list(map(str, argv)), cwd=folder, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=timeout)
    output = p.stdout.decode('utf-8', errors='replace')
    (folder / (name + '.log')).write_text(output, encoding='utf-8')
    with (folder / 'commands.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps(dict(argv=list(map(str, argv)), returncode=p.returncode,
                               elapsed_s=time.monotonic()-start, log=name+'.log'))+'\n')
    if p.returncode:
        raise RuntimeError(f'{name}: exit {p.returncode}: {output[-1800:]}')
    return output

def read_wave(path):
    rows = []
    for line in path.read_text().splitlines()[1:]:
        values = list(map(float, line.split()))
        if not values or not all(map(math.isfinite, values)):
            raise ValueError(f'invalid ngspice waveform: {path}')
        rows.append(values)
    if len(rows) < 100 or rows[-1][0] < 5.9e-6:
        raise ValueError(f'incomplete ngspice waveform: {path}')
    if any(a[0] > b[0] for a, b in zip(rows, rows[1:])):
        raise ValueError('nonmonotonic waveform time')
    return rows

def sample(rows, t_ns, column=1):
    t = t_ns * 1e-9
    k = bisect.bisect_right(rows, t, key=lambda r: r[0])
    if k == 0:
        return rows[0][column]
    if k == len(rows):
        return rows[-1][column]
    a, b = rows[k-1], rows[k]
    return a[column] + (b[column]-a[column]) * (t-a[0])/(b[0]-a[0])

def crossing(rows, threshold):
    for a, b in zip(rows, rows[1:]):
        if a[1] < threshold <= b[1]:
            return 1e9*(a[0] + (b[0]-a[0])*(threshold-a[1])/(b[1]-a[1]))
    raise ValueError('DESAT threshold was never reached')

def spice(folder, netlist):
    folder.mkdir(parents=True, exist_ok=False)
    (folder/'circuit.cir').write_text(netlist, encoding='utf-8')
    log = command(folder, 'ngspice', ['ngspice', '-b', 'circuit.cir'])
    if re.search(r'(?im)^(?:Error:|.*simulation\(s\) aborted|doAnalyses:)', log):
        raise RuntimeError(f'ngspice analysis failed: {folder}')
    return read_wave(folder/'wave.txt')

def stimulus(phase, wave=None, side=0, width=None, deadtime=3):
    words = []
    for i in range(CYCLES):
        t = phase+i*PERIOD
        rst = int(i < 10)
        we = int(i == 12)
        hi, lo = 0, 0
        if width is None:
            hi = int(20 <= i < 550)
        elif i >= 20:
            j = (i-20) % (2*(width+deadtime+6))
            a = j < width
            b = width+deadtime+6 <= j < 2*width+deadtime+6
            hi, lo = (int(a), int(b)) if side == 0 else (int(b), int(a))
        fault = int(wave is not None and sample(wave, t) < 1.65)
        words.append((rst<<12)|(hi<<11)|(lo<<10)|(fault<<9)|(we<<8)|deadtime)
    return words

def metrics(rows, reference, method, phase, short_ns=None):
    col = METHODS.index(method)+1
    first_fault = next((phase+r[0]*PERIOD for r in rows if r[col] & 0x800), None)
    first_gate_off = None
    if short_ns is not None:
        first_gate_off = next((phase+r[0]*PERIOD for r in rows
                               if phase+r[0]*PERIOD >= short_ns and not r[col]&0xc000), None)
    false_trip = first_fault is not None and (short_ns is None or first_fault < short_ns)
    delay = None if first_gate_off is None else first_gate_off-short_ns
    # An early spurious trip is a functional failure, never credited as fast SC protection.
    missed = short_ns is not None and first_gate_off is None
    late = short_ns is not None and (missed or delay > DEADLINE)
    lost = sum(bool(ref[1]&0xc000) and not bool(r[col]&0xc000)
               for r, ref in zip(rows, reference)
               if short_ns is None or phase+r[0]*PERIOD < short_ns)
    unintended = sum(bool(r[col]&0xc000) and not bool(ref[1]&0xc000)
                     for r, ref in zip(rows, reference))
    return dict(false_trip=bool(false_trip), missed_shutdown=bool(missed), late_shutdown=bool(late),
                first_fault_ns=first_fault, gate_off_ns=first_gate_off, shutdown_delay_ns=delay,
                lost_drive_cycles=lost, unexpected_drive_cycles=unintended,
                bridge_overlap_cycles=sum((r[col]&0xc000)==0xc000 for r in rows),
                normal_output_mismatch_cycles=sum(r[col]!=ref[1] for r, ref in zip(rows, reference)),
                shared_tmr_exact_match=all(r[4]==r[1] for r in rows))
