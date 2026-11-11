namespace slice_pool_detail;

// The Rust-backed variant of Crust's slice_pool_internal (see lib/goose/override/ for the pure
// Goose one). The decisions -- first fit, extending the last run, merging neighbours, growing in
// place -- are made by lib/goose/rust/span_runs.rs, called through `extern fn`. Select it with
// GOOSE_LIB=lib/goose/rust and put span_runs.rs in the same C file after the Goose one
// (examples/goose/interop/rust_pool.c).
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


// The free runs as flat (start, count) pairs, which is what span_runs.rs works on. A global
// scratch array holds them across a call; it is rebuilt from the leaves each time, so any
// pool (and recycle_all) can use it.
var runs: i64[>..] = [];

extern fn span_place(r: i64[:], total: i64, length: i64, cnt: i64, new_total: i64&) -> i64;
extern fn span_release(r: i64[:], total: i64, idx: i64, cnt: i64) -> i64;
extern fn span_regrow(r: i64[:], total: i64, length: i64, idx: i64, old: i64, cnt: i64,
                      new_total: i64&) -> i64;

// Copies the runs out of the leaves; the scratch array gets room for two more runs.
fn gather(nodes: node[>..]&, t: tree&) -> i64 {
    let total = run_total(nodes, t);
    while runs.len < 2 * (total + 2) { runs.push(0); }
    var at = 0;
    var l = t.root;
    while l != 0 {
        for k in nodes[l].n {
            runs[at] = nodes[l].data[k];
            runs[at + 1] = nodes[l].data[PER_LEAF + k];
            at += 2;
        }
        l = nodes[l].link;
    }
    return total;
}

// Writes `total` runs from the scratch array back into the leaves.
fn scatter(nodes: node[>..]&, t: tree&, total: i64) {
    shape(nodes, t, total);
    var at = 0;
    var l = t.root;
    while l != 0 {
        for k in nodes[l].n {
            nodes[l].data[k] = runs[at];
            nodes[l].data[PER_LEAF + k] = runs[at + 1];
            at += 2;
        }
        l = nodes[l].link;
    }
}

fn check(total: i64) {
    if total < 0 { abort("slice_pool: span tree is corrupt"); }
}

fn place(nodes: node[>..]&, t: tree&, length: i64, cnt: i64) -> i64 {
    if cnt == 0 || nodes.len == 0 { return length; }
    let total = gather(nodes, t);
    var now = 0;
    let start = span_place(runs[..], total, length, cnt, now);
    check(now);
    scatter(nodes, t, now);
    return start;
}

fn release_run(nodes: node[>..]&, t: tree&, idx: i64, cnt: i64) {
    if cnt <= 0 { return; }
    start_up(nodes, t);
    let total = gather(nodes, t);
    let now = span_release(runs[..], total, idx, cnt);
    check(now);
    scatter(nodes, t, now);
}

fn regrow(nodes: node[>..]&, t: tree&, length: i64, idx: i64, old: i64, cnt: i64) -> i64 {
    if nodes.len == 0 {
        release_run(nodes, t, idx, old);
        return place(nodes, t, length, cnt);
    }
    let total = gather(nodes, t);
    var now = 0;
    let start = span_regrow(runs[..], total, length, idx, old, cnt, now);
    check(now);
    scatter(nodes, t, now);
    return start;
}

// As in the pure Goose version: nothing a program can observe depends on handing pages back.
fn trim_pages(nodes: node[>..]&, t: tree&, items: u8[:], elem: i64) -> bool { return true; }
