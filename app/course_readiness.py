"""Read-only qualification of coursework inputs before inference begins.

This module deliberately returns no provider, throughput, budget, or human
review attestations. Evaluation files are read only here for identity metadata;
their answers and all input text stay out of the persistable evidence report.
"""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path, PurePosixPath

from formal_src.contracts import relative, safe_json
from formal_src.data_protocol import source_key
from formal_src.provider import digest
from formal_src.structure import project_structure
from src.ragpilot.common import GOLD_KEYS, reject_gold


def _blind(value):
    safe_json(value)
    # reject_gold is deliberately broader than safe_json (it includes answer).
    reject_gold(value)
    def normalized_keys(node):
        if isinstance(node, dict):
            if any(key.lower().replace('-', '_') in GOLD_KEYS for key in node):
                raise ValueError('Evaluation fields are forbidden')
            for child in node.values():
                normalized_keys(child)
        elif isinstance(node, list):
            for child in node:
                normalized_keys(child)
    normalized_keys(value)


class _Qualification:
    def __init__(self, record, verified_resources):
        self.root = Path(record['root']).resolve()
        self.manifest = record['manifest']
        self.errors = defaultdict(set)
        self.bindings = {}
        self.counts = {}
        self.source_counts = {}
        self.evaluation_ids = []
        expected = self.manifest.get('resources', [])
        verified = {r.get('path'): r for r in verified_resources} if verified_resources is not None else None
        for item in expected:
            try:
                name = item['path']
                relative(name)
                path = (self.root / name).resolve()
                if name in self.bindings or not path.is_relative_to(self.root) or not path.is_file():
                    raise ValueError()
                if verified is not None:
                    check = verified.get(name, {})
                    if check.get('status') != 'ready' or any(check.get(k) != item.get(k) for k in ('kind', 'sha256')):
                        raise ValueError()
                elif hashlib.sha256(path.read_bytes()).hexdigest() != item.get('sha256'):
                    raise ValueError()
                self.bindings[name] = dict(item, absolute=path)
            except (KeyError, TypeError, ValueError, OSError):
                self.block('inputs_complete', 'course_resource_unverified')

    def block(self, gate, code):
        self.errors[gate].add(code)

    def load(self, basename, *, required=True, kinds=('input',), blind=True):
        matches = [b for b in self.bindings.values() if b['absolute'].name == basename and b['kind'] in kinds]
        if not matches and not required:
            return None
        if len(matches) != 1:
            self.block('inputs_complete', 'course_input_binding_missing' if not matches else 'course_input_binding_duplicate')
            return None
        return self.read(matches[0], blind=blind)

    def read(self, binding, *, blind=True):
        try:
            path = binding['absolute']
            if path.suffix == '.jsonl':
                value = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
            else:
                value = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, OSError, UnicodeError):
            self.block('inputs_complete', 'course_input_json_invalid')
            return None
        if blind:
            try:
                _blind(value)
            except ValueError:
                self.block('inputs_complete', 'course_inference_sensitive_fields')
                return None
        return value

    def questions(self, questions, cohorts):
        indexed = {}
        roles = defaultdict(list)
        doc_sources = {}
        for row in questions:
            if isinstance(row, dict) and not row.get('source_id') and isinstance(row.get('source_report'), str):
                row = dict(row, source_id=source_key(row['source_report']))
            if not isinstance(row, dict) or not all(isinstance(row.get(k), str) and row[k].strip()
                                                   for k in ('question_id', 'doc_id', 'source_id', 'question', 'split')):
                self.block('cohorts_validated', 'course_question_identity_invalid')
                continue
            if 'source_report' in row and not isinstance(row['source_report'], str):
                self.block('cohorts_validated', 'course_question_identity_invalid')
                continue
            qid = row['question_id']
            if qid in indexed:
                self.block('cohorts_validated', 'course_question_id_duplicate')
            indexed[qid] = row
            if row['split'] not in ('development', 'validation', 'main', 'diagnostic'):
                self.block('cohorts_validated', 'course_question_role_invalid')
            roles[row['split']].append(row)
            if row['doc_id'] in doc_sources and doc_sources[row['doc_id']] != row['source_id']:
                self.block('source_disjoint', 'course_document_source_conflict')
            doc_sources[row['doc_id']] = row['source_id']
        self.counts = {role: len(rows) for role, rows in roles.items()}
        if not isinstance(cohorts, dict):
            self.block('cohorts_validated', 'course_cohorts_invalid')
            cohorts = {}
        allowed = {'development', 'validation', 'main', 'diagnostic', 's50'}
        if set(cohorts) - allowed:
            self.block('cohorts_validated', 'course_cohorts_invalid')
        for role, size in (('main', 200), ('development', 50), ('validation', 50), ('diagnostic', 50)):
            rows = roles[role]
            ids = cohorts.get(role, [])
            expected_size = size if role == 'main' or rows or ids else 0
            if (not isinstance(ids, list) or any(not isinstance(v, str) for v in ids)
                    or len(ids) != expected_size or len(set(ids)) != len(ids)
                    or len(rows) != expected_size or set(ids) != {q['question_id'] for q in rows}):
                self.block('cohorts_validated', 'course_' + role + '_cohort_invalid')
        if 's50' in cohorts:
            ids = cohorts['s50']
            if (not isinstance(ids, list) or any(not isinstance(v, str) for v in ids)
                    or len(ids) != 50 or len(set(ids)) != 50
                    or not set(ids) <= {q['question_id'] for q in roles['main']}):
                self.block('cohorts_validated', 'course_s50_invalid')
        sources = {role: {q['source_id'] for q in rows} for role, rows in roles.items()}
        self.source_counts = {role: len(ids) for role, ids in sources.items()}
        if self.source_counts.get('main') != 40:
            self.block('cohorts_validated', 'course_main_source_count')
        # Diagnostic questions deliberately reuse the main source pool in the
        # formal protocol; their qids remain unique. Other roles stay disjoint.
        for identity in ('source_id', 'doc_id', 'source_report'):
            sets = {role: {source_key(q[identity]) if identity == 'source_report' else q[identity]
                           for q in rows if q.get(identity)} for role, rows in roles.items()}
            for left, right in (('development', 'validation'), ('development', 'main'),
                                ('development', 'diagnostic'), ('validation', 'main'), ('validation', 'diagnostic')):
                if sets.get(left, set()) & sets.get(right, set()):
                    self.block('source_disjoint', 'course_source_overlap')
        if not sources.get('diagnostic', set()) <= sources.get('main', set()):
            self.block('source_disjoint', 'course_diagnostic_source_outside_main')
        if any(n > 3 for n in Counter(q['doc_id'] for q in indexed.values()).values()):
            self.block('cohorts_validated', 'course_page_question_quota')
        return indexed, roles

    def evidence(self, questions, roles, units, documents):
        indexed = {}
        by_doc = defaultdict(list)
        doc_sources = defaultdict(set)
        doc_reports = defaultdict(set)
        for q in questions.values():
            doc_sources[q['doc_id']].add(q['source_id'])
            if q.get('source_report'):
                doc_reports[q['doc_id']].add(source_key(q['source_report']))
        for row in units:
            if not isinstance(row, dict) or not all(isinstance(row.get(k), str) and row[k].strip()
                                                   for k in ('evidence_id', 'doc_id', 'text')):
                self.block('inputs_complete', 'course_evidence_identity_invalid')
                continue
            if row['evidence_id'] in indexed:
                self.block('inputs_complete', 'course_evidence_id_duplicate')
            indexed[row['evidence_id']] = row
            by_doc[row['doc_id']].append(row)
            if row.get('source_id') and doc_sources.get(row['doc_id']) and row['source_id'] not in doc_sources[row['doc_id']]:
                self.block('source_disjoint', 'course_evidence_source_mismatch')
            if row.get('source_report') and doc_reports.get(row['doc_id']):
                if not isinstance(row['source_report'], str) or source_key(row['source_report']) not in doc_reports[row['doc_id']]:
                    self.block('source_disjoint', 'course_evidence_source_mismatch')
            if row.get('question_exposure') is True:
                self.block('inputs_complete', 'course_evidence_question_exposure')
        if any(q['doc_id'] not in by_doc for q in questions.values()):
            self.block('inputs_complete', 'course_question_evidence_missing')
        if documents is not None:
            docs = {}
            for row in documents:
                if not isinstance(row, dict) or not isinstance(row.get('doc_id'), str) or row['doc_id'] in docs:
                    self.block('inputs_complete', 'course_document_identity_invalid')
                    continue
                docs[row['doc_id']] = row
            content_roles = defaultdict(set)
            for role, qs in roles.items():
                for q in qs:
                    doc = docs.get(q['doc_id'], {})
                    if not doc or (doc.get('source_id') and doc['source_id'] != q['source_id']):
                        self.block('inputs_complete', 'course_question_document_mismatch')
                    for key, kind in (('pdf_path', 'pdf'), ('ocr_path', 'ocr')):
                        if key not in doc:
                            continue
                        binding = self.bindings.get(doc[key]) if isinstance(doc[key], str) else None
                        if not binding or binding['kind'] != kind:
                            self.block('inputs_complete', 'course_document_asset_missing')
                        elif key == 'pdf_path':
                            content_roles[binding['sha256']].add('main' if role == 'diagnostic' else role)
            if any(len(values) > 1 for values in content_roles.values()):
                self.block('source_disjoint', 'course_source_content_overlap')
        return indexed

    def multimodal(self, units, structures):
        for unit in units.values():
            image_path = unit.get('image_path')
            binding = self.bindings.get(image_path) if isinstance(image_path, str) else None
            if not binding or binding['kind'] != 'input' or binding['sha256'] != unit.get('image_sha256'):
                self.block('inputs_complete', 'course_image_binding_invalid')
                continue
            try:
                with binding['absolute'].open('rb') as stream:
                    header = stream.read(8)
                if not header.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8')):
                    self.block('inputs_complete', 'course_image_format_invalid')
            except OSError:
                self.block('inputs_complete', 'course_image_binding_invalid')
        if not isinstance(structures, list):
            self.block('inputs_complete', 'course_structure_missing')
            return
        structures_by_id = {}
        for row in structures:
            if not isinstance(row, dict) or row.get('evidence_id') not in units:
                self.block('inputs_complete', 'course_structure_binding_invalid')
                continue
            eid = row['evidence_id']
            if eid in structures_by_id or row.get('evidence_sha') != digest(units[eid]):
                self.block('inputs_complete', 'course_structure_binding_invalid')
            structures_by_id[eid] = row
            if row.get('question_exposure') is True:
                self.block('inputs_complete', 'course_structure_question_exposure')
            try:
                if any(' '.join(n['text'].split()) not in ' '.join(units[eid]['text'].split()) for n in row['nodes']):
                    self.block('inputs_complete', 'course_structure_text_outside_evidence')
            except (KeyError, TypeError, AttributeError):
                self.block('inputs_complete', 'course_structure_invalid')
        if set(structures_by_id) != set(units):
            self.block('inputs_complete', 'course_structure_missing')
        try:
            project_structure(structures, list(units))
        except (KeyError, TypeError, AttributeError, ValueError):
            self.block('inputs_complete', 'course_structure_invalid')

    def model(self):
        modules = [r for r in self.bindings.values() if r['kind'] == 'model' and r['absolute'].name == 'modules.json']
        if len(modules) != 1:
            self.block('inputs_complete', 'course_model_binding_incomplete')
            return
        # Manifest bindings always use POSIX separators, so derive the prefix with
        # PurePosixPath; Path would emit backslashes on Windows and never match.
        prefix = PurePosixPath(modules[0]['path']).parent.as_posix()
        def bound(name):
            key = name if prefix in ('', '.') else prefix + '/' + name
            binding = self.bindings.get(key)
            return binding and binding['kind'] == 'model' and binding['absolute'].stat().st_size > 0
        if not all(bound(name) for name in ('config.json', 'tokenizer.json')) or not any(bound(name) for name in ('model.safetensors', 'pytorch_model.bin')):
            self.block('inputs_complete', 'course_model_binding_incomplete')
        value = self.read(modules[0])
        if not isinstance(value, list) or not value:
            self.block('inputs_complete', 'course_model_binding_incomplete')
            return
        for module in value:
            if not isinstance(module, dict) or not isinstance(module.get('type'), str):
                self.block('inputs_complete', 'course_model_binding_incomplete')
            elif module['type'].endswith('.Pooling'):
                folder = module.get('path', '')
                if not isinstance(folder, str) or not bound(str(PurePosixPath(folder) / 'config.json')):
                    self.block('inputs_complete', 'course_model_binding_incomplete')

    def evaluation(self, main):
        bindings = [r for r in self.bindings.values() if r['kind'] == 'evaluation']
        if not bindings:
            self.block('inputs_complete', 'course_evaluation_missing')
            return
        identities = []
        main_by_id = {q['question_id']: q for q in main}
        for binding in bindings:
            # Evaluation content never enters safe_json, inference loading, or
            # evidence. Only these identity fields are inspected for alignment.
            values = self.read(binding, blind=False)
            if not isinstance(values, list):
                self.block('inputs_complete', 'course_evaluation_identity_mismatch')
                continue
            for row in values:
                if not isinstance(row, dict) or not isinstance(row.get('question_id'), str):
                    self.block('inputs_complete', 'course_evaluation_identity_mismatch')
                    continue
                qid = row['question_id']
                question = main_by_id.get(qid)
                if not question or any(key in row and row[key] != question.get(key) for key in ('source_id', 'doc_id')):
                    self.block('inputs_complete', 'course_evaluation_identity_mismatch')
                identities.append(qid)
        if len(identities) != 200 or len(set(identities)) != len(identities) or set(identities) != set(main_by_id):
            self.block('inputs_complete', 'course_evaluation_identity_mismatch')
        self.evaluation_ids = identities

    def result(self):
        gates = {name: not self.errors[name] for name in ('source_disjoint', 'cohorts_validated', 'inputs_complete')}
        evidence = {'schema_version': 1, 'checker': 'course-local-qualification-v1',
                    'execution_scope': self.manifest.get('execution_scope', 'full'),
                    'counts': self.counts, 'source_counts': self.source_counts,
                    'checks': gates, 'human_reviewed': False,
                    'evaluation_identity_count': len(self.evaluation_ids),
                    'evaluation_identity_sha256': digest(sorted(self.evaluation_ids)),
                    'resource_bindings_sha256': digest(sorted(
                        ({k: b[k] for k in ('kind', 'path', 'sha256')} for b in self.bindings.values()),
                        key=lambda b: b['path']))}
        blocks = sorted({code for codes in self.errors.values() for code in codes})
        return {'applicable': True, 'passed': not blocks, 'qualification': gates,
                'blocking_items': blocks, 'evidence': evidence, 'evidence_sha256': digest(evidence)}


