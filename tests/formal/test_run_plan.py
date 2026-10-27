import copy
import json
import unittest
from collections import Counter
from pathlib import Path
try:
    from formal_src.run_plan import compile_run_plan
except ImportError:
    compile_run_plan = None

class PlanTests(unittest.TestCase):
    def setUp(self):
        self.cohorts={'main':[f'q{i}' for i in range(200)],'diagnostic':[f'd{i}' for i in range(50)],'s50':[f'q{i}' for i in range(50)]}
    def profile(self):
        return json.loads(Path('configs/formal/formal-v1.json').read_text())
    def test_compiler_available(self): self.assertIsNotNone(compile_run_plan, 'compiler missing')
    def test_complete_deterministic_plan(self):
        self.assertIsNotNone(compile_run_plan)
        plan=compile_run_plan(self.profile(),self.cohorts)
        self.assertEqual(plan['logical_generation_count'],6600)
        self.assertEqual(plan['local_baseline_count'],1000)
        self.assertFalse(plan['executable'])
        self.assertEqual(len(plan['units']),7600)
        self.assertEqual(len({u['task_id'] for u in plan['units']}),7600)
        self.assertEqual(plan,compile_run_plan(self.profile(),{k:list(reversed(v)) for k,v in self.cohorts.items()}))
        generations=[u for u in plan['units'] if u['work_type']=='generation']
        self.assertEqual(Counter(u['partition'] for u in generations),{'main':3600,'diagnostic':900,'s50':2100})
        expected={'D':150,'L':150,'G':150,'I':150,'C0':150,'C1':150,'E2_k1':300,'E2_k4':300,'E2_no_association':150,'M2_E2':150,'M2_E3':150,'M2_E4':150}
        self.assertEqual(Counter(u['condition'] for u in generations if u['partition']=='s50'),expected)
        for condition in expected:
            self.assertEqual({u['repeat'] for u in generations if u['condition']==condition},{1,2,3})
        json.dumps(plan,allow_nan=False)
    def test_reject_missing_overlap_duplicates_and_wrong_subset(self):
        self.assertIsNotNone(compile_run_plan)
        for changes in [dict(s50=['absent']*50),dict(main=['q0']*200),dict(diagnostic=['q0']+[f'd{i}' for i in range(49)]),dict(diagnostic=[]),dict(gold={})]:
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError): compile_run_plan(self.profile(),{**self.cohorts,**changes})
    def test_formal_mutation_and_unverified_freeze_rejected(self):
        self.assertIsNotNone(compile_run_plan)
        for key,value in [('repeats',2),('k',4),('status','frozen'),('api_key','secret')]:
            profile={**self.profile(),key:value}
            with self.subTest(key=key),self.assertRaises(ValueError): compile_run_plan(profile,self.cohorts)
