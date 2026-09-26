# LeanOS

A kernel written to be proved, from nothing, in the three languages this tree
already knows how to prove things about: rpython for decisions over lists and
records, C with contracts for the machine, and the `hoare.py` fragment for the
model that RosettaMath checks and Lean 4 checks again.

CrustOS was built to show Crust could compile Redox, and it did: eighty-nine
upstream objects link. But a kernel assembled to be compilable is not a kernel
assembled to be proved. Its `Context` is not the model's `Context`; its
scheduler reads fields through pointers the lift refuses; its loader ran what
it was given until `elfcheck.py` was bolted on. LeanOS keeps what CrustOS
learned -- the model-against-the-shipped-code discipline, the IL lift, the
validator that decides before it dereferences -- and drops the rest.

## The founding rule

**Memory is data before it is memory.** Every region the kernel will ever
touch -- each thread's stack, each thread's slots for spilled values and
libc state, each mapped ELF segment, the kernel's own tables -- is an entry
in one list, laid out at build time, with a base, a size and an owner. That
list is a value the proof side can quantify over, and *disjointness of the
list* is the one theorem everything else rests on. (Status: `memmap.py`
exists, is compiled by ShivyCX, agrees with its model over a corpus, and
has thirteen theorems Lean accepts, and the last of them is this
paragraph as a statement: `region_of_unique`, that `region_of` says 1 for
at most one owner whenever `regions_disjoint` says 1. See `memmap_eq.py`
for the chain that gets there.) A thread cannot read a
region it does not own because there is no address in its register file or
its slots that lands in one; the compiler allocated from the list.

This is why the register partition in `BAREMETAL_THREADS.md` is not a
special case of LeanOS but its first instance: two threads, one core,
disjoint registers, and the switcher saves what the partition says. LeanOS
does the same for memory, and for the same reason -- so that the
partition is a fact the compiler computed and the kernel can check, not a
convention the code hopes to keep.

Three consequences follow, and each is a milestone below.

1. **No shared heap.** A thread's `malloc` is a bump allocator over its own
   region. `used <= size` is its invariant and it is the `accepted_bounded`
   shape the kernel already proves.
2. **No re-entrant libc.** Each thread links the functions its call graph
   reaches, and nothing else, into its own region. A function with no shared
   state is a pure function of its thread's record, which is what the model
   already is.
3. **Memory access lifts.** A load from a region the list names is a field
   read on a record the lift can name, not a `ReadAt` it must refuse. The 93
   refusals in `kernel.c` are the count of pointers the list would have
   replaced.

## What is kept, by file

| from CrustOS | in LeanOS | why |
|---|---|---|
| `crustos/elfcheck.py` | `leanos/elfcheck.py` | already decides before it dereferences; its loads become entries in the region list |
| `tests/test_crustos_model.py`'s four checks | every module's test | behaviour, shape, coverage, Lean; plus compiled, plus end to end |
| `shivyc/ilproof.py` | unchanged | the seam from compiled C back to the model |
| `elf_regs_for_class` and its bound | unchanged | the one kernel theorem that already crosses from IL to Lean |

Nothing from `vendor/`. No `PageFlags<A>`. No `Mutex` that does not lock.

## Milestones, in the order their proofs depend on each other

**0. The region list, and its theorem.** `leanos/memmap.py`: a list of
`(base, size, owner)` and `regions_disjoint`, which is `loads_ordered` with
an owner column. The theorem is `regions_disjoint == 1 → no two regions
overlap`, and it is a *loop* theorem -- the same one `elfcheck.py` still
owes. Nothing else in LeanOS can be proved without it, so it comes first
and is proved before the second file exists.

**1. Threads as records.** `leanos/threads.py`: a thread is an owner and a
stack pointer, `sp_ok` is the access rule applied to the stack, and
`sp_after_push` is a guarded update that refuses rather than faults. Two
theorems Lean accepts: `push_keeps_sp_ok` -- a push never leaves the
thread's region, for any `n` -- and `sp_no_cross` -- two threads whose
stack pointers coincide are the same thread. Both are corollaries of
`region_of_unique`, which is the point. (Status: done; `all_sps_ok`, the
table check, is modelled and tested but its loop theorem is not yet
stated.)

**2. The per-thread allocator.** `leanos/alloc.py`: `bump` moves `used` up
a region the thread owns if the request fits, else leaves it -- out of
memory is a decision, not a fault. Three theorems Lean accepts:
`bump_bounded` (`used <= size` is kept), `bump_monotone` (allocation never
gives back), `slot_in_bounds` (the slot handed out is inside the region).
(Status: done.)

**3. The loader.** `leanos/loader.py`: `admit` is `accept_image` and then
`regions_disjoint` on the region list with the loads appended, both guards
written `== 1` so each is the theorem about it. Three theorems Lean accepts:
`admit_accepts`, `admit_disjoint`, and the composition `admit_ordered` --
an admitted image leaves the grown list in order, so every theorem about
the region list holds with the guest in it. The ELF theorem and the memory
theorem are one because the loader makes them one check. (Status: done;
`extended` and `claimed` are modelled and tested, their length theorems
not yet stated.)

