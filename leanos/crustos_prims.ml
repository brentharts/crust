(* CrustOS: what the kernel equation names and does not define.

   The equation `eq:crustos` of `crustos_eq.tex` states the kernel's two
   theorems and defines, inside its braces, the invariant, the scheduler's
   loop and its pieces; `tools/latex2ocaml.py` turns those into
   `generated_crustos.ml`.  The four names it leaves undefined -- `tick`,
   `pass`, `accepted`, `split` -- and the context they act on are here, ported
   from `crustos_eq.py` (the model the equation was proved of) and
   `crustos/schemes.py` (the rpython it models), as the LeanOS modules are.

   A context is `Ctx (cur, n, ticks)`: the model's `current`, `nthreads` and
   `ticks`, one variant for its record.  Bytes are an `int list`; `,` is 44
   and `:` is 58, as the model writes them.  `scheme_of` answers -1 for no
   scheme, as `schemes.py` does -- an OCaml `int` has a -1, so the model's
   `len(names)` stand-in is not needed. *)

type ctx = Ctx of int * int * int

let cur c = match c with Ctx (x, _, _) -> x
let n c = match c with Ctx (_, x, _) -> x
let ticks c = match c with Ctx (_, _, x) -> x

(* one clock tick: the scheduler's state is untouched *)
let tick c = match c with Ctx (a, b, t) -> Ctx (a, b, t + 1)
  [@@ensures cur result = cur c && n result = n c]

(* one pass of the scheduler's loop: the next thread, one more tick *)
let pass s = match s with Ctx (a, b, t) -> Ctx (a + 1, b, t + 1)
  [@@requires cur s < n s] [@@ensures cur result <= n result]

(* -- the routing layer's pieces: `crustos/schemes.py` -------------------- *)

(* the index of the first `:` in u, counting from i; -1 if none *)
let rec find_colon u i = match u with
  | [] -> 0 - 1
  | b :: t -> if b = 58 then i else find_colon t (i + 1)
  [@@requires i >= 0 && i + List.length u <= max_int]
  [@@ensures result >= 0 - 1] [@@variant u]

(* the first k bytes of u *)
let rec take u k = match u with
  | [] -> []
  | b :: t -> if k <= 0 then [] else b :: take t (k - 1)
  [@@variant u]

let rec eqs a b = match a with
  | [] -> (match b with [] -> true | _ :: _ -> false)
  | x :: at -> (match b with [] -> false | y :: bt -> x = y && eqs at bt)
  [@@variant a]

(* where `head` is among `names`, counting from i; -1 if it is not *)
let rec index_of head names i = match names with
  | [] -> 0 - 1
  | nm :: t -> if eqs nm head then i else index_of head t (i + 1)
  [@@requires i >= 0 && i + List.length names <= max_int]
  [@@ensures result >= 0 - 1] [@@variant names]

(* which scheme claims `url`, by its `name:` prefix; -1 if none *)
let scheme_of names url =
  let idx = find_colon url 0 in
  if idx <= 0 then 0 - 1 else index_of (take url idx) names 0

(* u split at each `sep`, as Python's str.split: "" is [""] *)
let rec split u sep = match u with
  | [] -> [[]]
  | b :: t ->
      let rest = split t sep in
      if b = sep then [] :: rest
      else (match rest with p :: ps -> (b :: p) :: ps | [] -> [[b]])
  [@@ensures List.length result <= List.length u + 1] [@@variant u]

(* the indices, counting from i, of the pieces a scheme claims *)
let rec accepted_from names parts i = match parts with
  | [] -> []
  | p :: t ->
      if scheme_of names p = 0 - 1 then accepted_from names t (i + 1)
      else i :: accepted_from names t (i + 1)
  [@@requires i >= 0 && i + List.length parts <= max_int]
  [@@ensures List.length result <= List.length parts] [@@variant parts]

(* the indices of the URLs in a comma-separated batch that name a
   registered scheme: `schemes.py`'s `accepted` *)
let accepted names urls = accepted_from names (split urls 44) 0
