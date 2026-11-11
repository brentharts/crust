import std;
import slice_pool;
fn main() {
    var p = slice_pool<i64> {};
    var live: i64[:][..300] = [];
    var r = rng { 11 };
    for step in 2500 {
        let op = r.rand_int(14);
        if op < 5 || live.len == 0 {
            let n = 1 + r.rand_int(4);
            let s = p.alloc_slice(n);
            live.push(s);
            print("alloc ", n, " at ", p.items.range_index(s), " len ", p.items.len);
        } else if op < 9 {
            let k = r.rand_int(live.len);
            p.free_slice(live[k]);
            live[k] = live[live.len - 1];
            live.pop();
        } else if op < 13 {
            let k = r.rand_int(live.len);
            let n = r.rand_int(9);
            let s = p.realloc_slice(live[k], n);
            print("realloc -> ", n, " at ", p.items.range_index(s));
            live[k] = s;
        } else if r.rand_int(6) == 0 {
            live.clear();
            recycle_all(p);
            print("recycled, items ", p.items.len);
        }
    }
    var u = slice_pool<u8> {};
    let a = u.alloc_slice(100);
    let b = u.alloc_slice(100);
    u.free_slice(a);
    print(u.trim(), " ", u.items.len);
    let c = u.alloc_slice(50);
    print(u.items.range_index(c));
}
