"""Apply Git-only cleanup to isolated fixture repositories, never the user's index."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('git'), 'Git required for cleanup fixture')
class RepositoryCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='minecraft cleanup fixture ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = os.environ.copy()
        for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'):
            self.env.pop(key, None)
        self.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        (self.root / 'scripts').mkdir()
        shutil.copy2(ROOT / 'scripts/untrack-runtime.sh', self.root / 'scripts/untrack-runtime.sh')
        shutil.copy2(ROOT / '.gitignore', self.root / '.gitignore')
        self.files = {'server/world/region/fixture.mca': b'private world bytes',
                      'server/logs/fixture.log': b'private gameplay log',
                      'server/libraries/fixture.jar': b'generated library',
                      'server/server.properties': b'local configuration'}
        for path, content in self.files.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        self.git('init', '--quiet')
        self.git('add', '-f', '.', '--')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'core.hooksPath=/dev/null', 'commit', '--quiet', '-m', 'fixture')

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, env=self.env, text=True,
                              capture_output=True, check=True, timeout=10).stdout

    def script(self, *args):
        return subprocess.run(['bash', 'scripts/untrack-runtime.sh', *args], cwd=self.root,
                              env=self.env, text=True, capture_output=True, timeout=10)

    def test_default_is_dry_run_and_apply_preserves_all_local_bytes(self):
        before = self.git('ls-files')
        dry = self.script()
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn('Dry run only', dry.stdout)
        self.assertEqual(self.git('ls-files'), before)
        applied = self.script('--apply')
        self.assertEqual(applied.returncode, 0, applied.stderr)
        tracked = self.git('ls-files')
        self.assertIn('server/server.properties', tracked)
        self.assertNotIn('server/world/region/fixture.mca', tracked)
        self.assertNotIn('server/logs/fixture.log', tracked)
        self.assertNotIn('server/libraries/fixture.jar', tracked)
        for path, content in self.files.items():
            self.assertEqual((self.root / path).read_bytes(), content)
        repeat = self.script('--apply')
        self.assertEqual(repeat.returncode, 0, repeat.stderr)
        self.assertIn('No generated runtime files remain tracked', repeat.stdout)

    def test_staged_runtime_changes_are_not_discarded(self):
        path = self.root / 'server/world/region/fixture.mca'
        path.write_bytes(b'new staged world snapshot')
        self.git('add', '-f', 'server/world/region/fixture.mca')
        before = self.git('diff', '--cached')
        result = self.script('--apply')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('staged changes', result.stderr)
        self.assertEqual(self.git('diff', '--cached'), before)
        self.assertEqual(path.read_bytes(), b'new staged world snapshot')

    def test_unrelated_staged_changes_and_modified_world_stay_intact(self):
        configuration = self.root / 'server/server.properties'
        configuration.write_bytes(b'changed configuration')
        self.git('add', 'server/server.properties')
        world = self.root / 'server/world/region/fixture.mca'
        world.write_bytes(b'modified local world')
        staged = self.git('diff', '--cached', '--', 'server/server.properties')
        result = self.script('--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git('diff', '--cached', '--', 'server/server.properties'), staged)
        self.assertEqual(world.read_bytes(), b'modified local world')


if __name__ == '__main__':
    unittest.main()
