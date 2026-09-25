(* LeanOS: the ELF loader's checks, in OCaml.

   A port of `leanos/elfcheck.py`, beside `elfcheck.rs`: decide before
   dereferencing whether an image's loads are ones a loader should map.
   `accept_image` is the gate `loader.ml` puts in front of the region list.

   Where it differs from the Python, as `elfcheck.rs` does:

     * a load whose end `vaddr + memsz` would pass `max_int` is refused (0)
       rather than computed -- no loader could map it; the Python, with no
       top to its integers, accepts it;
     * a negative `memsz` is refused, where the Python would let a load's end
       come before its start;
     * `entry < vaddr + memsz` is `entry - vaddr < memsz`, behind
       `vaddr <= entry` and `0 <= vaddr`.

   On addresses and sizes in [0, 2^62) whose ends stay below 2^62, the
   answers are the Python's. *)

(* elf.c: 0 = minimal, 1 = +extras, 2 = full GPR, 3 = +xmm *)
let reg_class_ok cls = if cls <= 3 then 1 else 0
  [@@ensures (result = 0 || result = 1) && (result = 0 || cls <= 3)]

(* each load starts at or after `upto`, the end of the one before *)
let rec loads_from upto vaddrs memszs = match vaddrs with
  | [] -> 1
  | v :: vt -> (match memszs with
      | [] -> 1
      | m :: mt ->
          if v < upto then 0
          else if m < 0 then 0
          else if v > max_int - m then 0
          else loads_from (v + m) vt mt)
  [@@requires upto >= 0] [@@ensures result = 0 || result = 1]
  [@@variant vaddrs]

(* Is `a` shorter than `b`?  `len a < len b`, without counting. *)
let rec shorter_load a b = match a with
  | [] -> (match b with [] -> false | _ :: _ -> true)
  | _ :: at -> (match b with [] -> false | _ :: bt -> shorter_load at bt)
  [@@variant a]

(* 1 if the loads are ascending and none overlaps the one before. *)
let loads_ordered vaddrs memszs =
  if shorter_load memszs vaddrs then 0 else loads_from 0 vaddrs memszs
  [@@ensures result = 0 || result = 1]

let rec entry_from vaddrs memszs entry = match vaddrs with
  | [] -> 0
  | v :: vt -> (match memszs with
      | [] -> 0
      | m :: mt ->
          if v <= entry then
            (if 0 <= v then
               (if entry - v < m then 1 else entry_from vt mt entry)
             else entry_from vt mt entry)
          else entry_from vt mt entry)
  [@@ensures result = 0 || result = 1] [@@variant vaddrs]

(* 1 if the entry point lies inside some load. *)
let entry_in_load vaddrs memszs entry =
  if shorter_load memszs vaddrs then 0 else entry_from vaddrs memszs entry
  [@@ensures result = 0 || result = 1]

(* The gate: a register class the scheduler sizes, ordered loads, and an
   entry point inside one of them. *)
let accept_image vaddrs memszs entry cls =
  if reg_class_ok cls = 0 then 0
  else if loads_ordered vaddrs memszs = 0 then 0
  else if entry_in_load vaddrs memszs entry = 0 then 0
  else 1
  [@@ensures (result = 0 || result = 1) && (result = 0 || cls <= 3)]
