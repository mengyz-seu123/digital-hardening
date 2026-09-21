from __future__ import annotations

import completion_aware as q

p = q.p0
_original_make_tb = p.make_tb


def make_tb_with_prereset(plan):
    text = _original_make_tb(plan)
    text = text.replace('reg [9:0] words[0:599];', 'reg [8:0] words[0:599];', 1)
    needle = ''' $readmemh(input_path,words); out=$fopen(output_path,"w");
 #(phase);'''
    replacement = ''' $readmemh(input_path,words);
 // Harness-only initialization: clock synchronous reset before logging.
 rst=1; start=0; stop=0; clear=0; fault=0; marker=0; pwm=0;
 repeat(4) begin #5; clk=1; #0.001; #4.999; clk=0; end
 out=$fopen(output_path,"w");
 #(phase);'''
    if text.count(needle) != 1:
        raise RuntimeError('transfer TB template changed; refusing silent patch')
    return text.replace(needle, replacement, 1)


def transfer_words_fixed(phase, wave=None, jitter=0, marker=True, pattern=0):
    words=[]
    for i in range(600):
        t=phase+10*i
        rst=int(i<10)
        start=int(i==12)
        stop=0
        clear=0
        fault=int(wave is not None and p.c.sample(wave,t)<1.65)
        if pattern==0:
            pwm=((i//17)&1) | (((i//23)&1)<<1) | (((i//31)&1)<<2)
        else:
            pwm=((i//(9+pattern))&1) | (((i//(13+pattern))&1)<<1) | (((i//(19+pattern))&1)<<2)
        mark=0
        if marker:
            k=p.math.ceil((980+jitter-phase)/10)
            mark=int(i==k)
        # TB concat: {marker,rst,start,stop,clear,fault,pwm[2:0]} = 9 bits.
        words.append((mark<<8)|(rst<<7)|(start<<6)|(stop<<5)|(clear<<4)|(fault<<3)|pwm)
    return words


p.make_tb = make_tb_with_prereset
p.transfer_words = transfer_words_fixed
q.main()
