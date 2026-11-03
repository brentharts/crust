// Crust's minimal `core` -- the handful of standard generic types that real
// Rust code cannot be read without.
//
// Crust monomorphises from source, so a generic with no template in the unit
// cannot be instantiated. Redox (and any real Rust) reaches constantly for
// `Vec<T>`, `Box<T>` and friends, which live in `alloc`/`core` and are written
// in a dialect far outside this subset -- traits, intrinsics, `unsafe`
// pointer arithmetic, allocator plumbing. So rather than trying to compile the
// real ones, Crust ships small honest equivalents *written in the Crust
// subset itself*, and seeds every translation unit with them.
//
// This is a reimplementation, not the standard library. What is here matches
// the shape and the common methods of the real types, so ordinary code reads
// and compiles the same way. What is not here -- iterators, a user-defined
// `Drop` trait, bounds checking, thread safety -- is absent rather than
// faked. Scope exit does call `free_buf` / `free_box` for by-value locals of
// these types (see `shivyc/crust.py`); the methods remain callable explicitly
// too. A local definition of the same name always wins, so a unit that brings
// its own `Vec` is unaffected by any of this.
//
// Instantiation is demand-driven, so a unit that never mentions `Vec<T>` pays
// nothing for these templates: they are tokens in a table until something
// asks for one.

// ---------------------------------------------------------------------------
// Vec<T> -- a growable array.
//
// The layout is the same three words the real `Vec` uses (pointer, length,
// capacity), so it stays an ordinary C struct that C code can read directly.
// There is no bounds checking: `get` on an out-of-range index is undefined,
// exactly as indexing a raw C array would be. Scope exit frees the buffer by
// calling `free_buf`; the method is also available for an earlier release.
// ---------------------------------------------------------------------------
struct Vec<T> {
    ptr: *mut T,
    len: usize,
    cap: usize,
}

impl<T> Vec<T> {
    fn new() -> Vec<T> {
        Vec { ptr: 0 as *mut T, len: 0, cap: 0 }
    }

    fn with_capacity(cap: usize) -> Vec<T> {
        let mut v: Vec<T> = Vec::<T>::new();
        v.reserve(cap);
        v
    }

    fn len(&self) -> usize {
        self.len
    }

    fn is_empty(&self) -> bool {
        self.len == 0
    }

    fn capacity(&self) -> usize {
        self.cap
    }

    // Grow to hold at least `want` elements, doubling so that repeated
    // `push` stays amortised O(1).
    fn reserve(&mut self, want: usize) {
        if want <= self.cap {
            return;
        }
        let mut cap: usize = self.cap;
        if cap == 0 {
            cap = 4;
        }
        while cap < want {
            cap = cap * 2;
        }
        self.ptr = realloc(self.ptr as *mut u8,
                           cap * size_of::<T>()) as *mut T;
        self.cap = cap;
    }

    fn push(&mut self, value: T) {
        self.reserve(self.len + 1);
        self.ptr[self.len] = value;
        self.len += 1;
    }

    fn get(&self, i: usize) -> T {
        self.ptr[i]
    }

    // The replaced element is dropped first, as `v[i] = x` does in Rust.
    // `drop_at` and `drop_elems` are not written here: they have to name
    // `T`'s destructor, which a generic body cannot, so the compiler
    // generates them per instantiation (empty when `T` owns nothing).
    fn set(&mut self, i: usize, value: T) {
        self.drop_at(i);
        self.ptr[i] = value;
    }

    fn last(&self) -> T {
        self.ptr[self.len - 1]
    }

    fn pop(&mut self) -> T {
        self.len -= 1;
        self.ptr[self.len]
    }

    fn clear(&mut self) {
        self.drop_elems();
        self.len = 0;
    }

    fn as_ptr(&self) -> *mut T {
        self.ptr
    }

    // Also invoked automatically at scope exit for by-value locals. The
    // elements are dropped first: the Vec owns them.
    fn free_buf(&mut self) {
        self.drop_elems();
        free(self.ptr as *mut u8);
        self.ptr = 0 as *mut T;
        self.len = 0;
        self.cap = 0;
    }
}

// ---------------------------------------------------------------------------
// PyList<T> -- the list an included rpython module builds.
//
// `tools/py2c.py` lowers a typed rpython list to
//
//     typedef struct _tlist_int { int* data; long len; long cap; } _tlist_int;
//
// which is the same three words in the same order as this type. That is the
// whole point: an rpython function returning `list[int]` hands back something
// Rust can walk directly, with a pointer cast and no copy.
//
// This is how Crust gets iteration over a built-up collection without having
// an iterator protocol. RPython already has one, and its output is a plain
// struct, so `for x in xs` over the result needs nothing new -- see
// `examples/crust/polylist.c`.
//
// The layout must not drift from py2c's. `len` and `cap` are `i64` rather
// than `usize` because py2c emits `long`, and a mismatch here would be a
// silent misread rather than a compile error.
// ---------------------------------------------------------------------------
struct PyList<T> {
    data: *mut T,
    len: i64,
    cap: i64,
}

impl<T> PyList<T> {
    fn len(&self) -> i64 {
        self.len
    }

    fn is_empty(&self) -> bool {
        self.len == 0
    }

    fn capacity(&self) -> i64 {
        self.cap
    }

    fn get(&self, i: i64) -> T {
        self.data[i]
    }

    fn set(&mut self, i: i64, value: T) {
        self.data[i] = value;
    }

