import json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
HOOK = os.path.join(ROOT, "scripts", "phase_hook.py")

PLAN = """### Task 1: One

**Files:**
- Create: `a.py`

- [ ] **Step 1: x**

### Task 2: Two

**Files:**
- Create: `b.py`

- [ ] **Step 1: x**

### Task 3: Three

**Files:**
- Create: `c.py`

- [ ] **Step 1: x**
"""


def git(root, *args):
    subprocess.check_call(["git"] + list(args), cwd=root,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def project(plan=PLAN, shipped=()):
    root = tempfile.mkdtemp()
    plans = os.path.join(root, "docs", "superpowers", "plans")
    os.makedirs(plans)
    with open(os.path.join(plans, "p.md"), "w") as fh:
        fh.write(plan)
    git(root, "init")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    git(root, "add", "-A")
    git(root, "commit", "-m", "seed")
    for number in shipped:
        name = "shipped%d.txt" % number
        with open(os.path.join(root, name), "w") as fh:
            fh.write("x")
        git(root, "add", name)
        git(root, "commit", "-m", "[phase-%d] done" % number)
    return root


def run(root, path, extra_env=None):
    event = {"tool_name": "Edit", "cwd": root, "tool_input": {"file_path": path}}
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = root
    if extra_env:
        env.update(extra_env)
    proc = subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, cwd=root, universal_newlines=True)
    out, err = proc.communicate(json.dumps(event), timeout=60)
    return proc.returncode, out, err


class TestFence(unittest.TestCase):
    def test_current_phase_file_is_allowed(self):
        self.assertEqual(run(project(shipped=[1]), "b.py")[0], 0)

    def test_later_phase_file_is_denied(self):
        code, _out, err = run(project(shipped=[1]), "c.py")
        self.assertEqual(code, 2)
        self.assertIn("phase 3", err.lower())

    def test_earlier_phase_file_is_allowed(self):
        self.assertEqual(run(project(shipped=[1]), "a.py")[0], 0)

    def test_unlisted_file_is_allowed(self):
        self.assertEqual(run(project(shipped=[1]), "zzz.py")[0], 0)

    def test_denial_names_the_current_phase_too(self):
        _code, _out, err = run(project(shipped=[1]), "c.py")
        self.assertIn("Two", err)


class TestFailsOpen(unittest.TestCase):
    def test_recursion_firewall_allows(self):
        self.assertEqual(run(project(shipped=[1]), "c.py", {"CCA_INNER": "1"})[0], 0)

    def test_no_plan_allows(self):
        root = tempfile.mkdtemp()
        git(root, "init")
        self.assertEqual(run(root, "c.py")[0], 0)

    def test_not_a_git_repo_allows(self):
        root = tempfile.mkdtemp()
        plans = os.path.join(root, "docs", "superpowers", "plans")
        os.makedirs(plans)
        with open(os.path.join(plans, "p.md"), "w") as fh:
            fh.write(PLAN)
        self.assertEqual(run(root, "c.py")[0], 0)

    def test_unparseable_plan_allows(self):
        self.assertEqual(run(project(plan="this document has no task headings"), "c.py")[0], 0)

    def test_disabled_in_config_allows(self):
        root = project(shipped=[1])
        os.makedirs(os.path.join(root, ".cca"))
        with open(os.path.join(root, ".cca", "config.json"), "w") as fh:
            json.dump({"phases": {"enabled": False}}, fh)
        self.assertEqual(run(root, "c.py")[0], 0)

    def test_malformed_stdin_allows(self):
        root = project(shipped=[1])
        proc = subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=root, universal_newlines=True)
        proc.communicate("not json", timeout=30)
        self.assertEqual(proc.returncode, 0)

    def test_all_phases_shipped_allows_everything(self):
        self.assertEqual(run(project(shipped=[1, 2, 3]), "c.py")[0], 0)

    def test_non_edit_tool_allows(self):
        root = project(shipped=[1])
        event = {"tool_name": "Read", "cwd": root, "tool_input": {"file_path": "c.py"}}
        proc = subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=root, universal_newlines=True)
        proc.communicate(json.dumps(event), timeout=30)
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()


def run_bash(root, command, extra_env=None):
    event = {"tool_name": "Bash", "cwd": root, "tool_input": {"command": command}}
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = root
    if extra_env:
        env.update(extra_env)
    proc = subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, cwd=root, universal_newlines=True)
    out, err = proc.communicate(json.dumps(event), timeout=60)
    return proc.returncode, out, err


