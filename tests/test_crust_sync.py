"""Real atomics, `Mutex` / `RwLock` with guards, and `Deref` on user types.

The bundled core's atomics used to be plain reads and writes and its `Mutex`
a pointer accessor, both documented as such because Crust had no threads. They
are now one `lock`-prefixed instruction apiece and a compare-and-swap
spinlock, and the proof that matters is the last class: four real pthreads
hammering one `Mutex` and one atomic, with an unsynchronised control that
loses updates.
"""

import unittest

import shivyc.crust as crust
from tests.test_crust import _run
from tests.test_crust_extras import _rs


class TestAtomics(unittest.TestCase):

    def test_operations_and_their_return_values(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let mut a: AtomicU32 = AtomicU32::new(40);
    let p: u32 = a.fetch_add(2, Ordering::SeqCst);
    let q: u32 = a.swap(7, Ordering::SeqCst);
    let r: u32 = a.compare_exchange(7, 9, Ordering::SeqCst, Ordering::SeqCst);
    let s: u32 = a.compare_exchange(7, 1, Ordering::SeqCst, Ordering::SeqCst);
    a.fetch_or(0x10, Ordering::SeqCst);
    a.fetch_sub(1, Ordering::SeqCst);
    let mut bad = 0;
    if p != 40 { bad += 1; }
    if q != 42 { bad += 2; }
    if r != 7 { bad += 4; }
    if s != 9 { bad += 8; }
    if a.load(Ordering::SeqCst) != 0x18 { bad += 16; }
    bad
}
"""), 0)

    def test_usize_u64_and_bool(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let mut u: AtomicUsize = AtomicUsize::new(1);
    u.fetch_add(4, Ordering::SeqCst);
    let mut w: AtomicU64 = AtomicU64::new(1);
    w.fetch_add(0x100000000, Ordering::SeqCst);
    let mut b: AtomicBool = AtomicBool::new(false);
    b.store(true, Ordering::SeqCst);
    let was: bool = b.swap(false, Ordering::SeqCst);
    let mut bad = 0;
    if u.load(Ordering::SeqCst) != 5 { bad += 1; }
    if w.load(Ordering::SeqCst) != 0x100000001 { bad += 2; }
    if !was { bad += 4; }
    if b.load(Ordering::SeqCst) { bad += 8; }
    bad
}
"""), 0)

    def test_each_operation_is_one_locked_instruction(self):
        c = crust.translate("""
fn f(a: *mut AtomicU32) -> u32 { a.fetch_add(1, Ordering::SeqCst) }
""")
        self.assertIn("lock xaddl", c)

    def test_helpers_are_only_emitted_when_used(self):
        self.assertNotIn("crust_atomic", crust.translate(
            "fn main() -> i32 { 0 }"))


class TestMutex(unittest.TestCase):

    def test_static_mutex_and_a_temporary_guard(self):
        self.assertEqual(_rs("""
static COUNTER: Mutex<i32> = Mutex::new(0);
fn main() -> i32 {
    *COUNTER.lock() += 5;
    *COUNTER.lock() += 2;
    let g = COUNTER.lock();
    *g
}
"""), 7)

    def test_a_temporary_guard_is_released_at_the_end_of_its_statement(self):
        # Three locks in one block: if the first were held to the end of the
        # block the second would spin forever.
        self.assertEqual(_rs("""
fn main() -> i32 {
    let m: Mutex<i32> = Mutex::new(1);
    *m.lock() += 1;
    *m.lock() += 1;
    *m.lock() += 1;
    if m.is_locked() { return 99; }
    *m.lock()
}
"""), 4)

    def test_a_named_guard_holds_until_its_scope_ends(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let m: Mutex<i32> = Mutex::new(0);
    let mut bad = 0;
    {
        let g = m.lock();
        if !m.is_locked() { bad += 1; }
    }
    if m.is_locked() { bad += 2; }
    bad
}
"""), 0)

    def test_guard_reaches_fields_and_methods(self):
        self.assertEqual(_rs("""
struct Stats { hits: i32, size: i32 }
impl Stats {
    fn bump(&mut self) { self.hits += 1; }
    fn total(&self) -> i32 { self.hits * 10 + self.size }
}
fn main() -> i32 {
    let m: Mutex<Stats> = Mutex::new(Stats { hits: 0, size: 3 });
    m.lock().hits += 4;
    m.lock().bump();
    let t = m.lock().total();
    let g = m.lock();
    let h = g.hits;
    t + h
}
"""), 58)

    def test_a_method_through_the_guard_changes_the_real_value(self):
        # The receiver must be the protected value itself, not a copy.
        self.assertEqual(_rs("""
