fn each_pair<T, F>(xs: T[:]) {
    var i = 0;
    while i + 1 < xs.len {
        F(xs[i], xs[i + 1]);
        i += 1;
    }
}

fn any_of<T, F>(xs: T[:]) -> bool {
    for x in xs { if F(x) { return true; } }
    return false;
}

fn fold<T, A, F>(xs: T[:], acc: A) -> A {
    var a = acc;
    for x in xs { a = F(a, x); }
    return a;
}

fn count_if<T, F>(xs: T[:]) -> i64 {
    var n = 0;
    for x in xs { if F(x) { n += 1; } }
    return n;
}

fn first_multiple(xs: i64[:], k: i64) -> i64 {
    xs.each_pair() { a, b => if a % k == 0 { return a; } };
    return -1;
}

fn bump<T, F>(xs: T[:]) {
    for &x in xs { F(x); }
}

fn main() {
    let xs = [3, 1, 4, 1, 5, 9, 2, 6];
    var total = 0;
    xs.each_pair() { a, b => total += a * b; };
    print(total);
    print(xs.any_of() { it > 8 }, xs.any_of() { it > 9 });
    print(xs.fold(0) { acc, x => acc + x });
    print(xs.count_if() { it % 2 == 0 });
    print(first_multiple(xs, 4), first_multiple(xs, 7));
    var ys = [1, 2, 3];
    ys.bump() { it += 10; };
    print(ys);
}
