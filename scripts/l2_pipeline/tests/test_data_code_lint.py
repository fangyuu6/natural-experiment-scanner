"""CI guard: pipeline executables belong to scripts, data copies are evidence."""
import ast
import json
from pathlib import Path
import tempfile
import unittest

from scripts.l2_pipeline.run6_adapters.paths import (
    MOVED_ADAPTERS, REPO_ROOT, code_path, relocated_code_path,
)


def agent_work_path(path):
    """Only per-agent/reviewer workspaces can contain generated helper code."""
    parts = Path(path).parts
    work = any(p == 'work' or p.startswith(('work_', '_work')) or p.endswith('_work')
               or '_work_' in p for p in parts)
    return ('agents' in parts or 'reviewer' in parts) and work


def archived_helper_evidence(root):
    """Exact source+archive byte pairs only; new or changed helpers still fail."""
    from scripts.l2_pipeline.storage import sha
    root=Path(root).resolve();allowed=set()
    manifest=root/'data/l2_pipeline/registry/CODE_EVIDENCE_C64M.json'
    if not manifest.is_file():return allowed
    value=json.loads(manifest.read_text())
    if value.get('schema')!='l2_archived_helper_evidence_v1' or value.get('production_imports_forbidden') is not True:return allowed
    for record in value.get('files',[]):
        source=root/record['path'];archive=root/record['archive']
        if source.is_symlink() or archive.is_symlink():continue
        if (source.resolve().is_relative_to(root/'data/l2_pipeline') and source.suffix=='.py'
                and archive.resolve().is_relative_to(root/'data/l2_pipeline/registry/archive/C64M_CODE_EVIDENCE')
                and archive.name==record['sha256']+'.py.txt' and source.is_file() and archive.is_file()
                and sha(source)==record['sha256']==sha(archive)):
            allowed.add(source.resolve())
    return allowed


def staged_read_scope_evidence(root):
    """C74 permits only exact path/hash pairs inside a queue packet's read_scope."""
    from scripts.l2_pipeline.storage import sha
    root = Path(root).resolve()
    queue = root/'data/l2_pipeline/registry/SONNET_QUEUE'
    allowed = set()
    for manifest in queue.glob('*/*/READ_SCOPE.json'):
        if any(p.is_symlink() for p in (manifest, *manifest.parents)):
            continue
        try:
            value = json.loads(manifest.read_text())
            if not isinstance(value, dict) or value.get('schema') != 'l2_packet_read_scope_v1':
                continue
            rows = value.get('files', [])
            if not isinstance(rows, list):
                continue
        except (OSError, ValueError):
            continue
        for row in rows:
            try:
                relative = Path(row['staged'])
                if relative.is_absolute() or '..' in relative.parts or relative.parts[0] != 'read_scope':
                    continue
                path = manifest.parent/relative
                if (any(p.is_symlink() for p in (path, *path.parents))
                        or not path.resolve().is_relative_to(manifest.parent/'read_scope')):
                    continue
                if path.is_file() and path.suffix.casefold() == '.py' and sha(path) == row['sha256']:
                    allowed.add(path.resolve())
            except (OSError, ValueError, KeyError, TypeError, IndexError):
                continue
    return allowed


def active_devcopy_evidence(root):
    """C74 permits an isolated task tree while its base-copy manifest exists."""
    allowed = set()
    for manifest in (Path(root).resolve()/'data/l2_pipeline/devcopy').glob('*/BASE_COPY_MANIFEST.json'):
        if not manifest.is_file() or any(p.is_symlink() for p in (manifest, *manifest.parents)):
            continue
        base = manifest.parent
        for path in base.rglob('*'):
            if (path.is_file() and path.suffix.casefold() == '.py'
                    and not any(p.is_symlink() for p in (path, *path.parents))
                    and path.resolve().is_relative_to(base)):
                allowed.add(path.resolve())
    return allowed


