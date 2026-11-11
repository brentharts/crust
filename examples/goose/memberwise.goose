struct P { x: f64, y: f64 }
struct Q { a: i32, b: i32 }
fn main() {
    var p = P { 1.0, 2.0 };
    p = p + P { 0.5, 0.5 };
    let q = p * 2.0;
    let r = 3.0 * q - p;
    let m = [1, 2, 3] + [10, 20, 30];
    let n = Q { 7, 9 } / 2;
    print(p, q, r, m, n, [1.5, 2.5] * 2.0);
}
