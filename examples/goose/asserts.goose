fn main() {
    let xs = [1, 2, 3];
    assert(xs.len == 3);
    var o: i64? = null;
    o .= xs[2];
    assert(o);
    print(o + 1);
}