def new_data_python(repo, legacy_paths):
    """Permit bound evidence and the explicit C74 temporary-copy scopes only."""
    root = Path(repo).resolve()
    snapshot_paths = archived_helper_evidence(root)
    checked = set()
    from scripts.l2_pipeline.snapshots import validate
    candidates = list((root/'data/l2_pipeline').rglob('*.job.json'))
    candidates += list((root/'data/l2_pipeline').rglob('FREEZE_*.json'))
    candidates += list((root/'data/l2_pipeline').rglob('CODE_SNAPSHOT_*.json'))
    candidates += list((root/'data/l2_pipeline/autopilot/c63/snapshot_evidence').glob('*.json'))
    for manifest_path in candidates:
        try:
            value = json.loads(manifest_path.read_text())
            if not isinstance(value, dict): continue
            contract = value.get('contract', value)
            if not isinstance(contract, dict): continue
            record = contract.get('code_snapshot')
            if not isinstance(record, dict): continue
            study = Path(contract.get('snapshot_root', str(manifest_path.parent))).resolve()
            if not study.is_relative_to((root/'data/l2_pipeline').resolve()): continue
            key = (str(study), record['digest'])
            if key in checked: continue
            inventory = contract.get('snapshot_source_inventory', contract.get('code_inventory'))
            tree = validate(study, record, inventory)
            checked.add(key)
            snapshot_paths.update((tree/relative).resolve() for relative in record['entries'])
        except (OSError, ValueError, KeyError, TypeError):
            # A broken snapshot grants no exception, even for neighbouring files.
            continue
    # C62 excludes the concurrently owned YRBS extraction task. Only exact
    # recorded helper bytes are exempt, never a directory or a future file.
    excluded = root/'data/l2_pipeline/autopilot/c62/EXCLUDED_YRBS_CODE.json'
    if excluded.exists():
        from scripts.l2_pipeline.storage import sha
        for relative,digest in json.loads(excluded.read_text()).get('paths',{}).items():
            path=root/relative
            if relative.startswith('data/l2_pipeline/connectors_v2/cdc_yrbs_sadc/extract_tool/') and path.is_file() and sha(path)==digest:
                snapshot_paths.add(path.resolve())
    # C63 owner-authorized development copies are isolated, hash-bound source trees.
    from scripts.l2_pipeline.ops.devcopy import validated_files
    snapshot_paths.update(validated_files(root))
    queue = root/'data/l2_pipeline/registry/SONNET_QUEUE'
    read_scope_paths = staged_read_scope_evidence(root)
    snapshot_paths.update(active_devcopy_evidence(root))
    return sorted(p.relative_to(root).as_posix()
                  for p in (root/'data/l2_pipeline').rglob('*')
                  if p.is_file() and p.suffix.casefold() == '.py'
                  and ((p.is_relative_to(queue) and p not in read_scope_paths)
                       or (not p.is_relative_to(queue) and p not in snapshot_paths
                           and not agent_work_path(p.relative_to(root))
                           and p.relative_to(root).as_posix() not in legacy_paths)))


def data_code_violations(source):
    tree = ast.parse(source)
    bindings = {}
    violations = []
    execution_aliases = {}
    module_aliases = {}

    def pieces(node, seen=()):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.Name) and node.id in bindings and node.id not in seen:
            return pieces(bindings[node.id], (*seen, node.id))
        return [part for child in ast.iter_child_nodes(node) for part in pieces(child, seen)]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module_aliases.update({alias.asname or alias.name: alias.name for alias in node.names})
        if isinstance(node, ast.ImportFrom) and node.module in {'subprocess', 'runpy', 'os', 'sys', 'builtins', 'importlib', 'importlib.util', 'importlib.machinery'}:
            execution_aliases.update({alias.asname or alias.name: node.module+'.'+alias.name for alias in node.names})
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            bindings[node.target.id] = node.value
    for node in ast.walk(tree):
        imports = []
        if isinstance(node, ast.Import):
            imports = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imports = [node.module or '']
        if any(name.casefold() == 'data' or name.casefold().startswith('data.') for name in imports):
            violations.append((node.lineno, 'import from data/'))
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func)
        prefix, separator, suffix = name.partition('.')
        name = execution_aliases.get(prefix, module_aliases.get(prefix, prefix)) + separator + suffix
        sink = name.split('.')[-1]
        if not (name.startswith(('subprocess.', 'os.', 'runpy.', 'importlib.')) or
                name in {'exec', '__import__', 'builtins.exec', 'builtins.__import__'} or
                name in {'sys.path.insert', 'sys.path.append'}):
            continue
        if sink not in {'run', 'Popen', 'call', 'check_call', 'check_output', 'system',
                        'exec', 'execv', 'execve', 'execvp', 'run_path',
                        'spec_from_file_location', 'SourceFileLoader', 'import_module',
                        '__import__', 'insert', 'append'}:
            continue
        values = pieces(node)
        joined = '/'.join(values).casefold()
        if any(value.casefold().startswith('data.') for value in values) or (
            ('data/' in joined or '/data/' in joined or joined == 'data') and
            ('.py' in joined or sink in {'insert', 'append'})
        ):
            violations.append((node.lineno, 'execute or load code under data/'))
    return violations


