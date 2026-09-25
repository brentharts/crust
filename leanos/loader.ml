(* LeanOS: admitting an ELF into the region list, in OCaml.

   uses: elfcheck.ml memmap.ml

   A port of `leanos/loader.py`, beside `loader.rs`.  `elfcheck.ml` decides
   whether an image's headers are ones a loader should map; LeanOS asks one
   more thing, the founding rule applied to the guest: its loads, taken as
   regions, must leave the region list disjoint.  So `admit` is
   `accept_image` and then `regions_disjoint` on the list with the loads
   appended -- built, as the Python builds it (`extended`, `claimed`),
   where `loader.rs` reads the concatenation in place: an OCaml list is a
   value, and appending one makes another.

   Its contracts are `loader_eq.py`'s, as far as a contract here can say
   them: an admitted image has a register class the scheduler sizes. *)

(* xs, then ys: the Python's `extended` *)
let rec extended xs ys = match xs with
  | [] -> ys
  | x :: t -> x :: extended t ys
  [@@variant xs]

(* `count` copies of `guest` *)
let rec copies guest count =
  if count <= 0 then [] else guest :: copies guest (count - 1)
  [@@variant count]

(* the owners, then `count` regions owned by `guest`: the Python's
   `claimed` *)
let claimed owners guest count = extended owners (copies guest count)

(* 1 if the image passes the ELF checks and its loads, appended to the
   region list, leave the list disjoint. *)
let admit bases sizes vaddrs memszs entry cls =
  if accept_image vaddrs memszs entry cls = 1 then
    (if regions_disjoint (extended bases vaddrs) (extended sizes memszs) = 1
     then 1 else 0)
  else 0
  [@@ensures (result = 0 || result = 1) && (result = 0 || cls <= 3)]
