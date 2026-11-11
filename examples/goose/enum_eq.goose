enum Op { Push { v: i64 }, Add, Jump { to: i32, cond: bool } }
fn main() {
    let a = Op.Push { v: 5 };
    print(a);
    let b: Op = Op.Add;
    print(b);
    var v: Op[>..] = [];
    v.push(Op.Add); v.push(Op.Jump { to: 3, cond: true });
    print(v);
    print(v[1] == v[1]);
}
