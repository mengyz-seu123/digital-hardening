import json
from pathlib import Path


def wire(bit):
    return "1'b" + bit if isinstance(bit, str) else 'b%d' % bit


def vector(bits):
    return wire(bits[0]) if len(bits) == 1 else '{' + ', '.join(wire(b) for b in reversed(bits)) + '}'


def inventory(mod):
    rows = []
    for name, cell in sorted(mod['cells'].items()):
        if 'dff' not in cell['type']:
            continue
        if cell['type'] not in ('$dff', '$dffe', '$sdff', '$sdffe', '$sdffce'):
            raise ValueError('Unsupported state cell: ' + cell['type'])
        assert int(cell['parameters']['CLK_POLARITY'], 2) == 1
        for offset, bit in enumerate(cell['connections']['Q']):
            aliases = ['%s[%d]' % (n, i) for n, net in mod['netnames'].items()
                       for i, b in enumerate(net['bits']) if b == bit and not n.startswith('$')]
            rows.append(dict(bit_id=len(rows), source_cell=name, source_offset=offset,
                             q_net=bit, aliases=aliases, ff_type=cell['type']))
    if not rows:
        raise ValueError('No state found')
    return rows


def emit(mod, selected, target, module='dut'):
    rows = inventory(mod)
    cells = mod['cells']
    ports = mod['ports']
    lines = ['module %s(%s);' % (module, ', '.join(ports))]
    for name, port in ports.items():
        width = '' if len(port['bits']) == 1 else '[%d:0] ' % (len(port['bits']) - 1)
        lines.append('%s wire %s%s;' % (port['direction'], width, name))
    bits = sorted({b for c in cells.values() for port in c['connections'].values() for b in port if isinstance(b, int)} |
                  {b for p in ports.values() for b in p['bits'] if isinstance(b, int)})
    lines.extend('wire b%d;' % bit for bit in bits)
    for name, port in ports.items():
        left, right = vector(port['bits']), name
        if port['direction'] == 'output':
            left, right = right, left
        lines.append('assign %s = %s;' % (left, right))
    physical = []
    for row in rows:
        cell = cells[row['source_cell']]
        conn, par = cell['connections'], cell['parameters']
        replicas = ['A', 'B', 'C'] if row['bit_id'] in selected else ['A']
        states = []
        for replica in replicas:
            name = 'q_%02d_%s' % (row['bit_id'], replica)
            states.append(name)
            physical.append(dict(row, physical_id=len(physical), replica=replica, path='dut.' + name))
            lines.append('(* keep = 1, dont_touch = 1 *) reg %s;' % name)
            initial = None
            for net in mod['netnames'].values():
                if row['q_net'] in net['bits'] and 'init' in net.get('attributes', {}):
                    idx = net['bits'].index(row['q_net'])
                    initial = net['attributes']['init'][-1-idx]
            if initial in ('0', '1'):
                lines.append("initial %s = 1'b%s;" % (name, initial))
            assign = '%s <= %s;' % (name, wire(conn['D'][row['source_offset']]))
            reset = None
            if 'SRST' in conn:
                signal = vector(conn['SRST'])
                if not int(par['SRST_POLARITY'], 2):
                    signal = '!(' + signal + ')'
                value = (int(par['SRST_VALUE'], 2) >> row['source_offset']) & 1
                reset = "if (%s) %s <= 1'b%d; else " % (signal, name, value)
            enable = None
            if 'EN' in conn:
                signal = vector(conn['EN'])
                if not int(par['EN_POLARITY'], 2):
                    signal = '!(' + signal + ')'
                enable = 'if (%s) ' % signal
            if cell['type'] == '$sdffce':
                body = (enable or '') + 'begin ' + (reset or '') + assign + ' end'
            else:
                body = (reset or '') + (enable or '') + assign
            lines.append('always @(posedge %s) begin %s end' % (vector(conn['CLK']), body))
        voted = states[0]
        if len(states) == 3:
            a, b, c = states
            voted = '((%s & %s) | (%s & %s) | (%s & %s))' % (a,b,a,c,b,c)
        lines.append('assign %s = %s;' % (wire(row['q_net']), voted))
    binary = {'$and': '&', '$or': '|', '$xor': '^', '$logic_and': '&&', '$logic_or': '||',
              '$eq': '==', '$ne': '!=', '$add': '+', '$sub': '-'}
    unary = {'$not': '~', '$logic_not': '!', '$reduce_or': '|', '$reduce_and': '&',
             '$reduce_bool': '|'}
    for name, cell in sorted(cells.items()):
        typ, conn = cell['type'], cell['connections']
        if 'dff' in typ or typ == '$scopeinfo':
            continue
        if typ in binary:
            expr = '(%s %s %s)' % (vector(conn['A']), binary[typ], vector(conn['B']))
        elif typ in unary:
            expr = '%s(%s)' % (unary[typ], vector(conn['A']))
        elif typ == '$mux':
            expr = '%s ? %s : %s' % (vector(conn['S']), vector(conn['B']), vector(conn['A']))
        else:
            raise ValueError('Unsupported combinational cell: ' + typ)
        lines.append('assign %s = %s;' % (vector(conn['Y']), expr))
    lines.append('endmodule')
    target.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return physical
