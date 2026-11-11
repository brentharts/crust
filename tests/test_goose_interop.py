"""Goose beside C, Rust and C#: `export fn`, `extern fn`, library mode and `#include "x.goose"`
(see GOOSE.md).  Every case is built with the driver, which is ShivyC, and the ones that
need no Crust front end are also checked with gcc."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IX = os.path.join(ROOT, "examples", "goose", "interop")
sys.path.insert(0, ROOT)

import tools.goose2c as C                              # noqa: E402


def read(name):
    with open(os.path.join(IX, name), encoding="utf-8") as f:
        return f.read()


def drive(*files, out, cwd=None, env=None, extra=()):
    e = dict(os.environ, **(env or {}))
    # shivyc leaves its .o/.s beside the sources, so build from a copy of the examples
    work = tempfile.mkdtemp(prefix="goose_src_")
    shutil.copytree(IX, os.path.join(work, "interop"))
    files = [os.path.join(work, "interop", os.path.basename(f)) if os.path.dirname(f) == IX else f
             for f in files]
    return subprocess.run([sys.executable, "-m", "shivyc.main", *extra, *files, "-o", out],
                          cwd=ROOT, capture_output=True, text=True, env=e)


RUST_LIB = os.path.join(ROOT, "lib", "goose", "rust")


def run(exe):
    return subprocess.run([exe], capture_output=True, text=True, timeout=60).stdout


class TestInterop(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="goose_interop_")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def exe(self, name):
        return os.path.join(self.d, name)

    def test_c_calls_goose_exports(self):
        exe = self.exe("export")
        p = drive(os.path.join(IX, "export.goose"), os.path.join(IX, "export_main.c"), out=exe)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(run(exe), read("export.out"))

    def test_c_calls_goose_exports_with_gcc(self):
        c = os.path.join(self.d, "export.c")
        with open(c, "w") as f:
            f.write(C.translate(read("export.goose"), "export.goose", lib=True))
        exe = self.exe("export_gcc")
        subprocess.run(["gcc", "-w", "-o", exe, c, os.path.join(IX, "export_main.c"), "-lm"], check=True)
        self.assertEqual(run(exe), read("export.out"))

    def test_goose_calls_c(self):
        exe = self.exe("extern")
        p = drive(os.path.join(IX, "extern.goose"), os.path.join(IX, "extern_c.c"), out=exe)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(run(exe), read("extern.out"))

    def test_goose_and_rust_in_one_unit(self):
        exe = self.exe("rs")
        p = drive(os.path.join(IX, "main_rs.c"), out=exe)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(run(exe), read("main_rs.out"))

    def test_goose_and_csharp_in_one_unit(self):
        exe = self.exe("cs")
        p = drive(os.path.join(IX, "main_cs.c"), out=exe)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(run(exe), read("main_cs.out"))

    def test_csharp_demo_calls_goose_exports(self):
        exe = self.exe("csd")
        p = drive(os.path.join(IX, "cs_demo.c"), out=exe)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(run(exe), read("cs_demo.out"))

    def test_a_library_with_a_main_keeps_it_as_goose_main(self):
        src = 'export fn two() -> i64 { return 2; }\nfn main() { print("hi"); }\n'
        c = C.translate(src, "m.goose", lib=True)
        self.assertNotIn("int main(", c)
        self.assertIn("void goose_main(void)", c)
        self.assertIn("int main(", C.translate(src, "m.goose"))


class TestRustSpanPool(unittest.TestCase):
    """slice_pool with its free-run bookkeeping in Rust (lib/goose/rust/span_runs.rs) must
    behave exactly like the pure Goose version and like Goose's own."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="goose_rustpool_")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def rust_pool(self, name, goose):
        c = os.path.join(self.d, name + ".c")
        with open(c, "w") as f:
            f.write('#include <stdio.h>\n#include "%s"\n#include "span_runs.rs"\n'
                    'int main(void) { goose_main(); return 0; }\n' % goose)
        exe = os.path.join(self.d, name)
        p = drive(c, out=exe, env={"GOOSE_LIB": RUST_LIB},
                  extra=["-I", RUST_LIB, "-I", os.path.join(ROOT, "examples", "goose")])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return run(exe)

    def test_stress_matches_the_pure_goose_version(self):
        exe = os.path.join(self.d, "pure")
        p = drive(os.path.join(IX, "pool_stress.goose"), out=exe)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(run(exe), read("pool_stress.out"))
        exe = os.path.join(self.d, "rust_pool")
        p = drive(os.path.join(IX, "rust_pool.c"), out=exe, env={"GOOSE_LIB": RUST_LIB},
                  extra=["-I", RUST_LIB])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(run(exe), read("pool_stress.out"))

    def test_the_example_trace_matches_goose(self):
        out = self.rust_pool("trace", "lib_slice_pool.goose")
        with open(os.path.join(ROOT, "examples", "goose", "lib_slice_pool.out"), encoding="utf-8") as f:
            self.assertEqual(out, f.read())


class TestBoundary(unittest.TestCase):
    def refused(self, src):
        with self.assertRaises(C.GooseError) as c:
            C.translate(src, lib=True)
        return c.exception.msg

    def test_a_struct_has_no_plain_c_shape(self):
        m = self.refused("struct P { x: i64 }\nexport fn f(p: P) -> i64 { return p.x; }\n")
        self.assertIn("no plain C shape", m)

    def test_an_export_needs_typed_parameters(self):
        self.assertIn("needs a type", self.refused("export fn f(x) -> i64 { return 1; }\n"))

    def test_an_export_cannot_be_generic(self):
        self.assertIn("type parameters", self.refused("export fn f<T>(x: T) -> i64 { return 1; }\n"))

    def test_extern_declares_only(self):
        self.assertIn("no body", self.refused("extern fn f(x: i64) -> i64 { return x; }\nfn main() {}\n"))

    def test_extern_result_is_a_scalar(self):
        m = self.refused("struct P { x: i64 }\nextern fn f() -> P;\nfn main() { f(); }\n")
        self.assertIn("one scalar", m)

    def test_no_main_and_no_exports(self):
        self.assertIn("no 'main'", self.refused("fn f() {}\n"))


if __name__ == "__main__":
    unittest.main()
