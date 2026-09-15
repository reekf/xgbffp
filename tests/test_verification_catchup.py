"""Exercise catch-up ordering and eligibility with isolated publisher stubs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

SOURCE = Path(__file__).resolve().parent
if SOURCE.name == 'tests':
    SOURCE = SOURCE.parent

class CatchupTest(unittest.TestCase):
    def run_case(self, day, *, sync_fail=False, complete=False, future=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = 'publish_missing_verification_outputs.sh' if day == 1 else 'publish_missing_day2_verification_outputs.sh'
            shutil.copy(SOURCE / script, root / script)
            valid = datetime.now(timezone.utc) - timedelta(days=10)
            date = valid.strftime('%Y%m%d')
            archive = ('docs/archive/' if day == 1 else 'docs/day2/archive/') + date
            fixture = root / 'incoming' / archive
            fixture.mkdir(parents=True)
            end = datetime.now(timezone.utc) + timedelta(days=1) if future else valid + timedelta(days=1)
            status = {'forecast_day': day, 'valid_end_utc': end.isoformat()}
            payload = {'schema_version': 5, 'forecast_day': day, 'layers': dict.fromkeys(['ml_r40','ml_r60','ml_r75','ml_r100','ml_mean','wpc','pp']), 'verification_truths': dict.fromkeys(['practically_perfect','ufvs_40km'])}
            if complete:
                status.update(verification_available=True, verification_plot='verification.png')
                (fixture / 'verification.png').write_text('image')
            (fixture / 'latest.png').write_text('image')
            (fixture / 'status.json').write_text(json.dumps(status))
            (fixture / 'map.json').write_text(json.dumps(payload))
            (root / 'publisher_git.sh').write_text('xgbffp_acquire_publish_lock() { :; }\nxgbffp_sync_main() { echo sync >> events; '+('return 1;' if sync_fail else 'cp -r incoming/docs .;')+' }\n')
            publisher = root / ('publish_verification_output.sh' if day == 1 else 'publish_day2_verification_output.sh')
            publisher.write_text('#!/bin/bash\necho "publish $1" >> events\n')
            publisher.chmod(0o755)
            env = {**os.environ, 'REPO_DIR': tmp, 'SOURCE_DIR': tmp, 'SITE_REPO': tmp, 'PUBLISH_GIT':'1', 'VERIFY_CATCHUP_LOCK_FILE':tmp+'/lock1', 'DAY2_VERIFY_CATCHUP_LOCK_FILE':tmp+'/lock2'}
            env.pop('VERIFY_LOOKBACK_DAYS', None)
            result = subprocess.run(['bash', str(root/script)], cwd=root, env=env, capture_output=True, text=True)
            events = (root/'events').read_text().splitlines()
            self.assertEqual(result.returncode, 1 if sync_fail else 0, result.stderr)
            expected = ['sync']
            if not (sync_fail or complete or future):
                issue = valid if day == 1 else valid-timedelta(days=1)
                expected.append('publish '+issue.strftime('%Y%m%d'))
            self.assertEqual(events, expected)

    def test_sync_discovers_older_archives(self):
        for day in [1,2]:
            with self.subTest(day=day): self.run_case(day)
    def test_failed_sync_stops_scan(self):
        for day in [1,2]:
            with self.subTest(day=day): self.run_case(day,sync_fail=True)
    def test_completed_archives_are_skipped(self):
        for day in [1,2]:
            with self.subTest(day=day): self.run_case(day,complete=True)
    def test_unfinished_day2_period_is_skipped(self):
        self.run_case(2,future=True)

if __name__ == '__main__': unittest.main()