    fn last(&self) -> T {
        self.data[self.len - 1]
    }

    fn as_ptr(&self) -> *mut T {
        self.data
    }

    // The buffer belongs to the rpython runtime, which allocated it with
    // malloc; freeing it here is correct but must not be done twice.
    fn free_buf(&mut self) {
        free(self.data as *mut u8);
        self.data = 0 as *mut T;
        self.len = 0;
        self.cap = 0;
    }
}

// ---------------------------------------------------------------------------
// Box<T> -- a single heap-allocated value.
//
// A thin wrapper: `new` allocates, `get` reads, `free_box` releases (also at
// scope exit for by-value locals). It exists mainly so that a signature
// written `Box<T>` in real code has something to resolve to.
// ---------------------------------------------------------------------------
struct Box<T> {
    ptr: *mut T,
}

impl<T> Box<T> {
    fn new(value: T) -> Box<T> {
        let p: *mut T = malloc(size_of::<T>()) as *mut T;
        p[0] = value;
        Box { ptr: p }
    }

    fn get(&self) -> T {
        self.ptr[0]
    }

    fn set(&mut self, value: T) {
        self.ptr[0] = value;
    }

    fn as_ptr(&self) -> *mut T {
        self.ptr
    }

    fn free_box(&mut self) {
        self.drop_elems();
        free(self.ptr as *mut u8);
        self.ptr = 0 as *mut T;
    }
}

// ---------------------------------------------------------------------------
// Cell<T> -- a value in a struct field that is written through a shared
// reference. Crust has no borrow checker, so this carries no interior
// mutability machinery; it is a named box for a value, present so code
// spelled with it resolves.
// ---------------------------------------------------------------------------
struct Cell<T> {
    value: T,
}

impl<T> Cell<T> {
    fn new(value: T) -> Cell<T> {
        Cell { value: value }
    }

    fn get(&self) -> T {
        self.value
    }

    fn set(&mut self, value: T) {
        self.value = value;
    }
}

// ---------------------------------------------------------------------------
// UnsafeCell<T> / SyncUnsafeCell<T> -- interior mutability.
//
// These are faithful. In real Rust `UnsafeCell<T>` is literally a struct with
// one field; all it does is tell the *compiler* that aliasing rules do not
// apply to its contents. Crust has no aliasing rules to suspend, so the
// wrapper carries exactly the same information here: none.
// ---------------------------------------------------------------------------
struct UnsafeCell<T> {
    value: T,
}

impl<T> UnsafeCell<T> {
    fn new(value: T) -> UnsafeCell<T> {
        UnsafeCell { value: value }
    }

    fn get(&self) -> *mut T {
        &self.value as *mut T
    }

    fn read(&self) -> T {
        self.value
    }

    fn write(&mut self, value: T) {
        self.value = value;
    }
}

struct SyncUnsafeCell<T> {
    value: T,
}

impl<T> SyncUnsafeCell<T> {
    fn new(value: T) -> SyncUnsafeCell<T> {
        SyncUnsafeCell { value: value }
    }

    fn get(&self) -> *mut T {
        &self.value as *mut T
    }

    fn read(&self) -> T {
        self.value
    }

    fn write(&mut self, value: T) {
        self.value = value;
    }
}

// ---------------------------------------------------------------------------
// NonNull<T> -- a pointer asserted not to be null.
//
// Also faithful: in real Rust the guarantee is a *niche* for layout
// optimisation and a promise the programmer makes, not a runtime check.
// `is_null` is provided so code that wants the check can make it.
// ---------------------------------------------------------------------------
struct NonNull<T> {
    ptr: *mut T,
}

impl<T> NonNull<T> {
    fn new(ptr: *mut T) -> NonNull<T> {
        NonNull { ptr: ptr }
    }

    fn as_ptr(&self) -> *mut T {
        self.ptr
    }

    fn read(&self) -> T {
        self.ptr[0]
    }

    fn write(&mut self, value: T) {
        self.ptr[0] = value;
    }

    fn is_null(&self) -> bool {
        self.ptr == 0 as *mut T
    }
}

// ---------------------------------------------------------------------------
// Once<T> -- run-once initialisation.
//
// Faithful for a single-threaded caller: the flag records whether the value
// has been produced. What is missing is the *blocking* half -- a real `Once`
// makes a second thread wait for the first to finish. There are no threads
// here, so there is nothing to wait for.
// ---------------------------------------------------------------------------
struct Once<T> {
    value: T,
    done: bool,
}

impl<T> Once<T> {
    fn new(zero: T) -> Once<T> {
        Once { value: zero, done: false }
    }

    fn is_completed(&self) -> bool {
        self.done
    }

    fn call_once(&mut self, value: T) -> T {
        if !self.done {
            self.value = value;
            self.done = true;
        }
        self.value
    }

    fn get(&self) -> T {
        self.value
    }
}

// ---------------------------------------------------------------------------
// Mutex<T> / RwLock<T> -- spinlocks over the bundled atomics.
//
// `lock()` spins on a compare-and-swap until it owns the lock and returns a
// guard; the guard dereferences to the protected value (`*m.lock() += 1`,
// `m.lock().field`, `m.lock()[i]`) and releases the lock when it is dropped,
// which is at the end of its scope -- or, for a guard that was only a
// temporary, at the end of its statement. The lock state is a field after
// `value`, so a zero-initialised or `{ value }`-initialised mutex is
// unlocked, and `static M: Mutex<T> = Mutex::new(v)` is a constant.
//
// A spinlock is the right primitive for the code this core targets (kernels
// and runtimes with no scheduler to park on). It does not block: a thread
// that holds one for long wastes the others' time, and locking twice on one
// thread deadlocks, exactly as with `spin::Mutex`. There is no poisoning, so
// `lock().unwrap()` is accepted and returns the guard itself.
// ---------------------------------------------------------------------------
struct Mutex<T> {
    value: T,
    state: u32,
}

