// Goose calling C (or Rust, or C#): an `extern fn` names a C function and uses the same shapes.
extern fn c_triple(x: i64) -> i64;
extern fn c_sum(xs: i64[:]) -> i64;
extern fn c_scale(x: f64, k: f64) -> f64 = "scale_it";   // the string is the C symbol
extern fn c_fill(out: i64&, v: i64);

fn main() {
    print(c_triple(14));
    let xs = [1, 2, 3, 4, 5];
    print(c_sum(xs[1..]));
    print(c_scale(1.5, 4.0));
    var z = 0;
    c_fill(z, 77);
    print(z);
}
