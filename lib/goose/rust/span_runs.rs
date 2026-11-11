// span_runs.rs -- first-fit, coalescing bookkeeping for the free runs of a slice_pool.
//
// This is the same job as the block list in examples/rpython2c/mbos/alloc.rs, turned from
// "blocks of a byte arena" into "runs of pool cells": the free runs are held in address order,
// none touching another, in one flat array of (start, count) pairs, run i at p[2i], p[2i+1].
// Goose's slice_pool calls these through `extern fn` (lib/goose/rust/slice_pool_internal.goose);
// the caller owns the array and its length `total`, which each function returns or updates.
//
//   place    first fit; else extend the run that ends at the end of the pool; else `length`
//   release  put a run back, merged with a free neighbour on either side
//   regrow   grow a run in place when the free run after it allows, else release and place
//
// The array must have room for two more cells than total pairs need (cap >= 2 * (total + 1)).
// A function that cannot honour that sets *new_total to -1.

fn get(p: *mut i64, i: i64) -> i64 {
    unsafe { return *p.add(i as usize); }
}

fn put(p: *mut i64, i: i64, v: i64) {
    unsafe { *p.add(i as usize) = v; }
}

fn start_of(p: *mut i64, k: i64) -> i64 {
    return get(p, 2 * k);
}

fn count_of(p: *mut i64, k: i64) -> i64 {
    return get(p, 2 * k + 1);
}

// Run k becomes (start, count); the runs from k on move up one place.
fn insert_at(p: *mut i64, total: i64, k: i64, start: i64, count: i64) {
    let mut j: i64 = total;
    while j > k {
        put(p, 2 * j, start_of(p, j - 1));
        put(p, 2 * j + 1, count_of(p, j - 1));
        j -= 1;
    }
    put(p, 2 * k, start);
    put(p, 2 * k + 1, count);
}

fn remove_at(p: *mut i64, total: i64, k: i64) {
    let mut j: i64 = k;
    while j + 1 < total {
        put(p, 2 * j, start_of(p, j + 1));
        put(p, 2 * j + 1, count_of(p, j + 1));
        j += 1;
    }
}

// Takes `count` cells from the front of run k; a run that is used up goes away.
// Returns the new number of runs.
fn take(p: *mut i64, total: i64, k: i64, count: i64) -> i64 {
    let c: i64 = count_of(p, k);
    if c <= count {
        remove_at(p, total, k);
        return total - 1;
    }
    put(p, 2 * k, start_of(p, k) + count);
    put(p, 2 * k + 1, c - count);
    return total;
}

// How many runs start before `start`: where a run at `start` belongs.
fn position(p: *mut i64, total: i64, start: i64) -> i64 {
    let mut k: i64 = 0;
    while k < total && start_of(p, k) < start {
        k += 1;
    }
    return k;
}

fn put_back(p: *mut i64, total: i64, idx: i64, cnt: i64) -> i64 {
    let k: i64 = position(p, total, idx);
    let before: bool = k > 0 && start_of(p, k - 1) + count_of(p, k - 1) == idx;
    let after: bool = k < total && idx + cnt == start_of(p, k);
    if before {
        let mut n: i64 = count_of(p, k - 1) + cnt;
        if after {
            n += count_of(p, k);
            remove_at(p, total, k);
            put(p, 2 * (k - 1) + 1, n);
            return total - 1;
        }
        put(p, 2 * (k - 1) + 1, n);
        return total;
    }
    if after {
        put(p, 2 * k, idx);
        put(p, 2 * k + 1, count_of(p, k) + cnt);
        return total;
    }
    insert_at(p, total, k, idx, cnt);
    return total + 1;
}

fn place_run(p: *mut i64, total: i64, length: i64, cnt: i64, out: *mut i64) -> i64 {
    let mut k: i64 = 0;
    while k < total && count_of(p, k) < cnt {
        k += 1;
    }
    if k < total {
        let start: i64 = start_of(p, k);
        unsafe { *out = take(p, total, k, cnt); }
        return start;
    }
    // No run is big enough. The last one may touch the end of the pool, and then the
    // allocation starts there and runs past the end.
    unsafe { *out = total; }
    if total == 0 {
        return length;
    }
    let last: i64 = total - 1;
    if start_of(p, last) + count_of(p, last) != length {
        return length;
    }
    let start: i64 = start_of(p, last);
    unsafe { *out = take(p, total, last, cnt); }
    return start;
}

pub extern "C" fn span_place(p: *mut i64, cap: i64, total: i64, length: i64, cnt: i64, new_total: *mut i64) -> i64 {
    unsafe { *new_total = total; }
    if cnt == 0 || cap < 2 * (total + 1) {
        if cnt != 0 {
            unsafe { *new_total = -1; }
        }
        return length;
    }
    return place_run(p, total, length, cnt, new_total);
}

pub extern "C" fn span_release(p: *mut i64, cap: i64, total: i64, idx: i64, cnt: i64) -> i64 {
    if cnt <= 0 {
        return total;
    }
    if cap < 2 * (total + 2) {
        return -1;
    }
    return put_back(p, total, idx, cnt);
}

pub extern "C" fn span_regrow(p: *mut i64, cap: i64, total: i64, length: i64, idx: i64, old: i64, cnt: i64, new_total: *mut i64) -> i64 {
    unsafe { *new_total = total; }
    if cap < 2 * (total + 2) {
        unsafe { *new_total = -1; }
        return length;
    }
    let end: i64 = idx + old;
    let k: i64 = position(p, total, end);      // the first run at or after the end of the old one
    if k < total && start_of(p, k) == end && (count_of(p, k) >= cnt - old || end + count_of(p, k) == length) {
        unsafe { *new_total = take(p, total, k, cnt - old); }
        return idx;
    }
    let t: i64 = put_back(p, total, idx, old);
    return place_run(p, t, length, cnt, new_total);
}