impl<T> Mutex<T> {
    fn new(value: T) -> Mutex<T> {
        Mutex { value: value, state: 0 }
    }

    fn lock(&self) -> MutexGuard<T> {
        let state = &self.state as *mut u32;
        while crust_atomic_cas32(state, 0, 1) != 0 {
            while crust_atomic_load32(state) != 0 {
                crust_atomic_pause();
            }
        }
        MutexGuard { ptr: &self.value as *mut T, state: state }
    }

    fn is_locked(&self) -> bool {
        crust_atomic_load32(&self.state as *mut u32) != 0
    }

    fn into_inner(self) -> T {
        self.value
    }

    fn read(&self) -> T {
        self.value
    }

    fn write(&mut self, value: T) {
        self.value = value;
    }
}

struct RwLock<T> {
    value: T,
    state: u32,
}

impl<T> RwLock<T> {
    fn new(value: T) -> RwLock<T> {
        RwLock { value: value, state: 0 }
    }

    // Many readers or one writer. State: 0 free, 1.. readers, 0x80000000 a
    // writer. A reader waits for a writer to leave; a writer waits for all.
    fn read(&self) -> RwLockReadGuard<T> {
        let state = &self.state as *mut u32;
        loop {
            let seen: u32 = crust_atomic_load32(state);
            if seen < 0x80000000 {
                if crust_atomic_cas32(state, seen, seen + 1) == seen {
                    break;
                }
            }
            crust_atomic_pause();
        }
        RwLockReadGuard { ptr: &self.value as *mut T, state: state }
    }

    fn write(&self) -> RwLockWriteGuard<T> {
        let state = &self.state as *mut u32;
        while crust_atomic_cas32(state, 0, 0x80000000) != 0 {
            crust_atomic_pause();
        }
        RwLockWriteGuard { ptr: &self.value as *mut T, state: state }
    }
}

// ---------------------------------------------------------------------------
// Formatter -- the sink `write!` writes into, and what `fn fmt` receives.
//
// Real Rust's `fmt::Formatter` wraps a `fmt::Write` implementation and can
// grow. Crust has no `String`, and a kernel formatter writes to a console or
// a serial port rather than to a heap buffer, so this one is bounded: the
// caller supplies the storage and the formatter never allocates.
//
// A write that would overflow is truncated rather than growing or faulting,
// and `overflowed` records that it happened. That is the behaviour a kernel
// log wants -- losing the tail of a message is much better than a panic
// inside the panic handler.
// ---------------------------------------------------------------------------
struct Formatter {
    buf: *mut c_char,
    cap: i64,
    len: i64,
    overflowed: bool,
}

impl Formatter {
    fn new(buf: *mut c_char, cap: i64) -> Formatter {
        let mut f: Formatter = Formatter { buf: buf, cap: cap, len: 0,
                                           overflowed: false };
        f.clear();
        f
    }

    fn clear(&mut self) {
        self.len = 0;
        self.overflowed = false;
        if self.cap > 0 {
            self.buf[0] = 0 as c_char;
        }
    }

    fn len(&self) -> i64 {
        self.len
    }

    fn is_empty(&self) -> bool {
        self.len == 0
    }

    fn overflowed(&self) -> bool {
        self.overflowed
    }

    fn as_str(&self) -> *mut c_char {
        self.buf
    }

    // Room left, excluding the terminating NUL.
    fn space(&self) -> i64 {
        if self.cap <= self.len + 1 {
            return 0;
        }
        self.cap - self.len - 1
    }

    fn write_str(&mut self, s: *const c_char) -> i32 {
        let mut i: i64 = 0;
        while s[i] != 0 as c_char {
            if self.len + 1 >= self.cap {
                self.overflowed = true;
                return -1;
            }
            self.buf[self.len] = s[i];
            self.len += 1;
            i += 1;
        }
        if self.cap > 0 {
            self.buf[self.len] = 0 as c_char;
        }
        0
    }

    fn write_char(&mut self, c: c_char) -> i32 {
        if self.len + 1 >= self.cap {
            self.overflowed = true;
            return -1;
        }
        self.buf[self.len] = c;
        self.len += 1;
        self.buf[self.len] = 0 as c_char;
        0
    }
}

// ---------------------------------------------------------------------------
// Arc<T> / Rc<T> -- shared ownership, without the ownership.
//
// These count references and free at zero. What they do *not* do is any of
// the automatic part: Rust drops a clone at end of scope, and Crust has no
// `Drop`, so every `clone` must be matched by an explicit `release`. An
// unmatched clone leaks rather than corrupting anything, which is the right
// direction to fail.
//
// `Arc` is not atomic. It is `Rc` with a different name, because Crust has no
// threads for its refcount to race with -- the same reasoning as `Mutex`, and
// it stops holding the moment real concurrency exists.
// ---------------------------------------------------------------------------
struct Rc<T> {
    ptr: *mut T,
    count: *mut i64,
}

