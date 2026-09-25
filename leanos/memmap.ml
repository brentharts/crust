(* LeanOS: the region list, in OCaml.

   A port of `leanos/memmap.py`, beside `memmap.rs`.  Every region the kernel
   will touch is an entry -- base, size, owner -- laid out at build time, and
   `regions_disjoint` is the check the rest of LeanOS rests on.

   `tools/ocaml2rust.py` compiles this to Crust's Rust; `tools/rustprove.py`
   proves each contract written here, and every place the compiled code
   could panic or an OCaml `int` could wrap, about that Rust; Lean 4 checks
   every theorem again.  The lists are OCaml lists -- `bases`, `sizes` and
   `owners` stay three, as in the Python, so each function answers the
   Python's question with the Python's arguments -- and a loop over an
   index is a recursion down the lists, `[@@variant]` its measure.

   Where it differs from the Python, it is where OCaml's 63-bit `int`
   differs from the Python's unbounded one, as `memmap.rs` differs where a
   `u64` does:

     * `b < pb + ps` is `b < pb || b - pb < ps`: the same test over the
       integers, and one that cannot wrap -- behind `0 <= pb`, since an
       address is never negative; a negative base is refused (0), where the
       Python would compare it.
     * `addr < b + s` is `addr - b < s`, behind `b <= addr` and `0 <= b`.
     * an index `i` below 0 answers 0, where the Python would count from
       the end of the list; the Rust's `usize` cannot be below 0.

   On bases, sizes and addresses in [0, 2^62) the answers are the Python's. *)

(* Is `a` shorter than `b`?  `len a < len b`, without counting. *)
let rec shorter a b = match a with
  | [] -> (match b with [] -> false | _ :: _ -> true)
  | _ :: at -> (match b with [] -> false | _ :: bt -> shorter at bt)
  [@@variant a]

(* base <= addr < base + size, for a non-negative base *)
let in_region base size addr =
  if base <= addr then (if 0 <= base then addr - base < size else false)
  else false

(* Each region at or after the head starts at or after the end of the one
   before it -- (pb, ps), the previous base and size. *)
let rec ordered pb ps bases sizes = match bases with
  | [] -> 1
  | b :: bt -> (match sizes with
      | [] -> 1
      | s :: st ->
          if b < pb then 0
          else if pb < 0 then 0
          else if b - pb < ps then 0
          else ordered b s bt st)
  [@@ensures result = 0 || result = 1] [@@variant bases]

(* 1 if the regions are ascending and no two overlap.  Touching is fine: a
   region may end exactly where the next begins. *)
let regions_disjoint bases sizes =
  if shorter sizes bases then 0
  else match bases with
    | [] -> 1
    | b :: bt -> (match sizes with [] -> 1 | s :: st -> ordered b s bt st)
  [@@ensures result = 0 || result = 1]

(* region i of these lists holds addr *)
let rec nth_holds bases sizes i addr = match bases with
  | [] -> 0
  | b :: bt -> (match sizes with
      | [] -> 0
      | s :: st ->
          if i = 0 then (if in_region b s addr then 1 else 0)
          else nth_holds bt st (i - 1) addr)
  [@@requires i >= 0] [@@ensures result = 0 || result = 1] [@@variant bases]

(* 1 if `addr` lies in region `i`: base <= addr < base + size. *)
let contains bases sizes i addr =
  if i < 0 then 0
  else if shorter sizes bases then 0
  else nth_holds bases sizes i addr
  [@@ensures result = 0 || result = 1]

(* How many regions `who` owns. *)
let rec owned_by owners who = match owners with
  | [] -> 0
  | o :: t -> if o = who then 1 + owned_by t who else owned_by t who
  [@@ensures result >= 0 && result <= List.length owners] [@@variant owners]

(* The index, among these lists, of the first region `who` owns holding
   `addr`; the length of `bases` if there is none. *)
let rec find bases sizes owners who addr = match bases with
  | [] -> 0
  | b :: bt -> (match sizes with
      | [] -> 0
      | s :: st -> (match owners with
          | [] -> 0
          | o :: ot ->
              if o = who then
                (if in_region b s addr then 0
                 else 1 + find bt st ot who addr)
              else 1 + find bt st ot who addr))
  [@@ensures result >= 0 && result <= List.length bases] [@@variant bases]

(* The index of the region `who` owns that holds `addr`, or the length of
   `bases`: the witness, not a yes or no.  `region_of` is the decision. *)
let region_index bases sizes owners who addr =
  if shorter sizes bases then List.length bases
  else if shorter owners bases then List.length bases
  else find bases sizes owners who addr
  [@@ensures result >= 0 && result <= List.length bases]

(* 1 if `addr` lies in a region `who` owns: the founding access rule. *)
let region_of bases sizes owners who addr =
  if region_index bases sizes owners who addr < List.length bases then 1
  else 0
  [@@ensures result = 0 || result = 1]
