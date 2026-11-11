struct Item { id: i32, w: f32 }
fn first_at_least(xs: i64[:], min: i64) -> i64? {
    for &x in xs { if x >= min { return x; } }
    return null;
}
fn bump(it: Item&) { it.w += 1.0; it.id++; }
fn reset(buf: u8[>..]&) { buf.clear(); buf.push(65); }
fn find(items: Item[>..]&, id: i32) -> Item? {
    for &it in items { if it.id == id { return it; } }
    return null;
}
fn main() {
    var grow: i64[>..] = [1, 4, 9, 16];
    let big = first_at_least(grow, 5);
    if big { print("first >= 5 is ", big); } else { print("none"); }
    let none = first_at_least(grow, 100);
    print(none == null, " ", big != null);
    var r .= grow[0];
    r = 100;
    r .= grow[1];
    r += 1;
    print(grow, " ", grow.index_of(r), " ", r .== grow[1], " ", r .== grow[2]);
    var items: Item[>..] = [];
    items.push(Item { 1, 0.5 });
    let first: Item& = items.push(Item { 2, 1.5 });
    first.w = 99.0;
    bump(items[0]);
    for i in 100 { items.push(Item { 10 + i as i32, 0.0 }); }
    first.id = 7;
    print(items[0], " ", items[1], " ", items.len);
    let f = find(items, 7);
    guard f else { print("nf"); return; }
    print("found ", f.id, " ", f.w);
    var line: u8[>..] = [];
    reset(line);
    format(line, "x", 5);
    print(line);
    let g = find(items, 12345);
    if g == null { print("absent"); }
}