impl<T> Rc<T> {
    fn new(value: T) -> Rc<T> {
        let p: *mut T = malloc(size_of::<T>()) as *mut T;
        p[0] = value;
        let c: *mut i64 = malloc(size_of::<i64>()) as *mut i64;
        c[0] = 1;
        Rc { ptr: p, count: c }
    }

    fn get(&self) -> T {
        self.ptr[0]
    }

    fn set(&mut self, value: T) {
        self.ptr[0] = value;
    }

    fn as_ptr(&self) -> *mut T {
        self.ptr
    }

    fn strong_count(&self) -> i64 {
        self.count[0]
    }

    fn clone(&self) -> Rc<T> {
        self.count[0] += 1;
        Rc { ptr: self.ptr, count: self.count }
    }

    // Explicit for `Rc`/`Arc`: scope-exit Drop does not auto-release clones.
    fn release(&mut self) {
        self.count[0] -= 1;
        if self.count[0] <= 0 {
            free(self.ptr as *mut u8);
            free(self.count as *mut u8);
            self.ptr = 0 as *mut T;
            self.count = 0 as *mut i64;
        }
    }
}

struct Arc<T> {
    ptr: *mut T,
    count: *mut i64,
}

impl<T> Arc<T> {
    fn new(value: T) -> Arc<T> {
        let p: *mut T = malloc(size_of::<T>()) as *mut T;
        p[0] = value;
        let c: *mut i64 = malloc(size_of::<i64>()) as *mut i64;
        c[0] = 1;
        Arc { ptr: p, count: c }
    }

    fn get(&self) -> T {
        self.ptr[0]
    }

    fn set(&mut self, value: T) {
        self.ptr[0] = value;
    }

    fn as_ptr(&self) -> *mut T {
        self.ptr
    }

    fn strong_count(&self) -> i64 {
        self.count[0]
    }

    fn clone(&self) -> Arc<T> {
        self.count[0] += 1;
        Arc { ptr: self.ptr, count: self.count }
    }

    fn release(&mut self) {
        self.count[0] -= 1;
        if self.count[0] <= 0 {
            free(self.ptr as *mut u8);
            free(self.count as *mut u8);
            self.ptr = 0 as *mut T;
            self.count = 0 as *mut i64;
        }
    }
}

// ---------------------------------------------------------------------------
// MutexGuard<T> and the RwLock guards -- what `lock()` / `read()` / `write()`
// return. Each holds the lock until it is dropped; `deref` / `deref_mut`
// reach the value, so `*g`, `g.field`, `g.method()` and `g[i]` all work. The
// pointer is cleared on release, so a guard dropped twice (by a path the
// translator cannot see) cannot unlock a lock someone else now holds.
// ---------------------------------------------------------------------------
struct MutexGuard<T> {
    ptr: *mut T,
    state: *mut u32,
}

impl<T> MutexGuard<T> {
    fn deref(&self) -> &T {
        self.ptr
    }

    fn deref_mut(&mut self) -> &mut T {
        self.ptr
    }

    fn unwrap(self) -> MutexGuard<T> {
        self
    }

    fn get(&self) -> T {
        self.ptr[0]
    }

    fn set(&mut self, value: T) {
        self.ptr[0] = value;
    }

    fn as_ptr(&self) -> *mut T {
        self.ptr
    }
}

impl<T> Drop for MutexGuard<T> {
    fn drop(&mut self) {
        if self.state as usize != 0 {
            crust_atomic_xchg32(self.state, 0);
            self.state = 0 as *mut u32;
        }
    }
}

struct RwLockReadGuard<T> {
    ptr: *mut T,
    state: *mut u32,
}

impl<T> RwLockReadGuard<T> {
    fn deref(&self) -> &T {
        self.ptr
    }
}

impl<T> Drop for RwLockReadGuard<T> {
    fn drop(&mut self) {
        if self.state as usize != 0 {
            crust_atomic_xadd32(self.state, 0xFFFFFFFF);
            self.state = 0 as *mut u32;
        }
    }
}

struct RwLockWriteGuard<T> {
    ptr: *mut T,
    state: *mut u32,
}

impl<T> RwLockWriteGuard<T> {
    fn deref(&self) -> &T {
        self.ptr
    }

    fn deref_mut(&mut self) -> &mut T {
        self.ptr
    }
}

impl<T> Drop for RwLockWriteGuard<T> {
    fn drop(&mut self) {
        if self.state as usize != 0 {
            crust_atomic_xchg32(self.state, 0);
            self.state = 0 as *mut u32;
        }
    }
}

// ---------------------------------------------------------------------------
// MaybeUninit<T> -- storage without a value in it yet.
//
// Faithful in the only way that matters here: in real Rust this is a union
// whose job is to tell the compiler not to assume the contents are
// initialised. Crust makes no such assumption about anything, so the wrapper
// carries the same information it does there -- none. `assume_init` is a
// read, and it is exactly as unchecked here as it is in Rust.
// ---------------------------------------------------------------------------
struct MaybeUninit<T> {
    value: T,
}

impl<T> MaybeUninit<T> {
    fn new(value: T) -> MaybeUninit<T> {
        MaybeUninit { value: value }
    }

    // Storage with nothing in it. The local is declared and never assigned,
    // which is what the name promises; reading it before `write` is as
    // undefined here as it is in Rust.
    fn uninit() -> MaybeUninit<T> {
        let m: MaybeUninit<T>;
        m
    }

    fn write(&mut self, value: T) {
        self.value = value;
    }

