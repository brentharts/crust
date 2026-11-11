"""The driver builds a Goose program end to end: `shivyc.main prog.goose -o prog`."""
import glob
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, "examples", "goose")


def build(src, exe, *extra):
    return subprocess.run([sys.executable, "-m", "shivyc.main", src, "-o", exe, *extra],
                          cwd=ROOT, capture_output=True, text=True)


class TestDriver(unittest.TestCase):
    def test_examples_build_and_run(self):
        for name in ("scalars", "flow", "nested", "frozen"):
            with self.subTest(program=name), tempfile.TemporaryDirectory() as d:
                exe = os.path.join(d, name)
                p = build(os.path.join(EX, name + ".goose"), exe)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                r = subprocess.run([exe], capture_output=True, text=True, timeout=60)
                with open(os.path.join(EX, name + ".out"), encoding="utf-8") as f:
                    self.assertEqual(r.stdout, f.read())

    def test_compile_only_keeps_an_object(self):
        with tempfile.TemporaryDirectory() as d:
            obj = os.path.join(d, "flow.o")
            p = build(os.path.join(EX, "flow.goose"), obj, "-c")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertTrue(os.path.getsize(obj) > 0)

    def test_a_refusal_is_a_diagnostic_with_the_goose_line(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "bad.goose")
            with open(src, "w") as f:
                f.write("fn main() {\n    defer print(1);\n}\n")
            p = build(src, os.path.join(d, "bad"))
            self.assertNotEqual(p.returncode, 0)
            text = p.stdout + p.stderr
            self.assertIn("bad.goose:2", text)
            self.assertIn("'defer' is not in the Crust Goose subset yet", text)

    def test_goose_and_c_link_together(self):
        with tempfile.TemporaryDirectory() as d:
            g = os.path.join(d, "g.goose")
            with open(g, "w") as f:
                f.write("fn main() { print(40 + 2); }\n")
            c = os.path.join(d, "extra.c")
            with open(c, "w") as f:
                f.write("int extra_value(void) { return 7; }\n")
            exe = os.path.join(d, "both")
            p = build(g, exe + ".tmp")           # the C file alone has no main: just check it compiles with it
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            p2 = subprocess.run([sys.executable, "-m", "shivyc.main", g, c, "-o", exe],
                                cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(p2.returncode, 0, p2.stdout + p2.stderr)
            r = subprocess.run([exe], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.stdout, "42\n")


if __name__ == "__main__":
    unittest.main()
