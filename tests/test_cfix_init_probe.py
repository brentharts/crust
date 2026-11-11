"""Regressions found while building the Goose subset (see GOOSE.md): each program in
tests/fixtures/cfix/ prints what gcc printed.

  init_zero, init_elision, init_strings  brace elision (`struct { long d[4]; long n; } x = {0}`,
                                         `long a[2][3] = {1, 2, 3, 4}`) and a string literal
                                         initializing a char array member or element
  or_probe, or_probe_min                 `if (K || p[k] ...)` with a constant left operand: the
                                         probe of the right operand cached values that only the
                                         throwaway IL defined, and the program crashed
  libc_decls                             strtod, strtof, strtoll, strtoull, atof, fmaf, exp2f ...
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "cfix")


class TestCFix(unittest.TestCase):
    def check(self, name):
        d = tempfile.mkdtemp(prefix="cfix_")
        exe = os.path.join(d, name)
        src = os.path.join(d, name + ".c")                  # build beside the exe, not in the fixtures
        with open(os.path.join(FIX, name + ".c")) as f, open(src, "w") as g:
            g.write(f.read())
        p = subprocess.run([sys.executable, "-m", "shivyc.main", src, "-o", exe, "-lm"],
                           cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        r = subprocess.run([exe], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, "crashed: %d" % r.returncode)
        with open(os.path.join(FIX, name + ".out")) as f:
            self.assertEqual(r.stdout, f.read())

    def test_zero_initializer_of_nested_aggregates(self):
        self.check("init_zero")

    def test_brace_elision(self):
        self.check("init_elision")

    def test_string_literal_in_a_member_initializer(self):
        self.check("init_strings")

    def test_constant_or_pointer_compare_with_index(self):
        self.check("or_probe_min")

    def test_constant_or_pointer_compare_after_member_store(self):
        self.check("or_probe")

    def test_libc_declarations(self):
        self.check("libc_decls")


if __name__ == "__main__":
    unittest.main()
