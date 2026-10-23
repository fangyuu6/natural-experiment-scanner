"""Stdlib-only reader for a RAND-style .xlsx workbook (openpyxl is not installed). Read only; values as strings/numbers."""
from __future__ import annotations
import re
import zipfile
import xml.etree.ElementTree as ET

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
RNS = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


def _col(ref):
    letters = re.match(r'[A-Z]+', ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def sheets(path):
    with zipfile.ZipFile(path) as z:
        wb = ET.fromstring(z.read('xl/workbook.xml'))
        rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        target = {r.get('Id'): r.get('Target') for r in rels}
        return {s.get('name'): 'xl/' + target[s.get(RNS + 'id')].lstrip('/').replace('xl/', '') for s in wb.iter(NS + 's' + 'heet')}


def read_sheet(path, name):
    """Rows as lists of cell values (None for empty). Dates stay as the stored serial/number (RAND stores y/m/d in separate columns)."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            for si in ET.fromstring(z.read('xl/sharedStrings.xml')).iter(NS + 'si'):
                shared.append(''.join(t.text or '' for t in si.iter(NS + 't')))
        root = ET.fromstring(z.read(sheets(path)[name]))
    out = []
    for row in root.iter(NS + 'row'):
        cells = {}
        for c in row.iter(NS + 'c'):
            t, v = c.get('t'), c.find(NS + 'v')
            if t == 'inlineStr':
                val = ''.join(x.text or '' for x in c.iter(NS + 't'))
            elif v is None or v.text is None:
                continue
            elif t == 's':
                val = shared[int(v.text)]
            elif t in ('str', 'e'):
                val = v.text
            elif t == 'b':
                val = v.text == '1'
            else:
                f = float(v.text)
                val = int(f) if f == int(f) else f
            cells[_col(c.get('r'))] = val
        if cells:
            out.append([cells.get(i) for i in range(max(cells) + 1)])
    return out
