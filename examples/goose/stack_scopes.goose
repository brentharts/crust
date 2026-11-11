fn find(n: i64) -> i64 {
    var acc: i64[>..] = [];
    for i in 100 {
        var tmp: i64[>..] = [];
        for j in i { tmp.push(j); }
        if tmp.len == n { return i; }
        if i % 2 == 0 { continue; }
        acc.push(tmp.len);
        if acc.len > 50 { break; }
    }
    return acc.len;
}
fn main() {
    print(find(7), " ", find(1000));
    var total = 0;
    for r in 1000 { total += find(3); }
    print(total);
    let v = block {
        var t: u8[>..] = [];
        t.push(1);
        guard t.len > 5 else { break t.len; }
        99
    };
    print(v);
}