class TestBashFence(unittest.TestCase):
    """The heredoc hole: `cat > file <<EOF` used to bypass the fence entirely."""

    def test_heredoc_write_to_a_later_phase_is_denied(self):
        code, _out, err = run_bash(project(shipped=[1]), "cat > c.py <<'EOF'\nx\nEOF")
        self.assertEqual(code, 2)
        self.assertIn("phase 3", err.lower())

    def test_redirect_to_a_later_phase_is_denied(self):
        self.assertEqual(run_bash(project(shipped=[1]), "echo x > c.py")[0], 2)

    def test_append_to_a_later_phase_is_denied(self):
        self.assertEqual(run_bash(project(shipped=[1]), "echo x >> c.py")[0], 2)

    def test_tee_to_a_later_phase_is_denied(self):
        self.assertEqual(run_bash(project(shipped=[1]), "echo x | tee c.py")[0], 2)

    def test_cp_onto_a_later_phase_is_denied(self):
        self.assertEqual(run_bash(project(shipped=[1]), "cp a.py c.py")[0], 2)

    def test_write_to_the_current_phase_is_allowed(self):
        self.assertEqual(run_bash(project(shipped=[1]), "cat > b.py <<'EOF'\nx\nEOF")[0], 0)

    def test_write_to_an_earlier_phase_is_allowed(self):
        self.assertEqual(run_bash(project(shipped=[1]), "echo x > a.py")[0], 0)

    def test_write_to_an_unlisted_file_is_allowed(self):
        self.assertEqual(run_bash(project(shipped=[1]), "echo x > scratch.txt")[0], 0)

    def test_reading_a_later_phase_file_is_allowed(self):
        for command in ["cat c.py", "grep foo c.py", "python3 -m unittest c.py",
                        "git diff c.py", "ls -la c.py"]:
            self.assertEqual(run_bash(project(shipped=[1]), command)[0], 0, command)

    def test_quoted_redirect_is_not_a_write(self):
        self.assertEqual(run_bash(project(shipped=[1]), 'echo "a > c.py"')[0], 0)

    def test_running_the_test_suite_is_never_blocked(self):
        self.assertEqual(
            run_bash(project(shipped=[1]), "python3 -m unittest discover tests")[0], 0)

    def test_bash_with_no_command_is_allowed(self):
        root = project(shipped=[1])
        event = {"tool_name": "Bash", "cwd": root, "tool_input": {}}
        proc = subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=root, universal_newlines=True)
        proc.communicate(json.dumps(event), timeout=30)
        self.assertEqual(proc.returncode, 0)

    def test_recursion_firewall_still_allows_bash(self):
        self.assertEqual(
            run_bash(project(shipped=[1]), "echo x > c.py", {"CCA_INNER": "1"})[0], 0)


NB_PLAN = """### Task 1: Ingest

**Files:**
- Create: `notebooks/ingest.ipynb`

- [ ] **Step 1: x**

### Task 2: Model

**Files:**
- Create: `notebooks/model.ipynb`

- [ ] **Step 1: x**
"""


def run_notebook(root, notebook_path, extra_env=None):
    event = {"tool_name": "NotebookEdit", "cwd": root,
             "tool_input": {"notebook_path": notebook_path, "new_source": "print(1)"}}
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = root
    if extra_env:
        env.update(extra_env)
    proc = subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, cwd=root, universal_newlines=True)
    out, err = proc.communicate(json.dumps(event), timeout=60)
    return proc.returncode, out, err


class TestNotebookFence(unittest.TestCase):
    """NotebookEdit takes `notebook_path`, not `file_path`, and the schema
    requires it to be absolute -- both of which the fence must handle."""

    def test_later_phase_notebook_is_denied(self):
        root = project(plan=NB_PLAN)          # current = phase 1
        code, _out, err = run_notebook(root, os.path.join(root, "notebooks/model.ipynb"))
        self.assertEqual(code, 2)
        self.assertIn("phase 2", err.lower())

    def test_current_phase_notebook_is_allowed(self):
        root = project(plan=NB_PLAN)
        self.assertEqual(
            run_notebook(root, os.path.join(root, "notebooks/ingest.ipynb"))[0], 0)

    def test_earlier_phase_notebook_is_allowed(self):
        root = project(plan=NB_PLAN, shipped=[1])   # current = phase 2
        self.assertEqual(
            run_notebook(root, os.path.join(root, "notebooks/ingest.ipynb"))[0], 0)

    def test_unlisted_notebook_is_allowed(self):
        root = project(plan=NB_PLAN)
        self.assertEqual(
            run_notebook(root, os.path.join(root, "notebooks/scratch.ipynb"))[0], 0)

    def test_relative_path_is_also_handled(self):
        root = project(plan=NB_PLAN)
        self.assertEqual(run_notebook(root, "notebooks/model.ipynb")[0], 2)

    def test_missing_notebook_path_is_allowed(self):
        root = project(plan=NB_PLAN)
        event = {"tool_name": "NotebookEdit", "cwd": root, "tool_input": {}}
        proc = subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=root, universal_newlines=True)
        proc.communicate(json.dumps(event), timeout=30)
        self.assertEqual(proc.returncode, 0)

    def test_recursion_firewall_still_allows_notebooks(self):
        root = project(plan=NB_PLAN)
        self.assertEqual(
            run_notebook(root, os.path.join(root, "notebooks/model.ipynb"),
                         {"CCA_INNER": "1"})[0], 0)
