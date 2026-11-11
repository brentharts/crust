# GOOSE — a Goose subset for Crust

Goose (https://github.com/aardappel/goose, Apache-2.0) is a memory-safe
systems language with no heap: every dynamic value lives inline on a few
compiler-managed *data stacks*, freed by restoring a watermark at scope exit.
This document scopes the subset Crust accepts and how it is lowered.

Status: **phase 1 + 2a done, 2b under way** (references, optionals, enums, trailing blocks). `tools/goose.py` parses every sample;
`tools/goose2c.py` lowers the plain subset and the first of the data-stack work
(below). Each program in `examples/goose/` is checked against what the real Goose
compiler prints for it, under gcc and under shivyc (`tests/test_goose2c.py`).

## Decisions

| Question | Decision |
|----------|----------|
| Lowering target | **Direct C**, following Goose spec Appendix C (representation and calling convention). The C is then compiled by shivyc like any other Crust C. |
| Front end | **Fresh, in Python**: `tools/goose.py` (lexer, parser, type checker), `tools/goose2c.py` (lowering). Not a wrapper around Goose's C++ compiler. |
| Scope of v1 | **Core language first.** The Goose-specific memory features arrive in later phases, in the order given below. |
| Oracle | The real Goose compiler, built from a checkout beside this repo (`cmake -B build && cmake --build build`). Its `samples/expected/*.out` and its own output are the reference, as `tools/ocamlinterp.py` is for OCaml. |

CLI (planned, same protocol as `csrust.py`: one output file, one exit status,
the diagnostic written to the output path on failure):

    python3 tools/goose2c.py prog.goose -o prog.c

Line numbers are preserved, so a diagnostic from shivyc on the generated C
points at the user's `.goose` line.

## v1 surface

**In (milestone M1: `samples/01_tour.goose`, 02–07 follow)**

| Area | Notes |
|------|-------|
| Lexical | `//` and nesting `/* */`, decimal/hex ints, floats, char literals as ints, strings with escapes, raw `"""` strings |
| Scalars | `bool i8..i64 u8..u64 f32 f64`; literals adapt, default `i64`/`f64`; arithmetic at operand width; Euclidean `%`; `as` and `as!` casts |
| Bindings | `let`, `var`, `const`, `.=` reference binding, multi-value `let a, b = f()` |
| Control flow | expression-oriented `if`, `match` (values, ranges `a..b`, `_`, variants), `block` with `break value`, `while`, `loop`, `for x in n`, `for i in a..b`, `for x in arr`, `for &x in arr`, `for x, i in arr`, `guard .. else`, `defer` |
| Functions | multiple returns, defaults, inferred result type, overloading, untyped parameters as generics (monomorphized), `fn f<T>`, nested fns capturing locals, trailing function blocks (`xs.filter() { it > 0 }`) with `return` from the enclosing function |
| Structs | packed, declaration order, field defaults, positional and named literals, elementwise arithmetic on same-typed structs |
| Arrays | fixed `T[k]`, fill `[0; n]`, slices `T[:]` with `a[i..j]` and `a[^n..]` |
| Optionals / refs | `T?`, `T&`, `null`, narrowing by `if` / `guard`, `.==` identity compare |
| Enums | fixed mode, payload variants, `match`, case functions (one overload per variant) |
| Output | `print(..)` of scalars, strings, arrays, structs, enums, in Goose's exact format |
| Checks | bounds checks on every index; overflow/range checks under `--debug` |

**Phase 2 (the Goose-specific memory model, in this order)**

1. Data stacks and grow-only `T[>..]`: `push` as a pointer bump, references that
   survive growth, `len`/`cap`/`clear`/`resize`, scope-exit watermark. Strings as `u8[>..]`
   with `append`, `format`, `str`.
2. Limited `T[..k]`, frozen `T[]`, grow-shrink `T[>..<]` (`pop`).
3. Inline variable-size structs and `varint`.
4. Variable-mode enums (`Shape..`).
5. `return E from f`.
6. Relative references (`T&<u32>`, `in pool`), `to_bytes` / `from_bytes`.
7. `thread_fn` and typed queues (candidate: Crust's own threading model).

M1 uses fixed arrays and slices only; the data stack arrives as the first
Phase 2 item because 01_tour already needs `T[>..]`. In practice the milestone
order is: parse every sample, then run the plain subset, then 01_tour with
the data stack in.

**Out for now, refused with the reason and the replacement**

Packages and `import name from "..."`, `extern fn` and the `ffi`/`os`/`gfx`/
`ui`/`physics` modules, `root struct`, `reusable` (deprecated upstream; use
`slot_pool`), `simd` / `checkfree`, `const for` / `const if`, coroutines,
the language server and JIT, `--standalone`.

## Plan

0. **Oracle and scope** (this file; Goose built, samples verified).
1. **Front end**: `tools/goose.py`. Parse all 32 samples, then type-check the plain subset.
2. **Lowering**: `tools/goose2c.py`. Plain subset, then Phase 2 in the order above.
3. **Integration** (done): `tests/test_goose.py`, `tests/test_goose2c.py` (samples against the
   oracle, pinned refusals), `tests/test_goose_driver.py`, `examples/goose/`, `make test_goose` /
   `make goose`, `.goose` handling in `shivyc/main.py`.
4. **Stretch**: roots/lifetime checking tied into `shivyc/memory_safety.py`,
   bounds-check elimination, `extern fn`, stdlib modules.

## Building a Goose program with Crust

```
python3 crust prog.goose -o prog          # translate to C, compile with ShivyC, link
python3 -m shivyc.main prog.goose -o prog # the same
make goose SRC=prog.goose OUT=prog        # the same, as a make target
python3 tools/goose2c.py prog.goose -o prog.c   # just the C
make test_goose                           # parser, translator, examples, samples, driver
```

`shivyc/main.py` handles a `.goose` input like a `.py` one: `process_goose_file` runs
`tools/goose2c.py`, then hands the C to the ordinary C pipeline (`-c`, `-S`, other
flags, and mixing in `.c` files all work). A refusal is a normal diagnostic that names
the `.goose` line. Programs that `import` a library module (`std`, `math`, `os`, ...)
need Goose's `stdlib/` source: set `GOOSE_ROOT`, copy it to `crust/lib/goose/`, or keep a
`goose` checkout next to this one.

## Interop: Goose beside C, Rust and C#

Only plain C shapes cross the boundary, so any of the languages can call any other through
ordinary C symbols.

| Goose | C |
|---|---|
| `i64`, `i32`, `f64`, ... | the same scalar; `bool` is `uint8_t` |
| `T&` (reference to a scalar) | `T *` |
| `T[:]` (slice of scalars) | two arguments: `T *data, int64_t len` |
| first result | the C return value |
| further results | `T *outN` pointers after the parameters |

Structs, growable arrays, enums and slice results are refused with a diagnostic that says the
type has no plain C shape.

- `export fn f(...) -> ...` defines a C function named `f`. It calls `gs_init()` first, which is
  idempotent, so module-level `var`s are set up on the first call from any language.
- `extern fn f(...) -> ...;` declares a C function (no body); `= "sym"` after the signature
  names a different C symbol.
- Library mode (`--lib`, which `#include "x.goose"` always uses): no C `main` is emitted, and
  a Goose `main` becomes `goose_main`. A file with no `main` is accepted when it has exports.
- `#include "x.goose"` lowers the file at the include point, like `.rs` / `.cs` / `.py`. Limit:
  one `.goose` per translation unit.
- Rust and C# calling a Goose export need a prototype or the `.goose` include before the
  `.rs` / `.cs` include (the Rust subset has no `extern "C"` blocks). A C# `class Side { static
  long CsUsesGoose }` lowers to the C name `Side_CsUsesGoose`.

### A Rust span allocator behind `slice_pool`

`lib/goose/rust/` holds a second `slice_pool_internal` whose free-run decisions (first fit,
extending the last run, merging neighbours, growing in place) are made in Rust,
`lib/goose/rust/span_runs.rs`, a port of the first-fit/coalescing idea in
`examples/rpython2c/mbos/alloc.rs` from byte blocks to pool cells. Goose copies the runs out of
its leaf nodes into a flat `(start, count)` array, calls `span_place` / `span_release` /
`span_regrow` through `extern fn`, and writes the runs back, so the leaf layout `recycle_all`
relies on is unchanged. The copy makes each operation linear in the number of free runs, as the
pure Goose version already is.

    GOOSE_LIB=lib/goose/rust python3 crust -I lib/goose/rust examples/goose/interop/rust_pool.c -o rust_pool

`GOOSE_LIB` (a path list) is searched before `lib/goose/override/`, and an `extern fn` in a file
found there is a user extern, supplied by C, Rust or C#. The Rust file must follow the Goose one
in the same C file. `tests/test_goose_interop.py` checks that it prints what the pure Goose
version and Goose's own example output print, including runs past one leaf.

### A C# class calling Goose

`examples/goose/interop/cs_demo.{goose,cs,c}`: `Report.Longest` calls the exports
`collatz_steps` and `count_call`.

Examples and tests: `examples/goose/interop/`, `tests/test_goose_interop.py`.

## Phase 0 findings

- The oracle builds (Release, ~2 min on 2 cores) and `samples/01_tour.goose`
  matches `samples/expected/tour.out`. `07_sieve` matches except for a timing line
  that Goose writes to stderr.
- `goose -o prog.c prog.goose` emits one C file of ~2.7k lines, most of it an
  embedded copy of `runtime.h`. Linked with `goose --emit-runtime rt.c` output it
  runs correctly under gcc.
- The same C **does not compile under shivyc yet**: shivyc's `math.h` lacks
  `fmaf`, `exp2f`, `expm1f` and friends. Our own emitted C will not depend on
  Goose's runtime; it will use a small purpose-written runtime header
  (data-stack control blocks, varints, printing, aborts) in the spirit of
  Appendix C, so this gap is avoided rather than fixed.
- Even the "core" samples lean on `print` of arbitrary values, `str`/`format`
  and `T[>..]`, which is why the data stack is the first Phase 2 item.

## What runs today (`tools/goose2c.py`)

| Area | State |
|------|-------|
| Scalars, `let`/`var`/`const`, arithmetic at operand width, Euclidean `%`, shifts, casts, float printing | done; matches Goose digit for digit (`%.15g`..`%.17g` until it round-trips, `%.6g`..`%.9g` for `f32`) |
| `if`/`match`/`block` as expressions, `while`, `loop` with `break value`, `for` over a count, a range, an array, `for &x`, `for x, i`, `guard` | done; `match` ranges are half-open; an arm that ends in `return`/`break`/`continue` gives no value and needs none |
| Functions: multiple results (first by return, the rest by out-pointer), overloads, untyped parameters, `<T>` and explicit type arguments, defaults, UFCS (`x.f(a)`), `assert`/`abort`/`exit` | done; one C function per instantiation |
| Nested functions (`fn`, `recursive fn`) | done; lifted to C functions that take pointers to the enclosing locals they can see, so they read and write them, recurse and call each other; a named nested function can stand in for a block (`sort(xs, by_name)`) |
| `return E from f` | done, both ways: from a block inside `f`, and from any function `f` calls (a `gs_rf` flag, set at the throw site and checked after each call that can reach it, runs every scope's release code on the way out) |
| Structs (packed *not* yet: plain C layout), generic structs, root structs that own growables, fixed arrays, slices, `a[i..j]`, `a[^n..]`, bounds checks, memberwise arithmetic | done |
| `T[>..]`, `T[>..<]`, `T[..k]`: `push pop clear resize append len cap`, indexing, slices, `for`, `copy`, `str`, `format`, `var out = [];` typed by its first push | done; growables live on data stacks, one per declaration site, released at every scope exit. `pop` on a grow-only array is allowed (Goose's "no live reference" check is not repeated) |
| Frozen `T[]` (strings, arrays of strings), `==` on slices | done; copied onto the receiving scope's stack when bound |
| `print` of scalars, strings, arrays, structs, enums; user `fn format(out, v)` overloads for structs, enums and arrays (used by `print` and `str`) | done |
| Optionals `T?`, references `T&`, `.=`, `.==`, `index_of`, flow narrowing (also across `&&`) | done; an optional field left out of a struct literal is null |
| Relative references `T&<u32>`, `T&<u16 in pool>` | accepted and represented as plain pointers; only the *size* differs, and `bytes_of(..).len` reports Goose's packed size for such types |
| Fixed-mode enums, variant literals, `match` on variants, case functions, `==` | done |
| Trailing blocks and generic `<T, F>` helpers (`xs.each_pair() { a, b => .. }`, `it`, `return` inside a block leaves the enclosing function) | done; a helper taking a function is expanded inline at the call site (not recursively), and may give back a growable array |
| Crust's own library versions (`lib/goose/override/`) | searched before `GOOSE_ROOT`. `slice_pool_internal.goose` replaces Goose's C span tree (`gs_slice_pool_*`) with the same four functions in plain Goose: free runs in one sorted list (first fit, merging on release, in-place regrow), stored in leaves that keep `recycle_all` working. Placements are identical to Goose's (checked against it with random alloc/free/realloc/recycle sequences, `examples/goose/lib_slice_pool.goose`); only the speed differs, linear in the number of free runs instead of logarithmic, and `trim` does nothing |
| Namespaces (`namespace x;` in a library, `ns::f`, `ns::Type`), `import std;` and other library modules | done (base64 and csv run; regex and json need frozen arrays of enums and variable-mode enums): the library is compiled from its own Goose source (`GOOSE_ROOT`, `crust/lib/goose` or a sibling `goose` checkout), only what a program reaches; scalar/slice `extern fn`s of the library map to small C helpers |
| `defer`, `varint`, variable-mode enums (`Shape..`), `self`, namespaced types, `thread_fn`, user `extern fn`, `import gfx`, `to_bytes`/`from_bytes` | refused, with the line and "is not in the Crust Goose subset yet" |

**Goose's own samples** (`tests/test_goose2c.py`, needs a Goose checkout; each is built with gcc and with
shivyc and compared with the output Goose ships): `01_tour 02_memory 03_strings 04_errors 06_functions
07_sieve 08_bignum 09_sorting 10_sudoku 11_maze 12_huffman 14_bst 15_word_freq 17_calc 19_vm 20_life 21_raytrace
22_image_filters 26_file_tree`: 19 of 32 pass. The rest:

| Sample | Needs |
|--------|-------|
| 05_shapes, 16_records, 18_json, 25_serialize | `varint`, variable-mode (packed) enums and structs, raw serialization |
| 13_linked_list | `self` inside a slot-pool `alloc_with` block |
| 23_mandelbrot_threads | `thread_fn` |
| 24_call_c | user `extern fn` against a C header that uses Goose's own generated names |
| 27-32 | the `gfx` / `ui` / `physics` libraries (graphics; out of scope) |

Not matched yet: Goose's packed layout (the C structs are ordinary), debug-mode
overflow checks, and the exact text of runtime aborts.

## Findings about the Crust toolchain

- **shivyc miscompiles `if (K < 0 || p + n * sizeof(T) > q) ...`** when `K` is a
  constant, `p`/`q` are pointers and the statement follows a store through a
  struct member: the program crashes at run time. Written as two `if`s it is
  correct. The emitted C avoids the pattern; the bug itself is not fixed here.
- shivyc's `<stdlib.h>` has no `strtod`/`strtof`, and `<math.h>` lacks `fmaf`,
  `exp2f`, `expm1f` and friends (which is why Goose's own emitted C does not
  compile under shivyc). The runtime here declares `strtod` itself.
