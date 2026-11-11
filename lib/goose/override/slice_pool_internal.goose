namespace slice_pool_detail;

// Crust's replacement for Goose's slice_pool_internal: the same four functions
// (place, release_run, regrow, trim_pages) with the same results, written in Goose
// instead of calling Goose's C span tree (gs_slice_pool_*).
//
// What slice_pool needs from them is small and plain. The free runs of the pool
// are kept in address order. place gives the lowest-addressed run that is big
// enough (first fit), or, when none is, extends the run that ends exactly at the
// end of the pool, or else says "append at the end". release_run puts a run back
// and merges it with a free neighbour on either side. regrow grows a run in place
// when the free run after it allows, and otherwise releases it and places it anew.
// Goose's own version indexes the runs with a B+ tree so that all of this is
// logarithmic; here the runs are one sorted list, so a scan is linear in the number
// of free runs. The results are the same.
//
// Storage, chosen so that slice_pool's own recycle_all (which writes it directly)
// stays valid: `nodes` is an array of 512-byte nodes and node 0 stays zero. The
// runs are held densely, in order, in leaves chained through `link` from the leaf
// `tree.root` names: a leaf holds up to 31 runs, run k's start in data[k] and its
// count in data[31 + k], and `n` says how many it holds. `tree.chain` heads a chain
// (again through `link`) of nodes that are free to become leaves.

struct node { n: i64, link: i64, data: i64[62] }
struct tree { root: i64, height: i64, chain: i64 }

let PER_LEAF = 31;

// The j-th leaf of the chain.
fn leaf_of(nodes: node[>..]&, t: tree&, j: i64) -> i64 {
    var l = t.root;
    var left = j;
    while left > 0 {
        l = nodes[l].link;
        left -= 1;
    }
    return l;
}

fn run_start(nodes: node[>..]&, t: tree&, i: i64) -> i64 { nodes[leaf_of(nodes, t, i / PER_LEAF)].data[i % PER_LEAF] }
fn run_count(nodes: node[>..]&, t: tree&, i: i64) -> i64 { nodes[leaf_of(nodes, t, i / PER_LEAF)].data[PER_LEAF + i % PER_LEAF] }
fn set_run(nodes: node[>..]&, t: tree&, i: i64, start: i64, count: i64) {
    let l = leaf_of(nodes, t, i / PER_LEAF);
    nodes[l].data[i % PER_LEAF] = start;
    nodes[l].data[PER_LEAF + i % PER_LEAF] = count;
}

fn run_total(nodes: node[>..]&, t: tree&) -> i64 {
    var total = 0;
    var l = t.root;
    while l != 0 {
        total += nodes[l].n;
        l = nodes[l].link;
    }
    return total;
}

// The first free of a pool sets up node 0 and the first leaf, as Goose's does.
fn start_up(nodes: node[>..]&, t: tree&) {
    if nodes.len == 0 {
        nodes.push(node { n: 0, link: 0, data: [0; 62] });
        t.chain = 0;
        t.height = 0;
        nodes.push(node { n: 0, link: 0, data: [0; 62] });
        t.root = 1;
    }
}

// A node for a new leaf: from the free chain, else a new one.
fn new_leaf(nodes: node[>..]&, t: tree&) -> i64 {
    if t.chain != 0 {
        let l = t.chain;
        t.chain = nodes[l].link;
        nodes[l] = node { n: 0, link: 0, data: [0; 62] };
        return l;
    }
    nodes.push(node { n: 0, link: 0, data: [0; 62] });
    return nodes.len - 1;
}

// Makes the chain exactly long enough for `total` runs (at least one leaf), gives the
// spare leaves back to the free chain, and sets every leaf's count.
fn shape(nodes: node[>..]&, t: tree&, total: i64) {
    var want = (total + PER_LEAF - 1) / PER_LEAF;
    if want < 1 { want = 1; }
    var have = 1;
    var last = t.root;
    while nodes[last].link != 0 && have < want {
        last = nodes[last].link;
        have += 1;
    }
    while have < want {
        let l = new_leaf(nodes, t);
        nodes[last].link = l;
        last = l;
        have += 1;
    }
    var spare = nodes[last].link;
    nodes[last].link = 0;
    while spare != 0 {
        let next = nodes[spare].link;
        nodes[spare].n = 0;
        nodes[spare].link = t.chain;
        t.chain = spare;
        spare = next;
    }
    var l = t.root;
    var left = total;
    while l != 0 {
        nodes[l].n = if left > PER_LEAF { PER_LEAF } else { left };
        left -= PER_LEAF;
        l = nodes[l].link;
    }
}

