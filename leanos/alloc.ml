(* LeanOS: the per-thread allocator, in OCaml.

   uses: memmap.ml

   A port of `leanos/alloc.py`, beside `alloc.rs`: `bump` moves a thread's
   `used` up a region it owns if the request fits, else leaves it -- out of
   memory is a decision, not a fault.

   Where it differs from the Python, as `alloc.rs` does:

     * `owners[heap] == tid + 1` is `o >= 1 && o - 1 = tid`, which cannot
       wrap; a `heap` past the end of `owners` or `sizes`, where the Python
       would raise, leaves `used` as it is;
     * `used + n <= size` is `0 <= n && n <= size && used <= size - n`: the
       same test for a request `n >= 0`, and one that cannot wrap; a negative
       request is refused, where the Python would move `used` down;
     * `slot_addr` and `slot_ok` compute `base + used` only where it fits in
       an `int` and neither is negative -- 0 otherwise, where `alloc.rs`
       requires it of its caller and the Python has no top;
     * a negative `heap` is no region (the Python would count from the end).

   On bases, sizes and requests in [0, 2^62), the answers are the Python's. *)

(* xs's element k, or `default` past the end *)
let rec nth_int xs k default = match xs with
  | [] -> default
  | x :: t -> if k = 0 then x else nth_int t (k - 1) default
  [@@requires k >= 0] [@@variant xs]

(* Move `used` up by `n` in region `heap`, if thread `tid` owns it and the
   request fits; else leave it.  Never gives back. *)
let bump bases sizes owners tid heap used n =
  if heap < 0 then used
  else if heap < List.length bases && heap < List.length owners
          && heap < List.length sizes then
    (let o = nth_int owners heap 0 in
     let s = nth_int sizes heap 0 in
     if o >= 1 && o - 1 = tid then
       (if 0 <= n && n <= s then
          (if used <= s - n then used + n else used)
        else used)
     else used)
  else used
  [@@ensures result >= used]

(* The address of the next slot: the region's base plus what is used. *)
let slot_addr bases heap used =
  if heap < 0 then 0
  else
    (let b = nth_int bases heap 0 in
     if 0 <= b && 0 <= used && b <= max_int - used then b + used else 0)

(* 1 if the next slot is still inside the region. *)
let slot_ok bases sizes heap used =
  if heap < 0 then 0
  else if heap >= List.length bases then 0
  else
    (let b = nth_int bases heap 0 in
     if 0 <= b && 0 <= used && b <= max_int - used then
       (if contains bases sizes heap (b + used) = 1 then 1 else 0)
     else 0)
  [@@ensures result = 0 || result = 1]
