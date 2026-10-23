"""Minimal read-only BIFF8 (.xls, OLE2 compound file) cell reader on the standard library.

xlrd is not installed in the L2 environment. This reads cell text and numbers only (SST, LABELSST, LABEL, NUMBER,
RK, MULRK); nothing is executed and no formulas are evaluated. Input files are untrusted public downloads.
Returns {sheet_name: {(row, col): value}} with 1-based rows and columns."""
import struct

from .common import ConnectorError

_FREE, _END = 0xFFFFFFFF, 0xFFFFFFFE
MAX_BYTES = 60_000_000


def _ole_stream(data, wanted=('Workbook', 'Book')):
    if len(data) > MAX_BYTES or data[:8] != b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
        raise ConnectorError('Not an OLE2 compound file')
    shift = struct.unpack_from('<H', data, 30)[0]
    mini_shift = struct.unpack_from('<H', data, 32)[0]
    sector, mini_sector = 1 << shift, 1 << mini_shift
    n_fat, dir_start, _, cutoff, mini_start, n_minifat, difat_start, n_difat = struct.unpack_from('<IIIIIIII', data, 44)[0:1] + struct.unpack_from('<IIIIIII', data, 48)
    difat = list(struct.unpack_from('<109I', data, 76))
    nxt = difat_start
    for _ in range(n_difat):
        base = (nxt + 1) * sector
        block = struct.unpack_from('<%dI' % (sector // 4), data, base)
        difat.extend(block[:-1])
        nxt = block[-1]
    fat = []
    for s in difat:
        if s in (_FREE, _END):
            continue
        base = (s + 1) * sector
        fat.extend(struct.unpack_from('<%dI' % (sector // 4), data, base))

    def chain(start, table):
        out, seen = [], set()
        while start not in (_END, _FREE):
            if start in seen or start >= len(table):
                raise ConnectorError('Corrupt OLE2 chain')
            seen.add(start)
            out.append(start)
            start = table[start]
        return out

    def read_chain(start):
        return b''.join(data[(s + 1) * sector:(s + 2) * sector] for s in chain(start, fat))

    directory = read_chain(dir_start)
    entries = []
    for i in range(0, len(directory), 128):
        e = directory[i:i + 128]
        nlen = struct.unpack_from('<H', e, 64)[0]
        name = e[:max(nlen - 2, 0)].decode('utf-16le', 'replace')
        entries.append((name, e[66], struct.unpack_from('<I', e, 116)[0], struct.unpack_from('<I', e, 120)[0]))
    root = entries[0]
    for name, kind, start, size in entries:
        if name in wanted and kind == 2:
            if size < cutoff:
                mini = read_chain(root[2])
                minifat = []
                for s in chain(mini_start, fat):
                    minifat.extend(struct.unpack_from('<%dI' % (sector // 4), data, (s + 1) * sector))
                out = b''.join(mini[s * mini_sector:(s + 1) * mini_sector] for s in chain(start, minifat))
            else:
                out = read_chain(start)
            return out[:size]
    raise ConnectorError('No workbook stream in OLE2 file')


def _rk(v):
    if v & 2:
        x = float(v >> 2 if v < 0x80000000 else (v >> 2) - (1 << 30))
        x = float(struct.unpack('<i', struct.pack('<I', v))[0] >> 2)
    else:
        x = struct.unpack('<d', struct.pack('<Q', (v & 0xFFFFFFFC) << 32))[0]
    return x / 100 if v & 1 else x


def read_xls(path):
    with open(path, 'rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    book = _ole_stream(data)
    pos, n = 0, len(book)
    records = []
    while pos + 4 <= n:
        rid, length = struct.unpack_from('<HH', book, pos)
        records.append((rid, book[pos + 4:pos + 4 + length], pos))
        pos += 4 + length
    # Shared string table, with CONTINUE records spliced in order.
    sst, strings = None, []
    index = 0
    while index < len(records):
        rid, body, _ = records[index]
        if rid == 0x00FC:
            chunks = [body]
            j = index + 1
            while j < len(records) and records[j][0] == 0x003C:
                chunks.append(records[j][1])
                j += 1
            strings = _parse_sst(chunks)
            break
        index += 1
    sheets, order = {}, []
    for rid, body, pos in records:
        if rid == 0x0085:
            offset = struct.unpack_from('<I', body, 0)[0]
            nchars, flags = body[6], body[7]
            raw = body[8:8 + nchars * (2 if flags & 1 else 1)]
            order.append((raw.decode('utf-16le' if flags & 1 else 'latin1', 'replace'), offset))
    by_offset = {off: name for name, off in order}
    current, cells = None, {}
    for rid, body, pos in records:
        if rid == 0x0809:
            kind = struct.unpack_from('<H', body, 2)[0]
            if pos in by_offset and kind == 0x0010:
                current, cells = by_offset[pos], {}
                sheets[current] = cells
            else:
                current = None
            continue
        if current is None:
            continue
        if rid == 0x00FD:
            r, c, _, i = struct.unpack_from('<HHHI', body, 0)
            cells[r + 1, c + 1] = strings[i]
        elif rid == 0x0203:
            r, c, _ = struct.unpack_from('<HHH', body, 0)
            cells[r + 1, c + 1] = struct.unpack_from('<d', body, 6)[0]
        elif rid == 0x027E:
            r, c, _, v = struct.unpack_from('<HHHI', body, 0)
            cells[r + 1, c + 1] = _rk(v)
        elif rid == 0x00BD:
            r, c0 = struct.unpack_from('<HH', body, 0)
            count = (len(body) - 6) // 6
            for k in range(count):
                v = struct.unpack_from('<I', body, 4 + 2 + 6 * k)[0]
                cells[r + 1, c0 + k + 1] = _rk(v)
        elif rid == 0x0204:
            r, c, _, ln = struct.unpack_from('<HHHH', body, 0)
            flags = body[8]
            cells[r + 1, c + 1] = body[9:9 + ln * (2 if flags & 1 else 1)].decode('utf-16le' if flags & 1 else 'latin1', 'replace')
    return sheets


def _parse_sst(chunks):
    out = []
    buf = chunks[0]
    total, unique = struct.unpack_from('<II', buf, 0)
    ci, pos = 0, 8
    cur = buf
    while len(out) < unique:
        if pos >= len(cur):
            ci += 1
            if ci >= len(chunks):
                break
            cur, pos = chunks[ci], 0
        nchars = struct.unpack_from('<H', cur, pos)[0]
        flags = cur[pos + 2]
        pos += 3
        runs = struct.unpack_from('<H', cur, pos)[0] if flags & 8 else 0
        pos += 2 if flags & 8 else 0
        ext = struct.unpack_from('<I', cur, pos)[0] if flags & 4 else 0
        pos += 4 if flags & 4 else 0
        wide = flags & 1
        text = []
        remaining = nchars
        while remaining:
            if pos >= len(cur):
                ci += 1
                cur, pos = chunks[ci], 0
                wide = cur[pos] & 1
                pos += 1
            take = min(remaining, (len(cur) - pos) // (2 if wide else 1))
            seg = cur[pos:pos + take * (2 if wide else 1)]
            text.append(seg.decode('utf-16le' if wide else 'latin1', 'replace'))
            pos += take * (2 if wide else 1)
            remaining -= take
        pos += 4 * runs + ext
        out.append(''.join(text))
    return out
