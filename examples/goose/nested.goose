// Nested functions: they see the locals of the function around them, and may
// recurse; `return ... from` leaves the outer function from deep inside.
fn depth_sum(limit: i64) -> i64, bool {
    var total = 0;
    var calls = 0;
    fn bump(by: i64) { total += by; calls++; }
    recursive fn walk(n: i64) -> i64 {
        if n > limit { return 0, false from depth_sum; }
        bump(n);
        if n < 5 { walk(n + 1); }
        return total;
    }
    walk(1);
    return total + calls, true;
}

fn main() {
    let a, ok = depth_sum(10);
    print(a, " ", ok);
    let b, ok2 = depth_sum(3);
    print(b, " ", ok2);
}
