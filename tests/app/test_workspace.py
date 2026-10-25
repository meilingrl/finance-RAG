"""Selecting next-launch output workspace must preserve existing outputs."""
import importlib,tempfile,unittest
from pathlib import Path
class WorkspaceTests(unittest.TestCase):
 def module(self):
  try:return importlib.import_module('app.workspace')
  except ModuleNotFoundError:self.fail('workspace selection not implemented')
 def test_saved_workspace_is_effective_next_launch_and_cli_override_wins(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td)/'project';root.mkdir();old=root/'artifacts/formal-v1';old.mkdir(parents=True);sentinel=old/'old-result';sentinel.write_text('preserve')
   target=Path(td)/'new-results';result=self.module().configure_workspace(root,str(target),old)
   self.assertTrue(result['restart_required']);self.assertEqual(self.module().resolve_workspace(root),target.resolve())
   self.assertEqual(self.module().resolve_workspace(root,old),old.resolve());self.assertEqual(sentinel.read_text(),'preserve')
 def test_unsafe_or_unrelated_nonempty_directory_is_rejected(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td)/'project';root.mkdir();foreign=Path(td)/'notes';foreign.mkdir();(foreign/'user.txt').write_text('do not touch')
   for target in [root,root/'src',root.parent,foreign,Path('relative/path')]:
    with self.subTest(target=target),self.assertRaises(ValueError):self.module().configure_workspace(root,str(target),root/'artifacts/formal-v1')
   self.assertFalse((root/'artifacts/app-entry.json').exists())
 def test_application_settings_apply_workspace_selection_without_losing_provider(self):
  from app.service import ApplicationService
  with tempfile.TemporaryDirectory() as td:
   root=Path(td)/'project';root.mkdir();old=root/'artifacts/original';old.mkdir(parents=True);(old/'runs').mkdir()
   service=ApplicationService(root,old);service.save_settings({'base_url':'https://example.invalid/v1/responses','model':'test-model'})
   target=Path(td)/'new-output';result=service.save_settings({'output_directory':str(target)})
   self.assertTrue(result['restart_required']);self.assertEqual(result['model'],'test-model')
   self.assertEqual(self.module().resolve_workspace(root),target.resolve());self.assertEqual(service.workspace,old.resolve())
