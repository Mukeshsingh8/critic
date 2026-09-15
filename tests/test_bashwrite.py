import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import bashwrite


class TestRedirection(unittest.TestCase):
    def test_heredoc_write(self):
        self.assertEqual(bashwrite.write_targets("cat > cli.py <<'EOF'"), ["cli.py"])

    def test_redirect_with_no_space(self):
        self.assertEqual(bashwrite.write_targets("cat >cli.py"), ["cli.py"])

    def test_append_redirect(self):
        self.assertEqual(bashwrite.write_targets("echo x >> cli.py"), ["cli.py"])

    def test_numbered_fd_redirect(self):
        self.assertEqual(bashwrite.write_targets("cmd 1> cli.py"), ["cli.py"])

    def test_redirect_after_cd_and_and(self):
        self.assertEqual(bashwrite.write_targets("cd sub && cat > cli.py <<'EOF'"), ["cli.py"])

    def test_multiple_targets(self):
        self.assertEqual(sorted(bashwrite.write_targets("echo a > x.py; echo b > y.py")),
                         ["x.py", "y.py"])


class TestOtherWriters(unittest.TestCase):
    def test_tee(self):
        self.assertEqual(bashwrite.write_targets("echo x | tee cli.py"), ["cli.py"])

    def test_tee_append_flag(self):
        self.assertEqual(bashwrite.write_targets("echo x | tee -a cli.py"), ["cli.py"])

    def test_cp_destination_only(self):
        self.assertEqual(bashwrite.write_targets("cp a.py cli.py"), ["cli.py"])

    def test_mv_destination_only(self):
        self.assertEqual(bashwrite.write_targets("mv a.py cli.py"), ["cli.py"])

    def test_sed_in_place(self):
        self.assertIn("cli.py", bashwrite.write_targets("sed -i '' s/x/y/ cli.py"))

    def test_sed_without_i_is_not_a_write(self):
        self.assertEqual(bashwrite.write_targets("sed s/x/y/ cli.py"), [])

    def test_touch(self):
        self.assertEqual(bashwrite.write_targets("touch cli.py"), ["cli.py"])

    def test_dd_of(self):
        self.assertEqual(bashwrite.write_targets("dd if=/dev/zero of=cli.py"), ["cli.py"])

    def test_truncate(self):
        self.assertIn("cli.py", bashwrite.write_targets("truncate -s 0 cli.py"))


class TestNoFalsePositives(unittest.TestCase):
    """The fence is the most dangerous code in the plugin. These matter most."""

    def test_reading_a_file_is_not_a_write(self):
        for command in ["cat cli.py", "grep foo cli.py", "head -5 cli.py",
                        "python3 -m unittest cli.py", "wc -l cli.py",
                        "git diff cli.py", "ls cli.py"]:
            self.assertEqual(bashwrite.write_targets(command), [], command)

    def test_redirect_inside_a_quoted_string_is_not_a_redirect(self):
        self.assertEqual(bashwrite.write_targets('echo "a > cli.py"'), [])

    def test_comparison_operator_is_not_a_redirect(self):
        self.assertEqual(bashwrite.write_targets("python3 -c 'print(1 > 2)'"), [])

    def test_source_of_a_copy_is_not_a_target(self):
        self.assertNotIn("a.py", bashwrite.write_targets("cp a.py out.py"))

    def test_process_substitution_is_ignored(self):
        self.assertEqual(bashwrite.write_targets("diff <(cat a.py) <(cat cli.py)"), [])

    def test_unparseable_command_returns_empty(self):
        self.assertEqual(bashwrite.write_targets("echo 'unterminated"), [])

    def test_empty_command_returns_empty(self):
        self.assertEqual(bashwrite.write_targets(""), [])

    def test_interpreter_writes_are_now_covered(self):
        # Superseded: these are detected statically via ast, without executing.
        self.assertEqual(bashwrite.write_targets("python3 -c \"open('cli.py','w')\""),
                         ["cli.py"])


if __name__ == "__main__":
    unittest.main()


