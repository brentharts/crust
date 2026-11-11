struct Point { x: f64, y: f64 }
struct Player { name: u8[4], pos: Point, hp: i32 = 100, alive: bool = true }

fn total(xs: i64[:]) -> i64 {
    var t = 0;
    for x in xs { t += x; }
    return t;
}
fn divide(a: i64, b: i64) -> i64, bool {
    if b == 0 { return 0, false; }
    return a / b, true;
}
fn larger(a, b) { if a > b { a } else { b } }
fn twice(x) { x + x }
fn span<T>(a: T, b: T) -> T { if a > b { a - b } else { b - a } }
fn describe(x: i64) -> i64 { x * 10 }
fn describe(x: f64) -> f64 { x * 0.5 }
fn fixed_sum(xs: i32[4]) -> i64[4] { return [xs[0], xs[1], xs[2], xs[3]]; }
recursive fn fib(n: i64) -> i64 { if n < 2 { n } else { fib(n - 1) + fib(n - 2) } }

fn main() {
    var fixed: i32[4] = [1, 2, 3, 4];
    fixed[0] = 10;
    let zeros: f64[8] = [0.0; 8];
    print(fixed, " ", zeros.len, " ", zeros[3]);
    let ws = [5, 6, 7, 8, 9];
    let middle = ws[1..4];
    let last_two = ws[^2..];
    print(middle, " ", last_two, " ", total(middle), " ", total(ws), " ", total(fixed_sum(fixed)));
    var p = Player { name: [1, 2, 3, 4], pos: Point { 1.0, 2.0 } };
    p.hp -= 30;
    print(p.hp, " ", p.pos.x, ",", p.pos.y, " ", p.alive);
    let kind = if p.hp > 50 { "healthy" } else { "hurt" };
    let level = match p.hp { 0 => "dead", 1..50 => "low", _ => "fine" };
    print(kind, " ", level);
    let verdict = block {
        guard p.alive else { break "gone"; }
        guard p.hp > 0 else { break "dead"; }
        "standing"
    };
    print(verdict);
    var acc = 0;
    for i in 3 { acc += i; }
    for i in 10..13 { acc += i; }
    for x in fixed { acc += x; }
    for &x in fixed { x *= 2; }
    for x, i in ws { acc += x * i; }
    var n = 0;
    while n < 3 { n++; }
    loop { n--; if n == 0 { break; } }
    print(acc, " ", fixed, " ", n);
    let q, ok = divide(7, 2);
    let q2, ok2 = divide(1, 0);
    print(q, " ", ok, " ", q2, " ", ok2);
    print(larger(3, 9), " ", larger(2.5, 1.5), " ", twice(21), " ", twice(1.5), " ", span(3, 8), " ", span(2.5, 1.0));
    print(describe(7), " ", describe(2.5), " ", fib(20));
    let r = loop { n++; if n > 4 { break n * 2; } };
    print(r);
    var j: u32 = 0; while j < 3 { j += 1; } print(j);
    let w = if r > 5 && n > 1 || false { 1 } else { 2.5 };
    print(w);
}