**4. The switch.** `leanos/switch.c`: the switcher's decisions -- which
side owns a register, where it is saved, how many a side saves, who runs
next -- as integer functions of the partition, in C. Nothing is modelled
from source: `ilproof.py` lifts each function from the compiled IL, the
binary agrees with the lift over every register number, and eight theorems
proved from the lift are Lean-accepted, among them that the left side is
below the split and the right is not (disjoint footprints), and that the
left save set is the six-register footprint `BAREMETAL_THREADS.md`
measured. (Status: the decisions, done. The saving itself is stores and
loads into the thread's slots -- the memory access the lift refuses, and
exactly what the region list was built to make liftable. That is the next
step. `assert split >= 19` as a contract is a compiler item: the front end
takes `len(x)` bounds only.)

**5. Syscalls.** Each one a function from a thread record to a thread record,
which is what `tick` and `sched` in `crustos_eq.py` already are -- now about
a `Context` that ships.

Hosted first, on the same terms as CrustOS: a program, not a boot image,
until milestone 4 gives a reason to boot.

## The OCaml port

Every module has a third version: `memmap.ml`, `elfcheck.ml`, `alloc.ml`,
`threads.ml`, `loader.ml`, ports of the rpython files beside the Rust ones.
`tools/ocaml2rust.py` compiles each to Crust's Rust (a `uses:` line in the
header names the modules it calls, as the Rust ports' `// uses:` does), so
the compiled kernel is under the same tooling the Rust port is:
`tools/rustprove.py` proves the contracts written as `[@@ensures]` /
`[@@requires]`, every place the compiled code could panic, and every place an
OCaml `int` could wrap.

The lists stay three (`bases`, `sizes`, `owners`), as in the Python, so
each function answers the Python's question with the Python's arguments; a
loop over an index is a recursion down the lists, `[@@variant]` its
measure, and a tail-recursive one compiles back to a loop. The loader
builds the grown lists by appending, as the Python does -- an OCaml list is
a value, and appending one makes another -- where `loader.rs` reads the
concatenation in place.

**Held to the Python.** `tests/test_leanos_ocaml.py`: compiled and run, and
run again by `tools/ocamlinterp.py`, each module answers as the Python does
over the Python model test's own corpus -- 236 answers across the five,
every one the same.

**Where it differs from the Python** is where OCaml's 63-bit `int` differs
from the Python's unbounded one, as the Rust differs where a `u64` does;
each file's header lists its differences. In short: a sum `b + s` that
could pass `max_int` is written as the difference that cannot (`addr - b <
s` behind `b <= addr`), an address or size below 0 is refused rather than
compared, a load running past `max_int` is refused, and a negative index,
request or push is refused where the Python would count from the end or
move backwards. On values in [0, 2^62) the answers are the Python's.

**What the proofs assume**, as the Rust proofs assume a `u64` holds a `u64`:
an integer parameter is an OCaml `int`; so is every integer inside a list;
and a list in memory has fewer than 2^60 elements (each cell is a distinct
allocation of at least 16 bytes). These are true of every value the running
code can hold, and are stated as hypotheses in each theorem, not proved.

**Proved.** memmap: every obligation, 34 of 34. elfcheck 30 of 32, alloc 44
of 46, threads 51 of 53, loader 68 of 71 (the loader's unit includes the
elfcheck and memmap functions it calls): every obligation but the open set
below, which `tests/test_leanos_ocaml.py` pins (`LEANOS_OCAML_PROOFS=1`). Lean has not been asked
yet: the certificates exist, and checking them is the next step.

**Open**, each an obligation the code meets and the tactics do not yet
settle -- not a finding about the code:

| module | obligation | why it holds |
|---|---|---|
| alloc | `slot_addr`, `slot_ok`: `b + used` may wrap | behind `0 <= b`, `0 <= used` and `b <= max_int - used` |
| elfcheck | `loads_from`: `v + m` may wrap | behind `0 <= m` and `not (v > max_int - m)` |
| elfcheck | `accept_image`: 0 or 1, and 1 only for `cls <= 3` | `reg_class_ok`'s contract, and the others' |
| threads | `all_sps_ok`: `sps_from`'s `requires` at 0; its result 0 or 1 | a list has fewer than 2^60 elements; `sps_from`'s contract |
| loader | `admit`: 0 or 1, and 1 only for `cls <= 3` | through `accept_image`'s |

The first two are one shape -- a guard `x <= max_int - y`, whose right side
is itself a case the tactic must split before the guard says anything; the
rest reach a callee's contract through a comparison with a constant.

## A kernel from an equation

`generated_crustos.ml` is not written by hand.  `tools/latex2ocaml.py`
reads the equation RosettaMath's `crustos_eq.tex` states the CrustOS kernel
as (`eq:crustos`) and writes the OCaml it describes: the definitions the
equation makes in its braces and `where` lines, its `Nat.rec` fold as a
counted recursion, and its two theorems as `[@@requires]`/`[@@ensures]`.
What the equation names and does not define -- `tick`, `pass`,
`accepted`, `split`, the context -- is `crustos_prims.ml`, a hand port of
the model.  `tools/crustos_agree.py` holds the compiled kernel to the
Python; `tests/test_leanos_ocaml.py` checks that it regenerates byte for
byte and still agrees.  Proving the equation's theorems again of the
*compiled* kernel is in progress: the ocamlrust paper's table has where it
stands.

## What is claimed at each step, and what is not

Every milestone states its theorem in `LEAN.md` terms: what is modelled,
what the model paraphrases, what the corpus stands in for. A milestone
whose theorem is stated and not proved says so in its docstring, as
`elfcheck_eq.py` does today. A theorem about a model is a theorem about a
model; the test that runs the compiled code over the same corpus is what
makes it a theorem about the kernel.
