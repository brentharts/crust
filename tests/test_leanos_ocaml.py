"""LeanOS's modules, ported to OCaml.

`leanos/{memmap,elfcheck,alloc,threads,loader}.ml` are ports of the rpython
files of the same names, beside the Rust ports.  Each is held to the
original the way the Rust ports are (`test_leanos_rust.py`):

  * compiled -- `tools/ocaml2rust.py` to Crust's Rust, Crust to a binary --
    and run, it answers as the Python does over the Python model test's own
    corpus, row for row; and so does `tools/ocamlinterp.py`, the reference
    semantics, on the same program;
  * every obligation `tools/rustprove.py` states about the compiled Rust --
    the contracts written as `[@@ensures]`, every place it could panic, and
    every place an OCaml `int` could wrap -- is proved, except the open set
    pinned here, each with its reason.  Slow (minutes a module): run with
    LEANOS_OCAML_PROOFS=1.

The ports differ from the Python only where OCaml's 63-bit `int` does (see
each file's header); the corpora stay below that, so the answers agree.
"""

import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "leanos"))

import ocaml2rust                                         # noqa: E402
import ocamlinterp                                        # noqa: E402
from tests import test_memmap_model as MM                 # noqa: E402
from tests import test_alloc_model as AM                  # noqa: E402
from tests import test_threads_model as TM                # noqa: E402
from tests import test_elfcheck_model as EM               # noqa: E402
from tests import test_loader_model as LM                 # noqa: E402

LEANOS = os.path.join(ROOT, "leanos")
MODULES = ("memmap", "elfcheck", "alloc", "threads", "loader")


def _l(xs):
    return "[" + "; ".join(str(x) for x in xs) + "]"


def _calls(module):
    """For each corpus row: the OCaml calls, and the Python's answers."""
    if module == "memmap":
        for row in MM.CORPUS:
            _, b, s, o, who, addr, i = row
            yield (["regions_disjoint %s %s" % (_l(b), _l(s)),
                    "contains %s %s %d %d" % (_l(b), _l(s), i, addr),
                    "owned_by %s %d" % (_l(o), who),
                    "region_of %s %s %s %d %d" % (_l(b), _l(s), _l(o), who,
                                                  addr)], MM._python(row))
    elif module == "alloc":
        B, S, O = AM.B, AM.S, AM.O
        for row in AM.CORPUS:
            _, tid, heap, used, n = row
            yield (["bump %s %s %s %d %d %d %d" % (_l(B), _l(S), _l(O), tid,
                                                   heap, used, n),
                    "slot_addr %s %d %d" % (_l(B), heap, used)
                    if heap < len(B) else "0",
                    "slot_ok %s %s %d %d" % (_l(B), _l(S), heap, used)
                    if heap < len(B) else "0"], AM._python(row))
    elif module == "threads":
        r = "%s %s %s" % (_l(TM.B), _l(TM.S), _l(TM.O))
        for row in TM.CORPUS:
            _, sps, tid, sp, n = row
            yield (["sp_ok %s %d %d" % (r, tid, sp),
                    "all_sps_ok %s %s" % (r, _l(sps)),
                    "sp_after_push %s %d %d %d" % (r, tid, sp, n),
                    "thread_owner %d" % tid], TM._python(row))
    elif module == "elfcheck":
        for row in EM.CORPUS:
            _, v, m, e, c = row
            yield (["loads_ordered %s %s" % (_l(v), _l(m)),
                    "entry_in_load %s %s %d" % (_l(v), _l(m), e),
                    "reg_class_ok %d" % c,
                    "accept_image %s %s %d %d" % (_l(v), _l(m), e, c)],
                   EM._python(row))
    elif module == "loader":
        B, S, O = LM.B, LM.S, LM.O
        for row in LM.CORPUS:
            _, v, m, e, cls, guest = row
            nb = "extended %s %s" % (_l(B), _l(v))
            own = "claimed %s %d %d" % (_l(O), guest, len(v))
            yield (["admit %s %s %s %s %d %d" % (_l(B), _l(S), _l(v), _l(m),
                                                 e, cls),
                    "List.length (%s)" % nb, "last_or (%s) 0" % nb,
                    "List.length (%s)" % own, "last_or (%s) 0" % own],
                   LM._python(row))


# for the loader's rows: a list's last element, as the Python's `xs[-1]`
_HELPERS = ("\nlet rec last_or xs d = match xs with [] -> d | [x] -> x\n"
            "  | _ :: t -> last_or t d\n")


