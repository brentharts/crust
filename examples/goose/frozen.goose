fn describe(x: i64) -> u8[] {
    var b: u8[>..] = [];
    format(b, "n=", x);
    return b[..];
}
fn main() {
    let frozen: i64[] = [7, 8, 9];
    let s: u8[] = "hello";
    print(frozen, " ", frozen.len, " ", s, " ", frozen[1], " ", frozen == frozen);
    var total = 0;
    for x in frozen { total += x; }
    print(total);
    let d = describe(5);
    let e = describe(77);
    print(d, " ", e);
}
