import json
import tempfile
import unittest
from pathlib import Path

class ReviewedPackageTests(unittest.TestCase):
    def test_packaging_requires_explicit_reviewed_assets(self):
        from scripts.package_reviewed_demo import package
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            with self.assertRaisesRegex(ValueError,'review'):
                package(root,root/'output',{'reviews':[],'structures':[]})
            self.assertFalse((root/'output').exists())

    def test_ai_review_never_qualifies_formal_materials(self):
        from app.resources import ResourceRegistry
        import hashlib
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'input').write_text('x');sha=hashlib.sha256(b'x').hexdigest()
            (root/'resources.json').write_text(json.dumps(dict(schema_version=1,resource_set_id='formal',purpose='formal',resources=[dict(kind='input',path='input',sha256=sha)])))
            registry=ResourceRegistry(root/'w');registry.register(root)
            with self.assertRaisesRegex(ValueError,'AI'):
                registry.import_reviews('formal',{'reviews':[dict(asset_sha256=sha,approved=True,qa_exposed=False,reviewer='Codex',reviewer_kind='ai',reviewed_at='2026-10-25T00:00:00Z')]})