class TestPythonInlineWrites(unittest.TestCase):
    def test_open_write_mode(self):
        self.assertEqual(bashwrite.write_targets("python3 -c \"open('cli.py','w')\""),
                         ["cli.py"])

    def test_open_append_mode(self):
        self.assertEqual(bashwrite.write_targets("python3 -c \"open('cli.py','a')\""),
                         ["cli.py"])

    def test_open_exclusive_and_update_modes(self):
        for mode in ("x", "w+", "r+", "wb", "ab"):
            self.assertEqual(
                bashwrite.write_targets("python3 -c \"open('cli.py','%s')\"" % mode),
                ["cli.py"], mode)

    def test_open_with_keyword_mode(self):
        self.assertEqual(bashwrite.write_targets("python3 -c \"open('cli.py', mode='w')\""),
                         ["cli.py"])

    def test_pathlib_write_text(self):
        cmd = "python3 -c \"import pathlib; pathlib.Path('cli.py').write_text('x')\""
        self.assertEqual(bashwrite.write_targets(cmd), ["cli.py"])

    def test_pathlib_write_bytes(self):
        cmd = "python3 -c \"from pathlib import Path; Path('cli.py').write_bytes(b'x')\""
        self.assertEqual(bashwrite.write_targets(cmd), ["cli.py"])

    def test_shutil_copy_destination(self):
        cmd = "python3 -c \"import shutil; shutil.copy('a.py','cli.py')\""
        self.assertEqual(bashwrite.write_targets(cmd), ["cli.py"])

    def test_os_remove_is_a_write(self):
        self.assertEqual(bashwrite.write_targets("python3 -c \"import os; os.remove('cli.py')\""),
                         ["cli.py"])

    def test_os_rename_covers_both_ends(self):
        cmd = "python3 -c \"import os; os.rename('a.py','cli.py')\""
        self.assertIn("cli.py", bashwrite.write_targets(cmd))

    def test_plain_python_binary_name(self):
        self.assertEqual(bashwrite.write_targets("python -c \"open('cli.py','w')\""),
                         ["cli.py"])


class TestPythonHeredocWrites(unittest.TestCase):
    """`python3 - <<'PY'` is the most common way an agent runs a script."""

    def test_quoted_heredoc_payload(self):
        cmd = "python3 - <<'PY'\nopen('cli.py','w').write('x')\nPY"
        self.assertEqual(bashwrite.write_targets(cmd), ["cli.py"])

    def test_unquoted_heredoc_payload(self):
        cmd = "python3 - <<PY\nimport pathlib\npathlib.Path('cli.py').write_text('x')\nPY"
        self.assertEqual(bashwrite.write_targets(cmd), ["cli.py"])

    def test_heredoc_read_only_payload_is_not_a_write(self):
        cmd = "python3 - <<'PY'\nprint(open('cli.py').read())\nPY"
        self.assertEqual(bashwrite.write_targets(cmd), [])


class TestNestedShell(unittest.TestCase):
    def test_bash_dash_c_redirect_is_found(self):
        self.assertEqual(bashwrite.write_targets("bash -c \"cat > cli.py\""), ["cli.py"])

    def test_sh_dash_c_tee_is_found(self):
        self.assertEqual(bashwrite.write_targets("sh -c 'echo x | tee cli.py'"), ["cli.py"])

    def test_recursion_is_depth_limited(self):
        deep = "bash -c \"bash -c \\\"bash -c 'cat > cli.py'\\\"\""
        bashwrite.write_targets(deep)   # must terminate, not recurse forever


class TestNodeWrites(unittest.TestCase):
    def test_write_file_sync(self):
        cmd = "node -e \"require('fs').writeFileSync('cli.py','x')\""
        self.assertEqual(bashwrite.write_targets(cmd), ["cli.py"])

    def test_append_file_sync(self):
        cmd = 'node -e "fs.appendFileSync(\'cli.py\', \'x\')"'
        self.assertEqual(bashwrite.write_targets(cmd), ["cli.py"])

    def test_read_file_sync_is_not_a_write(self):
        cmd = "node -e \"require('fs').readFileSync('cli.py')\""
        self.assertEqual(bashwrite.write_targets(cmd), [])


class TestInterpreterNoFalsePositives(unittest.TestCase):
    """Still the thing that matters most: a read must never be denied."""

    def test_open_default_mode_is_a_read(self):
        self.assertEqual(bashwrite.write_targets("python3 -c \"open('cli.py')\""), [])

    def test_open_explicit_read_modes(self):
        for mode in ("r", "rb"):
            self.assertEqual(
                bashwrite.write_targets("python3 -c \"open('cli.py','%s')\"" % mode),
                [], mode)

    def test_merely_naming_a_file_is_not_a_write(self):
        for cmd in ["python3 -c \"print('cli.py')\"",
                    "python3 -c \"x = 'cli.py'\"",
                    "python3 -c \"import ast; ast.parse(open('cli.py').read())\""]:
            self.assertEqual(bashwrite.write_targets(cmd), [], cmd)

    def test_running_a_module_is_not_a_write(self):
        self.assertEqual(bashwrite.write_targets("python3 -m unittest discover tests"), [])

    def test_running_a_script_file_is_not_a_write(self):
        self.assertEqual(bashwrite.write_targets("python3 scripts/cca_eval.py"), [])

    def test_syntactically_invalid_payload_returns_empty(self):
        self.assertEqual(bashwrite.write_targets("python3 -c \"open('cli.py',,,\""), [])

    def test_dynamic_path_is_undetectable_and_must_not_guess(self):
        # A fundamental limit of static analysis. It must fail open, not invent a target.
        cmd = "python3 -c \"n = 'cli' + '.py'; open(n,'w')\""
        self.assertEqual(bashwrite.write_targets(cmd), [])