// Run k becomes (start, count), the runs from k on moving up one place.
fn insert_run(nodes: node[>..]&, t: tree&, total: i64, k: i64, start: i64, count: i64) -> i64 {
    shape(nodes, t, total + 1);                 // room for one more first; the counts are set again below
    var j = total;
    while j > k {
        set_run(nodes, t, j, run_start(nodes, t, j - 1), run_count(nodes, t, j - 1));
        j -= 1;
    }
    set_run(nodes, t, k, start, count);
    return total + 1;
}

fn remove_run(nodes: node[>..]&, t: tree&, total: i64, k: i64) -> i64 {
    var j = k;
    while j + 1 < total {
        set_run(nodes, t, j, run_start(nodes, t, j + 1), run_count(nodes, t, j + 1));
        j += 1;
    }
    shape(nodes, t, total - 1);
    return total - 1;
}

// Takes `count` cells from the front of run k; a run that is used up goes away.
fn take(nodes: node[>..]&, t: tree&, total: i64, k: i64, count: i64) -> i64 {
    let c = run_count(nodes, t, k);
    if c <= count { return remove_run(nodes, t, total, k); }
    set_run(nodes, t, k, run_start(nodes, t, k) + count, c - count);
    return total;
}

// The number of runs that start before `start`: where a run at `start` belongs.
fn position(nodes: node[>..]&, t: tree&, total: i64, start: i64) -> i64 {
    var k = 0;
    while k < total && run_start(nodes, t, k) < start { k += 1; }
    return k;
}

fn place(nodes: node[>..]&, t: tree&, length: i64, cnt: i64) -> i64 {
    if cnt == 0 || nodes.len == 0 { return length; }
    let total = run_total(nodes, t);
    var k = 0;
    while k < total && run_count(nodes, t, k) < cnt { k += 1; }
    if k < total {
        let start = run_start(nodes, t, k);
        take(nodes, t, total, k, cnt);
        return start;
    }
    // No run is big enough. The last one may touch the end of the pool, and then the
    // allocation starts there and runs past the end.
    if total == 0 { return length; }
    let last = total - 1;
    if run_start(nodes, t, last) + run_count(nodes, t, last) != length { return length; }
    let start = run_start(nodes, t, last);
    take(nodes, t, total, last, cnt);
    return start;
}

// Puts [idx, idx + cnt) back, merged with the free run before and after it.
fn put_back(nodes: node[>..]&, t: tree&, idx: i64, cnt: i64) {
    start_up(nodes, t);
    let total = run_total(nodes, t);
    let k = position(nodes, t, total, idx);           // the run after idx is k, the one before is k - 1
    let joins_before = k > 0 && run_start(nodes, t, k - 1) + run_count(nodes, t, k - 1) == idx;
    let joins_after = k < total && idx + cnt == run_start(nodes, t, k);
    if joins_before {
        var n = run_count(nodes, t, k - 1) + cnt;
        if joins_after {
            n += run_count(nodes, t, k);
            remove_run(nodes, t, total, k);
        }
        set_run(nodes, t, k - 1, run_start(nodes, t, k - 1), n);
    } else if joins_after {
        set_run(nodes, t, k, idx, run_count(nodes, t, k) + cnt);
    } else {
        insert_run(nodes, t, total, k, idx, cnt);
    }
}

fn release_run(nodes: node[>..]&, t: tree&, idx: i64, cnt: i64) {
    if cnt <= 0 { return; }
    put_back(nodes, t, idx, cnt);
}

fn regrow(nodes: node[>..]&, t: tree&, length: i64, idx: i64, old: i64, cnt: i64) -> i64 {
    if nodes.len == 0 {
        put_back(nodes, t, idx, old);
        return place(nodes, t, length, cnt);
    }
    let total = run_total(nodes, t);
    let end = idx + old;
    let k = position(nodes, t, total, end);          // the first run at or after the end of the old one
    if k < total && run_start(nodes, t, k) == end &&
       (run_count(nodes, t, k) >= cnt - old || end + run_count(nodes, t, k) == length) {
        take(nodes, t, total, k, cnt - old);
        return idx;
    }
    put_back(nodes, t, idx, old);
    return place(nodes, t, length, cnt);
}

// Goose's version hands whole free pages back to the operating system. Nothing in a
// program can observe that except by reading a freed slice, so here it does nothing.
fn trim_pages(nodes: node[>..]&, t: tree&, items: u8[:], elem: i64) -> bool { return true; }