def _driver(module):
    calls, want = [], []
    for cs, answers in _calls(module):
        calls += cs
        want += list(answers)
    source = ocaml2rust.load(os.path.join(LEANOS, module + ".ml")) + _HELPERS
    source += "let () =\n" + ";\n".join(
        "  print_int (%s); print_newline ()" % c for c in calls) + "\n"
    return source, want


def _run_compiled(source):
    d = tempfile.mkdtemp()
    rs, exe = os.path.join(d, "m.rs"), os.path.join(d, "m")
    with open(rs, "w") as fh:
        fh.write(ocaml2rust.lower(source))
    r = subprocess.run([sys.executable, "-m", "shivyc.main", rs, "-o", exe],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(r.stdout + r.stderr)
    out = subprocess.run([exe], capture_output=True, text=True, timeout=60)
    return [int(x) for x in out.stdout.split()]


class TestAgreesWithPython(unittest.TestCase):
    """Compiled and run, and interpreted, over each Python model's corpus."""

    def check(self, module):
        source, want = _driver(module)
        self.assertEqual([int(x) for x in ocamlinterp.run(source).split()],
                         want, "the interpreter")
        self.assertEqual(_run_compiled(source), want, "the compiled code")

    def test_memmap(self):
        self.check("memmap")

    def test_elfcheck(self):
        self.check("elfcheck")

    def test_alloc(self):
        self.check("alloc")

    def test_threads(self):
        self.check("threads")

    def test_loader(self):
        self.check("loader")

    def test_uses_brings_in_what_a_module_calls(self):
        loader = ocaml2rust.load(os.path.join(LEANOS, "loader.ml"))
        for name in ("let accept_image", "let regions_disjoint",
                     "let admit"):
            self.assertEqual(loader.count(name), 1, name)
        self.assertLess(loader.index("let accept_image"),
                        loader.index("let admit"))


# Every obligation the kernel does not settle, per module, and why.  A new
# open obligation fails the proof test, so one cannot appear quietly.
OPEN = {
    'memmap': set(),
    # `v + m` behind not (v > max_int - m), the same shape; accept_image
    # reaches reg_class_ok's contract through a comparison with 0
    'elfcheck': {
        ('ml_accept_image', 'ensures'),
        ('ml_loads_from', 'OCaml `int` `+` may wrap'),
    },
    # `b + used` behind 0 <= b, 0 <= used, b <= max_int - used: the guard
    # is a case the tactic does not yet split before it says anything
    'alloc': {
        ('ml_slot_addr', 'OCaml `int` `+` may wrap'),
        ('ml_slot_ok', 'OCaml `int` `+` may wrap'),
    },
    # sps_from's `requires` at 0 is the list bound; its contract, reached
    # through a comparison with 0
    'threads': {
        ('ml_all_sps_ok', "`ml_sps_from`'s `#[requires]`"),
        ('ml_all_sps_ok', 'ensures'),
    },
    # elfcheck's, and admit reaching accept_image's contract
    'loader': {
        ('ml_accept_image', 'ensures'),
        ('ml_admit', 'ensures'),
        ('ml_loads_from', 'OCaml `int` `+` may wrap'),
    },
}


@unittest.skipUnless(os.environ.get("LEANOS_OCAML_PROOFS"),
                     "slow: set LEANOS_OCAML_PROOFS=1 to prove each module")
class TestProved(unittest.TestCase):
    def test_open_sets(self):
        sys.path.insert(0, os.path.join(ROOT, "..", "RosettaMath"))
        import rustprove
        only = os.environ.get("LEANOS_OCAML_MODULES")
        for module in (only.split(",") if only else MODULES):
            with self.subTest(module=module):
                source = ocaml2rust.load(os.path.join(LEANOS, module + ".ml"))
                rust = ocaml2rust.lower(source + "\nlet () = print_newline ()\n",
                                        every_function=True)
                path = os.path.join(tempfile.mkdtemp(), module + ".rs")
                with open(path, "w") as fh:
                    fh.write(rust)
                prover = rustprove.Prover(*rustprove.load_unit(path))
                opened = set()
                for name in prover.lifted:
                    if name not in prover.own:
                        continue
                    fn = prover.lifted[name]
                    if fn.ensures and not prover.contract(name):
                        opened.add((name, "ensures"))
                    for label, ok in prover.safety(name):
                        if not ok:
                            opened.add((name, label.split(" (line")[0]))
                self.assertEqual(opened, OPEN[module])


if __name__ == "__main__":
    unittest.main()
