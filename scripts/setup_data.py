"""Install and verify the fixed course data from its release or an offline ZIP."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.datasets import FixedDatasetError, ensure_fixed_dataset, inspect_fixed_dataset


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, help='Offline fixed-course-data-v1.zip; the pinned SHA256 is still required')
    parser.add_argument('--project-root', type=Path, default=ROOT, help='Repository root (defaults to this script\'s repository)')
    parser.add_argument('--check', action='store_true', help='Only inspect local data; never download or write')
    args = parser.parse_args(argv)
    if args.check and args.archive:
        parser.error('--check cannot be combined with --archive')
    last = {'stage': None, 'percent': -1}

    def progress(event):
        stage = event['stage']
        if stage == 'download':
            total = event.get('total_bytes') or 0
            percent = int(event['downloaded_bytes'] * 100 / total) if total else 0
            if last['stage'] != stage or percent >= last['percent'] + 10:
                print(f'Downloading fixed data: {percent}%', file=sys.stderr)
                last.update(stage=stage, percent=percent)
        elif last['stage'] != stage:
            print({'verify_archive': 'Verifying archive SHA256…', 'install': 'Installing frozen resources…', 'ready': 'Fixed course data is ready.'}.get(stage, stage), file=sys.stderr)
            last['stage'] = stage
    try:
        result = inspect_fixed_dataset(args.project_root) if args.check else ensure_fixed_dataset(args.project_root, archive_path=args.archive, progress=progress)
    except (FixedDatasetError, OSError) as error:
        print('Fixed data setup failed: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
