pub extern "C" fn rust_triple(x: i64) -> i64 {
    return x * 3;
}
pub extern "C" fn rust_uses_goose(x: i64) -> i64 {
    return goose_double(x) + 100;
}