    fn assume_init(&self) -> T {
        self.value
    }

    fn assume_init_ref(&self) -> &T {
        &self.value
    }

    fn assume_init_mut(&mut self) -> &mut T {
        &mut self.value
    }

    fn as_mut_ptr(&mut self) -> *mut T {
        &mut self.value as *mut T
    }

    // Dropping the held value is the caller's contract; the storage itself
    // has nothing to release.
    fn assume_init_drop(&mut self) {}

    fn as_ptr(&self) -> *mut T {
        &self.value as *mut T
    }
}

// ---------------------------------------------------------------------------
// VecDeque<T> -- a double-ended queue over a ring buffer.
//
// Grows by doubling like `Vec<T>`. Scope exit calls `free_buf` for by-value
// locals; the method remains available for an earlier release. `pop_front` on
// an empty deque is undefined rather than returning an `Option`, which
// matches how the rest of this core treats out-of-range access.
// ---------------------------------------------------------------------------
struct VecDeque<T> {
    ptr: *mut T,
    head: usize,
    len: usize,
    cap: usize,
}

impl<T> VecDeque<T> {
    fn new() -> VecDeque<T> {
        VecDeque { ptr: 0 as *mut T, head: 0, len: 0, cap: 0 }
    }

    fn len(&self) -> usize {
        self.len
    }

    fn is_empty(&self) -> bool {
        self.len == 0
    }

    fn capacity(&self) -> usize {
        self.cap
    }

    fn get(&self, i: usize) -> T {
        self.ptr[(self.head + i) % self.cap]
    }

    // Growing a ring buffer means unrolling it: the elements are copied out
    // in logical order so the new buffer starts at zero again.
    fn reserve(&mut self, want: usize) {
        if want <= self.cap {
            return;
        }
        let mut cap: usize = self.cap;
        if cap == 0 {
            cap = 4;
        }
        while cap < want {
            cap = cap * 2;
        }
        let fresh: *mut T = malloc(cap * size_of::<T>()) as *mut T;
        for i in 0..self.len {
            fresh[i] = self.ptr[(self.head + i) % self.cap];
        }
        if self.cap > 0 {
            free(self.ptr as *mut u8);
        }
        self.ptr = fresh;
        self.head = 0;
        self.cap = cap;
    }

    fn push_back(&mut self, value: T) {
        self.reserve(self.len + 1);
        self.ptr[(self.head + self.len) % self.cap] = value;
        self.len += 1;
    }

    fn push_front(&mut self, value: T) {
        self.reserve(self.len + 1);
        self.head = (self.head + self.cap - 1) % self.cap;
        self.ptr[self.head] = value;
        self.len += 1;
    }

    fn pop_front(&mut self) -> T {
        let v: T = self.ptr[self.head];
        self.head = (self.head + 1) % self.cap;
        self.len -= 1;
        v
    }

    fn pop_back(&mut self) -> T {
        self.len -= 1;
        self.ptr[(self.head + self.len) % self.cap]
    }

    fn front(&self) -> T {
        self.ptr[self.head]
    }

    fn back(&self) -> T {
        self.ptr[(self.head + self.len - 1) % self.cap]
    }

    fn clear(&mut self) {
        self.drop_elems();
        self.head = 0;
        self.len = 0;
    }

    fn free_buf(&mut self) {
        self.drop_elems();
        free(self.ptr as *mut u8);
        self.ptr = 0 as *mut T;
        self.head = 0;
        self.len = 0;
        self.cap = 0;
    }
}

// ---------------------------------------------------------------------------
// String -- a growable, NUL-terminated character buffer.
//
// Deliberately *not* rpython's `str`. py2c has a complete string runtime --
// concat, split, join, `%` formatting -- and reaching for it would mean
// linking `shivyc_rt.c` into every unit that formats anything, plus an arena
// allocator a kernel cannot use before its heap exists. A kernel's string
// handling is short and bounded; this is that.
//
// The buffer is always NUL-terminated, so `as_ptr` hands back something C can
// take directly. Scope exit calls `free_buf` for by-value locals.
// ---------------------------------------------------------------------------
struct String {
    buf: *mut c_char,
    len: i64,
    cap: i64,
}

impl String {
    // A deep copy: its own buffer. The buffer is always NUL-terminated, so
    // `push_str` copies exactly the contents.
    fn clone(&self) -> String {
        let mut s: String = String::new();
        if self.len > 0 {
            s.push_str(self.buf as *const c_char);
        }
        s
    }

    fn new() -> String {
        String { buf: 0 as *mut c_char, len: 0, cap: 0 }
    }

    fn with_capacity(cap: i64) -> String {
        let mut s: String = String::new();
        s.reserve(cap);
        s
    }

    fn len(&self) -> i64 {
        self.len
    }

    fn is_empty(&self) -> bool {
        self.len == 0
    }

    fn capacity(&self) -> i64 {
        self.cap
    }

    fn as_ptr(&self) -> *mut c_char {
        self.buf
    }

    // Room for `want` characters plus the terminator, doubling so repeated
    // appends stay amortised.
    fn reserve(&mut self, want: i64) {
        if want + 1 <= self.cap {
            return;
        }
        let mut cap: i64 = self.cap;
        if cap == 0 {
            cap = 16;
        }
        while cap < want + 1 {
            cap = cap * 2;
        }
        self.buf = realloc(self.buf as *mut u8, cap as usize) as *mut c_char;
        self.cap = cap;
        self.buf[self.len] = 0 as c_char;
    }

