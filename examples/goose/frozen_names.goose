fn tag(i: i64) -> u8[] {
    var b: u8[>..] = [];
    format(b, "item", i);
    return b[..];
}
fn fill(names: u8[][>..]&, n: i64) {
    for i in n { names.push(tag(i)); }
}
fn main() {
    var names: u8[][>..] = [];
    names.push("zero");
    names.push(tag(1));
    fill(names, 3);
    print(names, " ", names.len);
    for n in names { print(n); }
}
