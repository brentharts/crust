fn main() {
    for h in 0..4 { let k = match h * 25 { 0 => "z", 1..50 => "low", 50 => "fifty", _ => "other" }; print(h * 25, " ", k); }
    print(1e16, " ", 1e15, " ", 123456789012345678.0, " ", 0.0001, " ", 0.00001, " ", 12345.678);
    let f: f32 = 0.1; print(f, " ", f * 3.0);
    var x: i64 = 9223372036854775807; x += 1; print(x);
    let s: i32 = 1; print(s << 31, " ", -8 >> 1, " ", 1 << 3);
    let r = 7.5 % 2.0; print(r);
    print(5 / 2.0, " ", 10 / 4);
    let big = 18446744073709551615; print(big);
    print(!true, " ", 5 & 3, " ", 5 | 3, " ", 5 ^ 3, " ", ~5);
    print(7 == 7, " ", 2 < 1);
    var n = 0; n++; n--; n += 5; n *= 2; n -= 1; n /= 3; n %= 2; print(n);
}