    // Rust's `String::push` takes a `char` -- a Unicode scalar -- and appends
    // its UTF-8 encoding. `push_byte` appends one raw byte.
    fn push(&mut self, c: char) {
        let v: u32 = c as u32;
        if v < 128 {
            self.push_byte(v as c_char);
        } else if v < 2048 {
            self.push_byte((192 | (v >> 6)) as c_char);
            self.push_byte((128 | (v & 63)) as c_char);
        } else if v < 65536 {
            self.push_byte((224 | (v >> 12)) as c_char);
            self.push_byte((128 | ((v >> 6) & 63)) as c_char);
            self.push_byte((128 | (v & 63)) as c_char);
        } else {
            self.push_byte((240 | (v >> 18)) as c_char);
            self.push_byte((128 | ((v >> 12) & 63)) as c_char);
            self.push_byte((128 | ((v >> 6) & 63)) as c_char);
            self.push_byte((128 | (v & 63)) as c_char);
        }
    }

    fn push_byte(&mut self, c: c_char) {
        self.reserve(self.len + 1);
        self.buf[self.len] = c;
        self.len += 1;
        self.buf[self.len] = 0 as c_char;
    }

    fn push_str(&mut self, s: *const c_char) {
        let mut i: i64 = 0;
        while s[i] != 0 as c_char {
            self.push_byte(s[i]);
            i += 1;
        }
    }

    fn get(&self, i: i64) -> c_char {
        self.buf[i]
    }

    fn clear(&mut self) {
        self.len = 0;
        if self.cap > 0 {
            self.buf[0] = 0 as c_char;
        }
    }

    fn eq_str(&self, other: *const c_char) -> bool {
        let mut i: i64 = 0;
        while i < self.len {
            if other[i] != self.buf[i] {
                return false;
            }
            i += 1;
        }
        other[self.len] == 0 as c_char
    }

    fn free_buf(&mut self) {
        free(self.buf as *mut u8);
        self.buf = 0 as *mut c_char;
        self.len = 0;
        self.cap = 0;
    }
}

// ---------------------------------------------------------------------------
// PhantomData<T> -- a zero-sized marker. Real Rust uses it to tie a type
// parameter to a struct that does not otherwise mention it; here it exists so
// that such a struct still parses. It lowers to the same one-byte placeholder
// any unit struct does.
// ---------------------------------------------------------------------------
// CpuidResult -- what `core::arch::x86_64::__cpuid` returns.
//
// `cpuid` writes rbx, which this compiler's code never saves, so the query
// keeps it on the stack around the instruction and reads ebx out through esi.
// `__cpuid(leaf)` and `__cpuid_count(leaf, sub)` are lowered to `query`.
// ---------------------------------------------------------------------------
struct CpuidResult {
    eax: u32,
    ebx: u32,
    ecx: u32,
    edx: u32,
}

impl CpuidResult {
    fn query(leaf: u32, sub: u32) -> CpuidResult {
        let mut a: u32 = leaf;
        let mut c: u32 = sub;
        let d: u32;
        let b: u32;
        unsafe {
            asm!("push rbx", "cpuid", "mov esi, ebx", "pop rbx",
                 inlateout("eax") a, inlateout("ecx") c, out("edx") d, out("esi") b);
        }
        CpuidResult { eax: a, ebx: b, ecx: c, edx: d }
    }
}

// ---------------------------------------------------------------------------
struct PhantomData<T> {
    _marker: u8,
}

// ---------------------------------------------------------------------------
// AtomicU32 / AtomicUsize / AtomicU64 / AtomicBool / Ordering.
//
// Real atomics on x86-64: each operation is one `lock`-prefixed instruction
// (or, for loads, a volatile read the compiler may not hoist), supplied as
// `crust_atomic_*` helpers that the translator writes into any unit that uses
// one. They are sequentially consistent whatever `Ordering` is asked for --
// the strongest answer is always a correct one, and on x86 the cost of the
// weaker orderings is small. `compare_exchange` returns the value that was
// there, as the C11 builtin does, rather than std's `Result`; the swap
// happened iff it equals `current`.
// ---------------------------------------------------------------------------
enum Ordering {
    Relaxed,
    Acquire,
    Release,
    AcqRel,
    SeqCst,
}

struct AtomicU32 {
    value: u32,
}

impl AtomicU32 {
    fn new(value: u32) -> AtomicU32 {
        AtomicU32 { value: value }
    }

    fn load(&self, _order: Ordering) -> u32 {
        crust_atomic_load32(&self.value as *mut u32)
    }

    fn store(&mut self, value: u32, _order: Ordering) {
        crust_atomic_xchg32(&mut self.value, value);
    }

    fn swap(&mut self, value: u32, _order: Ordering) -> u32 {
        crust_atomic_xchg32(&mut self.value, value)
    }

    // Returns the value that was there, like the C11 builtin; the swap
    // happened iff it equals `current`.
    fn compare_exchange(&mut self, current: u32, new: u32,
                        _success: Ordering, _failure: Ordering) -> Result<u32, u32> {
        let seen: u32 = crust_atomic_cas32(&mut self.value, current, new);
        if seen == current {
            Ok(seen)
        } else {
            Err(seen)
        }
    }

    fn compare_exchange_weak(&mut self, current: u32, new: u32,
                             success: Ordering, failure: Ordering) -> Result<u32, u32> {
        self.compare_exchange(current, new, success, failure)
    }

