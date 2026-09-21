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
