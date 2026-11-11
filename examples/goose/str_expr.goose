fn show(s: u8[:]) { print("<", s, ">"); }
fn main() {
    show(str("a=", 5, " b=", 2.5));
    var x: i64[>..] = [];
    x.push(1); x.push(2);
    var y = copy(x);
    y.push(4);
    print(copy(y).len, " ", x.len, " ", str(true));
}
