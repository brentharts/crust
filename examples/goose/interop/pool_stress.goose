// Many live slices, then many frees, so the free runs outgrow one leaf (31 runs) several times
// over. The same program runs with the pure Goose span bookkeeping and with the Rust one
// (lib/goose/rust); the two must print the same thing.
import std;
import slice_pool;
fn main() {
    var p = slice_pool<i64> {};
    var live: i64[:][..400] = [];
    var r = rng { 5 };
    var check = 0;
    for round in 4 {
        for i in 300 { live.push(p.alloc_slice(1 + r.rand_int(3))); }
        for i in 200 {                                   // free scattered runs, leaving many gaps
            let k = r.rand_int(live.len);
            p.free_slice(live[k]);
            live[k] = live[live.len - 1];
            live.pop();
        }
        for i in 120 {
            let k = r.rand_int(live.len);
            let s = p.realloc_slice(live[k], r.rand_int(7));
            check = check * 31 + p.items.range_index(s) + s.len;
            live[k] = s;
        }
        print("round ", round, " items ", p.items.len, " check ", check);
        live.clear();
        recycle_all(p);
    }
}
