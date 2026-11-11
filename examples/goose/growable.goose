struct Item { id: i32, weight: f32 }
var total: i64[>..] = [100, 200];
fn sum(xs: i64[:]) -> i64 { var t = 0; for x in xs { t += x; } return t; }
fn main() {
    var grow: i64[>..] = [];
    for i in 5 { grow.push(i * i); }
    var stack: i64[>..<] = [];
    stack.push(1); stack.push(2); stack.push(3);
    let top = stack.pop();
    var small: u8[..8] = [];
    small.push('h'); small.push('i');
    print(grow, " ", stack, " ", top, " ", small, " ", small.len, "/", small.cap);
    var s: u8[>..] = [];
    s.append("hello");
    s.push(' ');
    format(s, "x", 1, " ", 2.5);
    print(s, " (", s.len, " bytes)");
    let middle = grow[1..4];
    print(middle, " ", sum(middle), " ", sum(grow), " ", grow[^2..], " ", total);
    var items: Item[>..] = [];
    for i in 1..4 { items.push(Item { i as i32, i * 0.5 }); }
    for &it in items { it.weight += 1.0; }
    print(items, " ", items.len);
    var a: i64[>..] = [1, 2, 3];
    var b = copy(a);
    b.push(4);
    print(a, " ", b);
    for round in 3 {
        var scratch: u8[>..] = [];
        for i in 1000 { scratch.push((i % 256) as u8); }
        print("round ", round, ": ", scratch.len, " bytes");
    }
    var msg = str("n=", 42, " ", true);
    print(msg);
    var big: u8[>..] = [];
    big.resize(10, 1); big[0] = 0;
    print(big.len, " ", big[0], big[9]);
    grow.clear(); print(grow.len);
    total.push(300); print(total);
}