class DataCodeLintTests(unittest.TestCase):
    def test_read_scope_requires_matching_hash_and_packet_path(self):
        from scripts.l2_pipeline.storage import sha
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            packet = root/'data/l2_pipeline/registry/SONNET_QUEUE/done/job'
            staged = packet/'read_scope/0/source.py'
            staged.parent.mkdir(parents=True)
            staged.write_text('pass\n')
            outside = packet/'outside.py'
            outside.write_text('pass\n')
            unlisted = staged.with_name('unlisted.py')
            unlisted.write_text('pass\n')
            manifest = packet/'READ_SCOPE.json'
            manifest.write_text(json.dumps(dict(schema='l2_packet_read_scope_v1', files=[
                dict(staged='read_scope/0/source.py', sha256=sha(staged)),
                dict(staged='outside.py', sha256=sha(outside))])))
            relative = lambda p: p.relative_to(root).as_posix()
            self.assertEqual(new_data_python(root, set()), sorted(map(relative, (outside, unlisted))))
            alias = packet/'alias.py'
            alias.symlink_to(staged)
            self.assertEqual(new_data_python(root, set()), sorted(map(relative, (outside, unlisted, alias))))
            alias.unlink()
            work = packet/'reviewer/work/helper.py'
            work.parent.mkdir(parents=True)
            work.write_text('pass\n')
            # Other allowances must not turn a queue packet into a blanket waiver.
            self.assertEqual(new_data_python(root, {relative(outside)}),
                             sorted(map(relative, (outside, unlisted, work))))
            work.unlink()
            staged.write_text('changed\n')
            self.assertEqual(new_data_python(root, set()), sorted(map(relative, (staged, outside, unlisted))))

    def test_read_scope_rejects_escape_symlink_and_invalid_manifest(self):
        from scripts.l2_pipeline.storage import sha
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            packet = root/'data/l2_pipeline/registry/SONNET_QUEUE/pending/job'
            scope = packet/'read_scope'
            scope.mkdir(parents=True)
            outside = packet/'outside.py'
            outside.write_text('pass\n')
            link = scope/'link.py'
            link.symlink_to(outside)
            manifest = packet/'READ_SCOPE.json'
            for entries in ([dict(staged='read_scope/../outside.py', sha256=sha(outside))],
                            [dict(staged=str(outside), sha256=sha(outside))],
                            [dict(staged='read_scope/link.py', sha256=sha(outside))], [None]):
                manifest.write_text(json.dumps(dict(schema='l2_packet_read_scope_v1', files=entries)))
                self.assertEqual(staged_read_scope_evidence(root), set())
            manifest.write_text('invalid json')
            self.assertEqual(staged_read_scope_evidence(root), set())

    def test_devcopy_requires_task_base_manifest_including_helpers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            base = root/'data/l2_pipeline/devcopy/TASK'
            paths = [base/'scripts/pkg/source.py', base/'RUN_VERIFIED_TESTS.py', base/'scratchpad/helper.py']
            other = base.with_name('OTHER')/'unregistered.py'
            for path in [*paths, other]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('pass\n')
            relative = lambda p: p.relative_to(root).as_posix()
            self.assertEqual(new_data_python(root, set()), sorted(map(relative, [*paths, other])))
            manifest = base/'BASE_COPY_MANIFEST.json'
            manifest.write_text('{}\n')
            self.assertEqual(new_data_python(root, set()), [relative(other)])
            outside_alias = other.with_name('alias.py')
            outside_alias.symlink_to(paths[0])
            self.assertEqual(new_data_python(root, set()), sorted(map(relative, (other, outside_alias))))
            outside_alias.unlink()
            link = base/'linked.py'
            link.symlink_to(other)
            self.assertEqual(new_data_python(root, set()), sorted(map(relative, (link, other))))
            manifest.unlink()
            self.assertEqual(active_devcopy_evidence(root), set())

    def test_no_new_python_files_under_data(self):
        baseline = json.loads((REPO_ROOT/'scripts/l2_pipeline/intake/legacy_data_code_paths.json').read_text())
        self.assertEqual(new_data_python(REPO_ROOT, set(baseline['paths'])), [])

    def test_new_data_lint_allows_only_snapshot_and_agent_work(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            allowed = 'data/l2_pipeline/old.py'
            helpers = ('data/l2_pipeline/work/design/v1/agents/A/check.py',
                       'data/l2_pipeline/final_work_v4/design/v1/agents/auditor/check.py',
                       'data/l2_pipeline/audit_work/design/v1/reviewer/check.py')
            forbidden = ('data/l2_pipeline/new.py', 'data/l2_pipeline/work/helper.py',
                         'data/l2_pipeline/agents/helper.py', 'data/l2_pipeline/OTHER.PY',
                         'data/l2_pipeline/framework/agents/helper.py')
            for relative in (allowed, *helpers, *forbidden):
                p = root/relative
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text('pass\n')
            self.assertEqual(new_data_python(root, {allowed}), sorted(forbidden))

    def test_archived_helpers_require_exact_source_and_archive_no_directory_waiver(self):
        from scripts.l2_pipeline.storage import sha,ensure_once
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();source=root/'data/l2_pipeline/acquisition/helper.py'
            source.parent.mkdir(parents=True);source.write_text('pass\n');digest=sha(source)
            archive=root/('data/l2_pipeline/registry/archive/C64M_CODE_EVIDENCE/'+digest+'.py.txt')
            archive.parent.mkdir(parents=True);archive.write_bytes(source.read_bytes())
            ensure_once(root/'data/l2_pipeline/registry/CODE_EVIDENCE_C64M.json',dict(schema='l2_archived_helper_evidence_v1',production_imports_forbidden=True,
                files=[dict(path=str(source.relative_to(root)),sha256=digest,archive=str(archive.relative_to(root)))]))
            self.assertEqual(archived_helper_evidence(root),{source})
            extra=source.with_name('new.py');extra.write_text('pass\n')
            self.assertEqual(new_data_python(root,set()),[str(extra.relative_to(root))])
            source.write_text('changed\n');self.assertEqual(archived_helper_evidence(root),set())
            source.write_bytes(archive.read_bytes());archive.write_text('changed\n')
            self.assertEqual(archived_helper_evidence(root),set())

    def test_content_addressed_bound_snapshot_is_allowed_and_tamper_is_not(self):
        from scripts.l2_pipeline.snapshots import create
        from scripts.l2_pipeline.storage import sha, write_once
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve(); source = root/'scripts/pkg/entry.py'
            source.parent.mkdir(parents=True); source.write_text('VALUE=1\n')
            study = root/'data/l2_pipeline/work/design/v1'
            inventory = {str(source):sha(source)}
            record = create(study, inventory, root)
            # A snapshot from a failed job emission remains explicitly bound.
            self.assertEqual(new_data_python(root, set()), [])
            write_once(study/'FREEZE_PREP.json', dict(code_snapshot=record, code_inventory=inventory))
            self.assertEqual(new_data_python(root, set()), [])
            tree = study/record['base']/'tree'
            bound = tree/'scripts/pkg/entry.py'
            original = bound.read_bytes(); bound.write_text('VALUE=2\n')
            self.assertIn(bound.relative_to(root).as_posix(), new_data_python(root, set()))
            bound.write_bytes(original)
            extra = tree/'scripts/pkg/unbound.py'; extra.write_text('VALUE=3\n')
            self.assertIn(extra.relative_to(root).as_posix(), new_data_python(root, set()))

    def test_pipeline_has_no_data_imports_or_executions(self):
        errors = []
        for path in (REPO_ROOT / 'scripts/l2_pipeline').rglob('*.py'):
            if 'tests' in path.parts:
                continue
            try:
                code_path(path)
            except ValueError as exc:
                errors.append(str(exc))
            errors.extend(f'{path.relative_to(REPO_ROOT)}:{line}: {reason}'
                          for line, reason in data_code_violations(path.read_text()))
        self.assertEqual(errors, [])

    def test_lint_rejects_import_exec_and_split_path(self):
        for source in (
            'from data.l2_pipeline.runs.run6 import adapter',
            'import data.l2_pipeline.runs.run6.adapter',
            "subprocess.run(['python3', 'data/l2_pipeline/adapter.py'])",
            "p=ROOT/'data'/'l2_pipeline'/'adapter.py'\nsubprocess.Popen(['python3',p])",
            "importlib.import_module('data.l2_pipeline.runs.run6.adapter')",
            "sys.path.insert(0,str(ROOT/'data'/'l2_pipeline'))",
            "runpy.run_path('data/l2_pipeline/adapter.py')",
            "import subprocess as sp\nsp.run(['python3','data/l2_pipeline/adapter.py'])",
            "from importlib.util import spec_from_file_location as load\nload('adapter','data/l2_pipeline/adapter.py')",
            "from runpy import run_path as replay\nreplay('data/l2_pipeline/adapter.py')",
            "from sys import path as paths\npaths.append(str(ROOT/'data'))",
            "p: Path=ROOT/'data'/'adapter.py'\nsubprocess.run(['python3',p])",
            "exec(open(ROOT/'data'/'adapter.py').read())",
            "import builtins as b\nb.exec(Path('data/l2_pipeline/adapter.py').read_text())",
        ):
            with self.subTest(source=source):
                self.assertTrue(data_code_violations(source))

    def test_data_reads_and_archived_mapping_are_permitted(self):
        self.assertEqual(data_code_violations(
            "p=ROOT/'data'/'l2_pipeline'/'history.json'\nvalue=json.loads(p.read_text())"), [])
        self.assertEqual(data_code_violations(
            "archives={'data/l2_pipeline/old.py':'scripts/l2_pipeline/new.py'}"), [])

    def test_guard_resolves_symlinks_and_refuses_data_python(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / 'data/adapter.py'
            data.parent.mkdir()
            data.write_text('pass\n')
            link = root / 'scripts/adapter.py'
            link.parent.mkdir()
            link.symlink_to(data)
            for path in (data, link, 'data/adapter.py', 'scripts/missing.py',
                         root.parent/'outside.py', 'data/adapter.PY'):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    code_path(path, root)

    def test_all_reusable_archived_adapters_have_script_relocations(self):
        reusable = list((REPO_ROOT/'data/l2_pipeline/cards').glob('run*/_meta/*.py'))
        reusable += list((REPO_ROOT/'data/l2_pipeline/runs/run6').glob('*.py'))
        for domain in ('cdc', 'cms', 'pdaps', 'social'):
            reusable += list((REPO_ROOT/'data/l2_pipeline/runs/run6'/domain).glob('*.py'))
        for archived in reusable:
            relative = archived.relative_to(REPO_ROOT).as_posix()
            with self.subTest(archive=relative):
                self.assertIn(relative, MOVED_ADAPTERS)
                moved = relocated_code_path(archived)
                self.assertTrue(moved.is_file())
                self.assertTrue(moved.is_relative_to(REPO_ROOT/'scripts/l2_pipeline'))


if __name__ == '__main__':
    unittest.main()