    fn fetch_add(&mut self, value: u32, _order: Ordering) -> u32 {
        crust_atomic_xadd32(&mut self.value, value)
    }

    fn fetch_sub(&mut self, value: u32, _order: Ordering) -> u32 {
        crust_atomic_xadd32(&mut self.value, (0 as u32) - value)
    }

    fn fetch_or(&mut self, value: u32, _order: Ordering) -> u32 {
        let mut old: u32 = crust_atomic_load32(&self.value as *mut u32);
        loop {
            let seen: u32 = crust_atomic_cas32(&mut self.value, old, old | value);
            if seen == old {
                return old;
            }
            old = seen;
        }
    }

    fn fetch_and(&mut self, value: u32, _order: Ordering) -> u32 {
        let mut old: u32 = crust_atomic_load32(&self.value as *mut u32);
        loop {
            let seen: u32 = crust_atomic_cas32(&mut self.value, old, old & value);
            if seen == old {
                return old;
            }
            old = seen;
        }
    }
}

struct AtomicUsize {
    value: usize,
}

impl AtomicUsize {
    fn new(value: usize) -> AtomicUsize {
        AtomicUsize { value: value }
    }

    fn load(&self, _order: Ordering) -> usize {
        crust_atomic_load64(&self.value as *mut usize)
    }

    fn store(&mut self, value: usize, _order: Ordering) {
        crust_atomic_xchg64(&mut self.value, value);
    }

    fn swap(&mut self, value: usize, _order: Ordering) -> usize {
        crust_atomic_xchg64(&mut self.value, value)
    }

    // Returns the value that was there, like the C11 builtin; the swap
    // happened iff it equals `current`.
    fn compare_exchange(&mut self, current: usize, new: usize,
                        _success: Ordering, _failure: Ordering) -> Result<usize, usize> {
        let seen: usize = crust_atomic_cas64(&mut self.value, current, new);
        if seen == current {
            Ok(seen)
        } else {
            Err(seen)
        }
    }

    fn compare_exchange_weak(&mut self, current: usize, new: usize,
                             success: Ordering, failure: Ordering) -> Result<usize, usize> {
        self.compare_exchange(current, new, success, failure)
    }

    fn fetch_add(&mut self, value: usize, _order: Ordering) -> usize {
        crust_atomic_xadd64(&mut self.value, value)
    }

    fn fetch_sub(&mut self, value: usize, _order: Ordering) -> usize {
        crust_atomic_xadd64(&mut self.value, (0 as usize) - value)
    }

    fn fetch_or(&mut self, value: usize, _order: Ordering) -> usize {
        let mut old: usize = crust_atomic_load64(&self.value as *mut usize);
        loop {
            let seen: usize = crust_atomic_cas64(&mut self.value, old, old | value);
            if seen == old {
                return old;
            }
            old = seen;
        }
    }

    fn fetch_and(&mut self, value: usize, _order: Ordering) -> usize {
        let mut old: usize = crust_atomic_load64(&self.value as *mut usize);
        loop {
            let seen: usize = crust_atomic_cas64(&mut self.value, old, old & value);
            if seen == old {
                return old;
            }
            old = seen;
        }
    }
}

struct AtomicU64 {
    value: u64,
}

impl AtomicU64 {
    fn new(value: u64) -> AtomicU64 {
        AtomicU64 { value: value }
    }

    fn load(&self, _order: Ordering) -> u64 {
        crust_atomic_load64(&self.value as *mut u64)
    }

    fn store(&mut self, value: u64, _order: Ordering) {
        crust_atomic_xchg64(&mut self.value, value);
    }

    fn swap(&mut self, value: u64, _order: Ordering) -> u64 {
        crust_atomic_xchg64(&mut self.value, value)
    }

    // Returns the value that was there, like the C11 builtin; the swap
    // happened iff it equals `current`.
    fn compare_exchange(&mut self, current: u64, new: u64,
                        _success: Ordering, _failure: Ordering) -> Result<u64, u64> {
        let seen: u64 = crust_atomic_cas64(&mut self.value, current, new);
        if seen == current {
            Ok(seen)
        } else {
            Err(seen)
        }
    }

    fn compare_exchange_weak(&mut self, current: u64, new: u64,
                             success: Ordering, failure: Ordering) -> Result<u64, u64> {
        self.compare_exchange(current, new, success, failure)
    }

    fn fetch_add(&mut self, value: u64, _order: Ordering) -> u64 {
        crust_atomic_xadd64(&mut self.value, value)
    }

    fn fetch_sub(&mut self, value: u64, _order: Ordering) -> u64 {
        crust_atomic_xadd64(&mut self.value, (0 as u64) - value)
    }

    fn fetch_or(&mut self, value: u64, _order: Ordering) -> u64 {
        let mut old: u64 = crust_atomic_load64(&self.value as *mut u64);
        loop {
            let seen: u64 = crust_atomic_cas64(&mut self.value, old, old | value);
            if seen == old {
                return old;
            }
            old = seen;
        }
    }

    fn fetch_and(&mut self, value: u64, _order: Ordering) -> u64 {
        let mut old: u64 = crust_atomic_load64(&self.value as *mut u64);
        loop {
            let seen: u64 = crust_atomic_cas64(&mut self.value, old, old & value);
            if seen == old {
                return old;
            }
            old = seen;
        }
    }
}

// `AtomicBool` stores a u32 so the same instructions serve it.
struct AtomicU8 {
    value: u8,
}

