import os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import gitstate


def git(root, *args):
    subprocess.check_call(["git"] + list(args), cwd=root,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def make_repo():
    """A throwaway repo in a temp dir. The plugin's own repo is never touched."""
    root = tempfile.mkdtemp()
    git(root, "init")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    return root


def commit(root, name, message):
    with open(os.path.join(root, name), "w") as fh:
        fh.write("x")
    git(root, "add", name)
    git(root, "commit", "-m", message)


class TestIsRepo(unittest.TestCase):
    def test_true_inside_a_repo(self):
        self.assertTrue(gitstate.is_repo(make_repo()))

    def test_false_outside_a_repo(self):
        self.assertFalse(gitstate.is_repo(tempfile.mkdtemp()))


class TestPhaseCommits(unittest.TestCase):
    def test_empty_when_no_tagged_commits(self):
        root = make_repo()
        commit(root, "a.txt", "just a normal commit")
        self.assertEqual(gitstate.phase_commits(root), {})

    def test_maps_phase_number_to_sha(self):
        root = make_repo()
        commit(root, "a.txt", "[phase-1] Event log")
        commit(root, "b.txt", "[phase-2] Verdict parsing")
        found = gitstate.phase_commits(root)
        self.assertEqual(sorted(found.keys()), [1, 2])
        self.assertTrue(all(len(sha) >= 7 for sha in found.values()))

    def test_tag_must_be_at_the_start_of_the_subject(self):
        root = make_repo()
        commit(root, "a.txt", "mentions [phase-9] in passing")
        self.assertEqual(gitstate.phase_commits(root), {})

    def test_returns_none_outside_a_repo(self):
        self.assertIsNone(gitstate.phase_commits(tempfile.mkdtemp()))


class TestDirtyPaths(unittest.TestCase):
    def test_empty_list_when_clean(self):
        root = make_repo()
        commit(root, "a.txt", "init")
        self.assertEqual(gitstate.dirty_paths(root), [])

    def test_lists_modified_and_untracked(self):
        root = make_repo()
        commit(root, "a.txt", "init")
        with open(os.path.join(root, "a.txt"), "w") as fh:
            fh.write("changed")
        with open(os.path.join(root, "b.txt"), "w") as fh:
            fh.write("new")
        self.assertEqual(sorted(gitstate.dirty_paths(root)), ["a.txt", "b.txt"])

    def test_returns_none_outside_a_repo(self):
        self.assertIsNone(gitstate.dirty_paths(tempfile.mkdtemp()))


if __name__ == "__main__":
    unittest.main()


class TestFreshRepo(unittest.TestCase):
    """A repo with zero commits has no `git log`, so phase state is unknowable.
    The gate must go inert rather than treat 'no commits' as 'nothing shipped'."""

    def test_phase_commits_is_none_before_the_first_commit(self):
        self.assertIsNone(gitstate.phase_commits(make_repo()))

    def test_is_repo_is_still_true_before_the_first_commit(self):
        self.assertTrue(gitstate.is_repo(make_repo()))

    def test_dirty_paths_still_works_before_the_first_commit(self):
        root = make_repo()
        with open(os.path.join(root, "a.txt"), "w") as fh:
            fh.write("x")
        self.assertEqual(gitstate.dirty_paths(root), ["a.txt"])


class TestUntrackedDirectories(unittest.TestCase):
    """`git status --porcelain` collapses an untracked directory to `dir/`,
    which hides every file inside it from phase classification. Found by
    dogfooding: the lump check saw `scripts/`, never `scripts/ccalib/x.py`."""

    def test_files_inside_an_untracked_directory_are_listed_individually(self):
        root = make_repo()
        commit(root, "seed.txt", "seed")
        os.makedirs(os.path.join(root, "pkg", "sub"))
        for name in ("pkg/a.py", "pkg/sub/b.py"):
            with open(os.path.join(root, name), "w") as fh:
                fh.write("x")
        paths = gitstate.dirty_paths(root)
        self.assertIn("pkg/a.py", paths)
        self.assertIn("pkg/sub/b.py", paths)
        self.assertNotIn("pkg/", paths)
