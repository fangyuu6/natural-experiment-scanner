"""Minimal read-only .xlsx reader on the standard library (openpyxl is not installed in the L2 environment).

Reads cell text and numbers only: no formulas are evaluated and nothing is executed. Used for Census/NCES/USDA
downloads, which are untrusted data. Returns {sheet_name: {(row, col): value}} with 1-based rows and columns."""
import re
import zipfile
import xml.etree.ElementTree as ET

from .common import ConnectorError

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
RNS = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
MAX_MEMBER = 200_000_000


def _read(z, name):
    info = z.getinfo(name)
    if info.file_size > MAX_MEMBER:
        raise ConnectorError('xlsx member too large: ' + name)
    return z.read(name)


def _column(ref):
    letters = re.match(r'[A-Z]+', ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def read_xlsx(path):
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared = []
        if 'xl/sharedStrings.xml' in names:
            root = ET.fromstring(_read(z, 'xl/sharedStrings.xml'))
            for si in root.iter(NS + 'si'):
                shared.append(''.join(t.text or '' for t in si.iter(NS + 't')))
        wb = ET.fromstring(_read(z, 'xl/workbook.xml'))
        rels = ET.fromstring(_read(z, 'xl/_rels/workbook.xml.rels'))
        targets = {r.get('Id'): r.get('Target') for r in rels}
        sheets = {}
        for sheet in wb.iter(NS + 'sheet'):
            target = targets[sheet.get(RNS + 'id')].lstrip('/')
            member = target if target.startswith('xl/') else 'xl/' + target
            cells = {}
            root = ET.fromstring(_read(z, member))
            for c in root.iter(NS + 'c'):
                ref, kind = c.get('r'), c.get('t')
                v = c.find(NS + 'v')
                if kind == 'inlineStr':
                    value = ''.join(t.text or '' for t in c.iter(NS + 't'))
                elif v is None or v.text is None:
                    continue
                elif kind == 's':
                    value = shared[int(v.text)]
                elif kind in ('str', 'e', 'b'):
                    value = v.text
                else:
                    value = float(v.text)
                row = int(re.search(r'\d+', ref).group(0))
                cells[row, _column(ref)] = value
            sheets[sheet.get('name')] = cells
        return sheets
