"""Read selected primitive columns from public Stata-118 files; no author code runs."""
from pathlib import Path
import math
import struct

TYPES = {65530: ('b', 1, 101), 65529: ('h', 2, 32741),
         65528: ('i', 4, 2147483621), 65527: ('f', 4, 1.7014118346046923e38),
         65526: ('d', 8, 8.98846567431158e307)}


def rows(path, selected):
    b = Path(path).read_bytes()
    def tag(key):
        i = b.index(('<' + key + '>').encode()) + len(key) + 2
        return b[i:b.index(('</' + key + '>').encode(), i)]
    assert tag('release') == b'118' and tag('byteorder') == b'LSF'
    k = struct.unpack('<H', tag('K'))[0]
    n = struct.unpack('<Q', tag('N'))[0]
    types = struct.unpack('<' + 'H' * k, tag('variable_types'))
    raw_names = tag('varnames')
    names = [raw_names[i*129:(i+1)*129].split(b'\0')[0].decode() for i in range(k)]
    assert set(selected) <= set(names), set(selected) - set(names)
    formats = [TYPES[t][:2] if t in TYPES else ('strl', 8) if t == 32768 else ('string', t) for t in types]
    offsets, width = [], 0
    for _, size in formats:
        offsets.append(width)
        width += size
    start = b.index(b'<data>') + 6
    assert b.index(b'</data>', start) - start == n * width
    use = [i for i, name in enumerate(names) if name in selected]
    assert all(formats[i][0] != 'strl' for i in use), 'strL selected: unsupported'
    for j in range(n):
        result = {}
        for i in use:
            fmt, size = formats[i]
            at = start + j * width + offsets[i]
            if fmt == 'string':
                value = b[at:at+size].split(b'\0')[0].decode('utf8', 'replace')
            else:
                value = struct.unpack_from('<' + fmt, b, at)[0]
                if value >= TYPES[types[i]][2] or not math.isfinite(value):
                    value = None
            result[names[i]] = value
        yield result
