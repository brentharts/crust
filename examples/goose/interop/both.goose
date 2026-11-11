// One C file, three languages: Goose calls a Rust function, Rust and C# call Goose exports.
extern fn rust_triple(x: i64) -> i64;
export fn goose_double(x: i64) -> i64 { return x * 2; }
export fn goose_combo(x: i64) -> i64 { return rust_triple(x) + 1; }
