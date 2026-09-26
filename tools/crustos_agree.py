#!/usr/bin/env python3
"""crustos_agree.py -- the kernel `latex2ocaml.py` wrote from the CrustOS
equation, held to the Python it models.

`leanos/generated_crustos.ml` (with `crustos_prims.ml`), compiled through
Crust and run, and interpreted by `ocamlinterp.py`, against: `run` -- the
model's `tick`, `sched`, `tick`, statement for statement as `crustos_eq.py`
writes them -- field by field; and `routing_layer` against
`crustos/schemes.py`'s `accepted`, over `tests/test_crustos_model.py`'s
corpus and batches of it.  The last line is the verdict.
"""
import sys, os, subprocess, tempfile
C='/home/claude/crust'
sys.path.insert(0,C); sys.path.insert(0,C+'/tools'); sys.path.insert(0,C+'/crustos')
import ocaml2rust, ocamlinterp, schemes
from tests.test_crustos_model import CORPUS
def py_run(cur, n, ticks):            # crustos_eq.py's tick, sched, tick
    ticks += 1
    while cur < n:
        cur += 1; ticks += 1
    return (cur, n, ticks + 1)
B = lambda s: '[' + '; '.join(str(b) for b in s.encode()) + ']'
names = 'scheme_names'
calls, want = [], []
for cur, n, t in [(0,0,0),(0,3,0),(2,5,7),(5,5,1),(1,10,100),(0,1,0)]:
    calls += ['cur (run (Ctx (%d, %d, %d)))' % (cur,n,t), 'ticks (run (Ctx (%d, %d, %d)))' % (cur,n,t)]
    r = py_run(cur,n,t); want += [r[0], r[2]]
batches = CORPUS + [','.join(CORPUS), 'sys:a,gpu:b', ',,', 'irq:1,nope:2,file:x']
for u in batches:
    got = schemes.accepted(u)
    calls.append('List.length (routing_layer %s %s)' % (names, B(u))); want.append(len(got))
    calls.append('sum_list (routing_layer %s %s)' % (names, B(u))); want.append(sum(got))
src = ocaml2rust.load(C + '/leanos/generated_crustos.ml') + '\nlet scheme_names = [' + '; '.join(B(x) for x in schemes._NAMES) + ']\nlet rec sum_list l = match l with [] -> 0 | x :: t -> x + sum_list t\n'
src += 'let () =\n' + ';\n'.join('  print_int (%s); print_newline ()' % c for c in calls) + '\n'
interp = [int(x) for x in ocamlinterp.run(src).split()]
d = tempfile.mkdtemp(); rs = d + '/c.rs'; exe = d + '/c'
open(rs, 'w').write(ocaml2rust.lower(src))
r = subprocess.run([sys.executable, '-m', 'shivyc.main', rs, '-o', exe], cwd=C, capture_output=True, text=True)
if r.returncode: print('COMPILE FAIL', (r.stdout + r.stderr)[-600:]); sys.exit(1)
native = [int(x) for x in subprocess.run([exe], capture_output=True, text=True, timeout=30).stdout.split()]
print(len(want), 'answers; interpreter agrees', interp == want, '; compiled agrees', native == want)
if native != want: print([ (k, native[k], want[k]) for k in range(len(want)) if native[k] != want[k]][:6])
