"""Goose in Crust: the lowering (`tools/goose2c.py`), see GOOSE.md.

Each program in `examples/goose/` is translated, compiled and run -- by gcc
and by shivyc, the Crust C compiler -- and its output must be the file beside
it, `NAME.out`, which is what the real Goose compiler prints for it.  The
refusal tests pin what the subset does not have yet, with the line.
"""
import glob
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import tools.goose as G                                    # noqa: E402
import tools.goose2c as C                                  # noqa: E402

EX = os.path.join(ROOT, "examples", "goose")


def _run(exe, stdin=None):
    # in the program's own directory: some samples write files
    return subprocess.run([exe], capture_output=True, text=True, timeout=120, cwd=os.path.dirname(exe),
                          input=stdin)


def build_gcc(c_path, exe):
    subprocess.run(["gcc", "-w", "-O1", "-o", exe, c_path, "-lm"], check=True)


def build_shivyc(c_path, exe):
    subprocess.run([sys.executable, "-m", "shivyc.main", c_path, "-o", exe], check=True,
                   cwd=ROOT, capture_output=True)


def program(path):
    with open(path, encoding="utf-8") as f:
        return C.translate(f.read(), path)


class TestExamples(unittest.TestCase):
    def check(self, build):
        for src in sorted(glob.glob(os.path.join(EX, "*.goose"))):
            name = os.path.basename(src)[:-6]
            with self.subTest(program=name):
                with open(os.path.join(EX, name + ".out"), encoding="utf-8") as f:
                    want = f.read()
                with tempfile.TemporaryDirectory() as d:
                    c = os.path.join(d, name + ".c")
                    with open(c, "w") as f:
                        f.write(program(src))
                    exe = os.path.join(d, name)
                    build(c, exe)
                    stdin = None
                    if name in STDIN:
                        with open(os.path.join(base, STDIN[name]), encoding="utf-8") as f:
                            stdin = f.read()
                    p = _run(exe, stdin)
                    self.assertEqual(p.returncode, 0, p.stderr)
                    self.assertEqual(p.stdout, want)

    def test_gcc(self):
        self.check(build_gcc)

    def test_shivyc(self):
        self.check(build_shivyc)


def goose_samples():
    """The `samples/` directory of a Goose checkout (GOOSE_ROOT or a sibling `goose`)."""
    for r in (os.environ.get("GOOSE_ROOT"), os.path.join(os.path.dirname(ROOT), "goose")):
        if r and os.path.isdir(os.path.join(r, "samples", "expected")):
            return os.path.join(r, "samples")
    return None


# Goose's own samples that translate today, with the output file Goose ships for them.
SAMPLES = {"19_vm": "vm",
    "04_errors": "errors", "20_life": "life", "11_maze": "maze", "07_sieve": "sieve", "10_sudoku": "sudoku",
           "09_sorting": "sorting", "01_tour": "tour", "03_strings": "strings",
           "21_raytrace": "raytrace", "02_memory": "memory", "22_image_filters": "image_filters",
           "15_word_freq": "word_freq", "17_calc": "calc", "08_bignum": "bignum", "06_functions": "functions",
           "12_huffman": "huffman", "14_bst": "bst"}
STDIN = {"17_calc": "data/calc.stdin"}                  # samples that read standard input


@unittest.skipIf(goose_samples() is None, "no Goose checkout (set GOOSE_ROOT)")
class TestGooseSamples(unittest.TestCase):
    def check(self, build):
        base = goose_samples()
        for name, out in sorted(SAMPLES.items()):
            with self.subTest(sample=name):
                with open(os.path.join(base, "expected", out + ".out"), encoding="utf-8") as f:
                    want = f.read()
                with tempfile.TemporaryDirectory() as d:
                    if os.path.isdir(os.path.join(base, "data")):
                        os.symlink(os.path.join(base, "data"), os.path.join(d, "data"))
                    c = os.path.join(d, name + ".c")
                    with open(c, "w") as f:
                        f.write(program(os.path.join(base, name + ".goose")))
                    exe = os.path.join(d, name)
                    build(c, exe)
                    stdin = None
                    if name in STDIN:
                        with open(os.path.join(base, STDIN[name]), encoding="utf-8") as f:
                            stdin = f.read()
                    p = _run(exe, stdin)
                    self.assertEqual(p.returncode, 0, p.stderr)
                    self.assertEqual(p.stdout, want)

    def test_gcc(self):
        self.check(build_gcc)

    def test_shivyc(self):
        self.check(build_shivyc)


def refused(src):
    with unittest.TestCase().assertRaises(G.GooseError) as c:
        C.translate(src)
    return c.exception


class TestRefusals(unittest.TestCase):
    def msg(self, src):
        with self.assertRaises(G.GooseError) as c:
            C.translate(src)
        return c.exception

    def test_unknown_name_has_its_line(self):
        e = self.msg("fn main() {\n  print(x);\n}\n")
        self.assertEqual(e.line, 2)
        self.assertIn("unknown name 'x'", e.msg)

    def test_not_a_var(self):
        e = self.msg("fn main() { let a = 1; a = 2; }")
        self.assertIn("not a 'var'", e.msg)

    def test_type_mismatch(self):
        e = self.msg("fn main() { var a: i32 = 1; let b: i64 = 2; a = b; }")
        self.assertIn("expected i32, found i64", e.msg)

    def test_literal_does_not_fit(self):
        e = self.msg("fn main() { let a: u8 = 300; }")
        self.assertIn("does not fit u8", e.msg)

    def test_not_yet_features_say_so(self):
        for src, what in [("fn main() { var a: i32[varint] = [1]; }", "T[varint]"),
                          ("thread_fn w() {}\nfn main() {}", "'thread_fn'"),
                          ("struct S { x: i32 }\nfn main() { var a: S..[>..] = []; }", "variable-mode"),
                          ("import gfx from \"x/y@v1\";\nfn main() {}", "'import ... from'")]:
            with self.subTest(what=what):
                e = self.msg(src)
                self.assertIn(what, e.msg)
                self.assertIn("not in the Crust Goose subset yet", e.msg)

    def test_no_main(self):
        self.assertIn("no 'main'", self.msg("fn f() {}").msg)

    def test_index_out_of_range_aborts(self):
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "a.c")
            with open(c, "w") as f:
                f.write(C.translate("fn main() { let a = [1, 2, 3]; var i = 5; print(a[i]); }"))
            build_gcc(c, os.path.join(d, "a"))
            p = _run(os.path.join(d, "a"))
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("index out of range", p.stderr)

    def test_cli_writes_diagnostic_to_output(self):
        with tempfile.TemporaryDirectory() as d:
            src, out = os.path.join(d, "a.goose"), os.path.join(d, "a.c")
            with open(src, "w") as f:
                f.write("fn main() { print(nope); }")
            p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "goose2c.py"), src, "-o", out],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 1)
            with open(out) as f:
                self.assertIn("unknown name 'nope'", f.read())


if __name__ == "__main__":
    unittest.main()