struct C { n: i32 }
impl C { fn inc(&mut self) { self.n += 1; } }
fn main() -> i32 {
    let m: Mutex<C> = Mutex::new(C { n: 0 });
    m.lock().inc();
    m.lock().inc();
    m.lock().n
}
"""), 2)

    def test_array_inside_a_struct_under_a_static_mutex(self):
        self.assertEqual(_rs("""
struct Jobs { slots: [i32; 4], count: i32 }
static JOBS: Mutex<Jobs> = Mutex::new(Jobs { slots: [0; 4], count: 0 });
fn main() -> i32 {
    JOBS.lock().slots[2] = 30;
    JOBS.lock().count += 1;
    let a = JOBS.lock().slots[2];
    let n = JOBS.lock().count;
    a + n
}
"""), 31)

    def test_static_initialiser_takes_the_declared_type(self):
        c = crust.translate("""
static M: Mutex<i64> = Mutex::new(0);
fn f() -> i64 { *M.lock() }
""")
        self.assertIn("Mutex_long", c)
        self.assertNotIn("Mutex_int", c)

    def test_unwrap_is_accepted_for_std_spelling(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let m: Mutex<i32> = Mutex::new(9);
    let g = m.lock().unwrap();
    *g
}
"""), 9)

    def test_early_return_releases_a_named_guard(self):
        self.assertEqual(_rs("""
fn peek(m: &Mutex<i32>, early: bool) -> i32 {
    let g = m.lock();
    if early { return *g; }
    *g + 1
}
fn main() -> i32 {
    let m: Mutex<i32> = Mutex::new(5);
    let a = peek(&m, true);
    let b = peek(&m, false);
    if m.is_locked() { return 99; }
    a * 10 + b
}
"""), 56)


class TestRwLock(unittest.TestCase):

    def test_many_readers_then_a_writer(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let l: RwLock<i32> = RwLock::new(5);
    let mut bad = 0;
    {
        let r1 = l.read();
        let r2 = l.read();
        if *r1 + *r2 != 10 { bad += 1; }
    }
    *l.write() += 4;
    if *l.read() != 9 { bad += 2; }
    bad
}
"""), 0)


class TestUserDeref(unittest.TestCase):
    """`Deref` / `DerefMut` on any type, not just the bundled guards."""

    def test_star_field_method_and_index(self):
        self.assertEqual(_rs("""
struct Pair { a: i32, b: i32 }
impl Pair { fn sum(&self) -> i32 { self.a + self.b } }
struct Handle { target: *mut Pair }
impl Handle {
    fn deref(&self) -> &Pair { unsafe { &*self.target } }
    fn deref_mut(&mut self) -> &mut Pair { unsafe { &mut *self.target } }
}
fn main() -> i32 {
    let mut p = Pair { a: 1, b: 2 };
    let mut h = Handle { target: &mut p };
    h.a = 10;
    h.sum() + h.b
}
"""), 14)


class TestRealThreads(unittest.TestCase):
    """Four pthreads, one lock, one atomic. A C `main` drives the threads
    because Crust has no `thread::spawn`; everything they run is Rust."""

    _DRIVER = """
int pthread_create(unsigned long *, void *, void *(*)(void *), void *);
int pthread_join(unsigned long, void **);

static COUNTER: Mutex<i64> = Mutex::new(0);
static PLAIN: i64 = 0;
static HITS: AtomicU64 = AtomicU64::new(0);

fn rust_worker() {
    let mut i = 0;
    while i < 50000 {
        %(bump)s
        HITS.fetch_add(1, Ordering::SeqCst);
        i += 1;
    }
}

fn check() -> i32 {
    %(read)s
    let hits: u64 = HITS.load(Ordering::SeqCst);
    if hits != 200000 { return 2; }
    if total != 200000 { return 1; }
    0
}

void *thread_main(void *arg) { rust_worker(); return 0; }

int main(void) {
    unsigned long ids[4];
    int k;
    for (k = 0; k < 4; k++) pthread_create(&ids[k], 0, thread_main, 0);
    for (k = 0; k < 4; k++) pthread_join(ids[k], 0);
    return check();
}
"""

    def test_locked_counter_and_atomic_lose_nothing(self):
        src = self._DRIVER % {"bump": "*COUNTER.lock() += 1;",
                              "read": "let total: i64 = *COUNTER.lock();"}
        for _ in range(3):
            self.assertEqual(_run(src, suffix=".c"), 0)

    def test_control_without_the_lock_does_lose_updates(self):
        # If this ever passes, the test above proves nothing.
        src = self._DRIVER % {
            "bump": "unsafe { PLAIN += 1; }",
            "read": "let total: i64 = unsafe { PLAIN };"}
        src = src.replace("static PLAIN: i64 = 0;",
                          "static mut PLAIN: i64 = 0;")
        results = [_run(src, suffix=".c") for _ in range(3)]
        self.assertIn(1, results)


if __name__ == "__main__":
    unittest.main()
