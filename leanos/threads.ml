(* LeanOS: threads as records, in OCaml.

   uses: memmap.ml

   A port of `leanos/threads.py`, beside `threads.rs`.  A thread is an owner
   and a stack pointer: thread `tid` is owner `tid + 1` (0 is the kernel),
   `sp_ok` is the founding access rule applied to its stack, and
   `sp_after_push` is a guarded update that refuses rather than faults.

   Where it differs from the Python, as `threads.rs` does:

     * `thread_owner` requires `tid < max_int`, and `sp_ok` answers 0 for
       `tid = max_int`, whose owner would wrap;
     * `sp_after_push` refuses a negative push, where the Python would move
       the stack pointer up.

   On stack pointers and pushes in [0, 2^62), the answers are the
   Python's. *)

let thread_owner tid = tid + 1
  [@@requires tid < max_int] [@@ensures result = tid + 1]

(* 1 if `sp` lies in a region thread `tid` owns. *)
let sp_ok bases sizes owners tid sp =
  if tid >= max_int then 0
  else if region_of bases sizes owners (thread_owner tid) sp = 1 then 1
  else 0
  [@@ensures result = 0 || result = 1]

(* every stack pointer from thread `i` on is in its thread's region *)
let rec sps_from bases sizes owners i sps = match sps with
  | [] -> 1
  | sp :: t ->
      if sp_ok bases sizes owners i sp = 0 then 0
      else sps_from bases sizes owners (i + 1) t
  [@@requires i >= 0 && i + List.length sps <= max_int]
  [@@ensures result = 0 || result = 1] [@@variant sps]

(* The table check: thread `i`'s stack pointer is `sps`'s element `i`. *)
let all_sps_ok bases sizes owners sps = sps_from bases sizes owners 0 sps
  [@@ensures result = 0 || result = 1]

(* The stack pointer after pushing `n` bytes, if that stays in the thread's
   region; else the stack pointer as it was. *)
let sp_after_push bases sizes owners tid sp n =
  if n <= sp then
    (if 0 <= n then
       (if sp_ok bases sizes owners tid (sp - n) = 1 then sp - n else sp)
     else sp)
  else sp
  (* push_keeps_sp_ok: a push never leaves the thread's region *)
  [@@ensures sp_ok bases sizes owners tid sp = 0
             || sp_ok bases sizes owners tid result = 1]
