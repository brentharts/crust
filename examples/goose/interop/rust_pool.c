/* Goose's slice_pool with its free-run bookkeeping done in Rust: build with
 *   GOOSE_LIB=lib/goose/rust python3 crust examples/goose/interop/rust_pool.c -o rust_pool
 * One C file, two languages: the Goose program first (its `extern fn span_*` declarations become
 * C prototypes), then the Rust file that defines them. */
#include <stdio.h>
#include "pool_stress.goose"
#include "span_runs.rs"
int main(void) { goose_main(); return 0; }