def qualify_course_resources(record, *, verified_resources=None):
    """Return local gates and deterministic, persistable, answer-free evidence.

    ``record`` is ResourceRegistry's ``{'root': ..., 'manifest': ...}`` record.
    Passing its already hash-verified report ``resources`` avoids hashing files
    twice. The caller still owns persistence and all runtime provider checks.
    Human-reviewed packages retain the existing qualification path.
    """
    manifest = record.get('manifest', {})
    if manifest.get('purpose') != 'formal' or manifest.get('review_policy') != 'automatic_course':
        return {'applicable': False, 'passed': False, 'qualification': {}, 'blocking_items': []}
    checker = _Qualification(record, verified_resources)
    questions = checker.load('inference_questions.jsonl')
    units = checker.load('evidence_units.jsonl')
    cohorts = checker.load('cohorts.json')
    documents = checker.load('documents.jsonl', required=False)
    structures = checker.load('draft_structures.jsonl', required=False)
    if structures is None:
        structures = checker.load('approved_structures.jsonl', required=False, kinds=('approved_description',))
    if not isinstance(questions, list):
        checker.block('cohorts_validated', 'course_questions_missing')
        questions = []
    if not isinstance(units, list):
        checker.block('inputs_complete', 'course_evidence_missing')
        units = []
    indexed, roles = checker.questions(questions, cohorts)
    evidence = checker.evidence(indexed, roles, units, documents)
    checker.multimodal(evidence, structures)
    checker.model()
    checker.evaluation(roles.get('main', []))
    return checker.result()
