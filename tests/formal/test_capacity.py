import unittest
class CapacityTests(unittest.TestCase):
 def test_missing_models_or_conditions_cannot_pass_from_small_sample(self):
  from formal_src.capacity import estimate
  result=estimate([{'condition':'E2','model_role':'primary','elapsed_ms':10000,'usage':{'input_tokens':1000,'output_tokens':100},'status':'answered'}],remaining_attempts=7968)
  self.assertFalse(result['throughput_validated']);self.assertEqual(result['logical_generations'],6600)
  self.assertEqual(result['estimate_tokens'],7260000);self.assertIsNone(result['estimated_cost_cny'])
 def test_failed_calls_count_in_sample_and_coverage(self):
  from formal_src.capacity import estimate
  rows=[{'condition':'E2','model_role':'primary','elapsed_ms':1000,'usage':{},'status':'failed'}]
  result=estimate(rows,remaining_attempts=6500)
  self.assertFalse(result['attempt_budget_sufficient']);self.assertIsNone(result['estimate_tokens']);self.assertEqual(result['failed_sample_count'],1)