impl AtomicU8 {
    fn new(value: u8) -> AtomicU8 {
        AtomicU8 { value: value }
    }

    fn load(&self, _order: Ordering) -> u8 {
        crust_atomic_load8(&self.value as *mut u8)
    }

    fn store(&mut self, value: u8, _order: Ordering) {
        crust_atomic_xchg8(&mut self.value, value);
    }

    fn swap(&mut self, value: u8, _order: Ordering) -> u8 {
        crust_atomic_xchg8(&mut self.value, value)
    }

    // Returns the value that was there, like the C11 builtin; the swap
    // happened iff it equals `current`.
    fn compare_exchange(&mut self, current: u8, new: u8,
                        _success: Ordering, _failure: Ordering) -> Result<u8, u8> {
        let seen: u8 = crust_atomic_cas8(&mut self.value, current, new);
        if seen == current {
            Ok(seen)
        } else {
            Err(seen)
        }
    }

    fn compare_exchange_weak(&mut self, current: u8, new: u8,
                             success: Ordering, failure: Ordering) -> Result<u8, u8> {
        self.compare_exchange(current, new, success, failure)
    }

    fn fetch_add(&mut self, value: u8, _order: Ordering) -> u8 {
        crust_atomic_xadd8(&mut self.value, value)
    }

    fn fetch_sub(&mut self, value: u8, _order: Ordering) -> u8 {
        crust_atomic_xadd8(&mut self.value, (0 as u8) - value)
    }

    fn fetch_or(&mut self, value: u8, _order: Ordering) -> u8 {
        let mut old: u8 = crust_atomic_load8(&self.value as *mut u8);
        loop {
            let seen: u8 = crust_atomic_cas8(&mut self.value, old, old | value);
            if seen == old {
                return old;
            }
            old = seen;
        }
    }

    fn fetch_and(&mut self, value: u8, _order: Ordering) -> u8 {
        let mut old: u8 = crust_atomic_load8(&self.value as *mut u8);
        loop {
            let seen: u8 = crust_atomic_cas8(&mut self.value, old, old & value);
            if seen == old {
                return old;
            }
            old = seen;
        }
    }
}

struct AtomicU16 {
    value: u16,
}

impl AtomicU16 {
    fn new(value: u16) -> AtomicU16 {
        AtomicU16 { value: value }
    }

    fn load(&self, _order: Ordering) -> u16 {
        crust_atomic_load16(&self.value as *mut u16)
    }

    fn store(&mut self, value: u16, _order: Ordering) {
        crust_atomic_xchg16(&mut self.value, value);
    }

    fn swap(&mut self, value: u16, _order: Ordering) -> u16 {
        crust_atomic_xchg16(&mut self.value, value)
    }

    // Returns the value that was there, like the C11 builtin; the swap
    // happened iff it equals `current`.
    fn compare_exchange(&mut self, current: u16, new: u16,
                        _success: Ordering, _failure: Ordering) -> Result<u16, u16> {
        let seen: u16 = crust_atomic_cas16(&mut self.value, current, new);
        if seen == current {
            Ok(seen)
        } else {
            Err(seen)
        }
    }

    fn compare_exchange_weak(&mut self, current: u16, new: u16,
                             success: Ordering, failure: Ordering) -> Result<u16, u16> {
        self.compare_exchange(current, new, success, failure)
    }

    fn fetch_add(&mut self, value: u16, _order: Ordering) -> u16 {
        crust_atomic_xadd16(&mut self.value, value)
    }

    fn fetch_sub(&mut self, value: u16, _order: Ordering) -> u16 {
        crust_atomic_xadd16(&mut self.value, (0 as u16) - value)
    }

    fn fetch_or(&mut self, value: u16, _order: Ordering) -> u16 {
        let mut old: u16 = crust_atomic_load16(&self.value as *mut u16);
        loop {
            let seen: u16 = crust_atomic_cas16(&mut self.value, old, old | value);
            if seen == old {
                return old;
            }
            old = seen;
        }
    }

    fn fetch_and(&mut self, value: u16, _order: Ordering) -> u16 {
        let mut old: u16 = crust_atomic_load16(&self.value as *mut u16);
        loop {
            let seen: u16 = crust_atomic_cas16(&mut self.value, old, old & value);
            if seen == old {
                return old;
            }
            old = seen;
        }
    }
}

struct AtomicBool {
    value: u32,
}

impl AtomicBool {
    fn new(value: bool) -> AtomicBool {
        AtomicBool { value: value as u32 }
    }

    fn load(&self, _order: Ordering) -> bool {
        crust_atomic_load32(&self.value as *mut u32) != 0
    }

    fn store(&mut self, value: bool, _order: Ordering) {
        crust_atomic_xchg32(&mut self.value, value as u32);
    }

    fn swap(&mut self, value: bool, _order: Ordering) -> bool {
        crust_atomic_xchg32(&mut self.value, value as u32) != 0
    }

    fn compare_exchange(&mut self, current: bool, new: bool,
                        _success: Ordering, _failure: Ordering) -> Result<bool, bool> {
        let seen: bool = crust_atomic_cas32(&mut self.value, current as u32, new as u32) != 0;
        if seen == current {
            Ok(seen)
        } else {
            Err(seen)
        }
    }

    fn compare_exchange_weak(&mut self, current: bool, new: bool,
                             success: Ordering, failure: Ordering) -> Result<bool, bool> {
        self.compare_exchange(current, new, success, failure)
    }
}
