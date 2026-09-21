import os, select, subprocess, time


class Stream:
    def __init__(self, executable):
        self.p=subprocess.Popen(['vvp',str(executable),'+stream'],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,bufsize=0)
        self.pending=b''

    def exchange(self, line, timeout=20):
        self.p.stdin.write((line+'\n').encode());self.p.stdin.flush()
        end=time.monotonic()+timeout
        while b'DONE_EVENT\n' not in self.pending:
            left=end-time.monotonic()
            if left<=0:raise TimeoutError('vvp stream timeout')
            if not select.select([self.p.stdout],[],[],left)[0]:raise TimeoutError('vvp stream timeout')
            part=os.read(self.p.stdout.fileno(),65536)
            if not part:raise RuntimeError('vvp stream terminated: '+self.pending.decode(errors='replace'))
            self.pending+=part
        out,self.pending=self.pending.split(b'DONE_EVENT\n',1)
        return out.decode(errors='replace')

    def close(self):
        if self.p.poll() is None:
            self.p.stdin.close()
            try:self.p.wait(timeout=3)
            except subprocess.TimeoutExpired:self.p.terminate();self.p.wait(timeout=3)
