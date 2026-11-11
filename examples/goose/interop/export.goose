// A Goose library for C, Rust and C# to call.  `export fn` makes a C function of that name;
// only plain C shapes cross: scalars, a reference to a scalar, a slice of scalars (as a pointer
// and a length), and extra results (as pointers after the parameters).  Module-level state is
// set up the first time any export is called.
export fn goose_double(x: i64) -> i64 { return x * 2; }

export fn sum_slice(xs: i64[:]) -> i64 {
    var t = 0;
    for x in xs { t += x; }
    return t;
}

export fn divmod(a: i64, b: i64) -> i64, i64 { return a / b, a % b; }

var calls = 0;
export fn bump() -> i64 { calls++; return calls; }

export fn scale(x: f64, k: f64) -> f64 { return x * k; }
