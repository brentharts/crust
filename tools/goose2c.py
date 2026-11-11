#!/usr/bin/env python3
"""goose2c -- translate a Goose subset program to C (see GOOSE.md).

    python3 tools/goose2c.py prog.goose -o prog.c

Phase 2a: the plain subset -- scalars, `let`/`var`, control flow as
expressions, functions (multiple results, overloads, untyped parameters as
generics, monomorphized), structs, fixed arrays, slices, `print`.  The
checker and the lowering are one pass: every expression is typed as it is
emitted, and what the subset does not have is refused, with the line, in
Goose's terms.

The emitted C is plain C99 with no library beyond <stdio.h>/<stdlib.h>/
<string.h>/<stdint.h>/<math.h>; the small runtime it needs is in the file
(RUNTIME below), written for this translator -- not Goose's own.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.goose import GooseError, N, parse                   # noqa: E402


def refuse(msg, line):
    raise GooseError(msg, line)


def unsupported(what, line):
    refuse("%s is not in the Crust Goose subset yet" % what, line)


# ---------------------------------------------------------------------------
# Types

class Ty:
    """k: int float bool arr slice struct.  Compared by `key`."""
    __slots__ = ("k", "name", "bits", "signed", "elem", "n", "info", "key")

    def __init__(self, k, key, name=None, bits=0, signed=False, elem=None, n=0, info=None):
        self.k, self.key, self.name, self.bits, self.signed = k, key, name, bits, signed
        self.elem, self.n, self.info = elem, n, info

    def __eq__(self, o):
        return isinstance(o, Ty) and self.key == o.key

    def __hash__(self):
        return hash(self.key)

    def __repr__(self):
        return self.key

    @property
    def is_num(self):
        return self.k in ("int", "float")


def _mk_prims():
    d = {}
    for nm, bits, signed in (("i8", 8, True), ("i16", 16, True), ("i32", 32, True), ("i64", 64, True),
                             ("u8", 8, False), ("u16", 16, False), ("u32", 32, False), ("u64", 64, False)):
        d[nm] = Ty("int", nm, nm, bits, signed)
    d["f32"] = Ty("float", "f32", "f32", 32)
    d["f64"] = Ty("float", "f64", "f64", 64)
    d["bool"] = Ty("bool", "bool", "bool", 8)
    return d


PRIM = _mk_prims()
I64, F64, BOOL, U8 = PRIM["i64"], PRIM["f64"], PRIM["bool"], PRIM["u8"]
CPRIM = {"i8": "int8_t", "i16": "int16_t", "i32": "int32_t", "i64": "int64_t",
         "u8": "uint8_t", "u16": "uint16_t", "u32": "uint32_t", "u64": "uint64_t",
         "f32": "float", "f64": "double", "bool": "uint8_t"}


def arr_ty(elem, n):
    return Ty("arr", "%s[%d]" % (elem.key, n), elem=elem, n=n)


def slice_ty(elem):
    return Ty("slice", "%s[:]" % elem.key, elem=elem)


def grow_ty(elem, shrink=False):
    return Ty("grow", "%s[>..%s]" % (elem.key, "<" if shrink else ""), elem=elem, n=1 if shrink else 0)


def lim_ty(elem, cap):
    return Ty("lim", "%s[..%d]" % (elem.key, cap), elem=elem, n=cap)


def frozen_ty(elem):
    return Ty("frozen", "%s[]" % elem.key, elem=elem)


SEQ = ("arr", "slice", "grow", "lim", "frozen")


def ref_ty(elem, width=0):
    """`width` is the byte width of a relative reference `T&<u32>` (0: a plain reference); it is
    not part of the type's identity, only of the size Goose's layout gives it."""
    return Ty("ref", "%s&" % elem.key, elem=elem, n=width)


def opt_ty(elem, width=0):
    return Ty("opt", "%s?" % elem.key, elem=elem, n=width)


NULLTY = Ty("null", "null")


class StructInfo:
    def __init__(self, name, decl, env=None, cn=None, targs=None):
        self.name, self.decl = name, decl
        self.env = env or {}                     # the type arguments of a generic struct's instance
        self.cn = cn or name                     # the C name
        self.gname, self.targs = name, targs
        self.display = name
        self.root = bool(getattr(decl, "root", False))
        self.fields = None                       # [(name, Ty, default node)]
        self.ty = Ty("struct", "struct:" + self.cn, name=name, info=self)


class EnumInfo:
    def __init__(self, name, decl):
        self.name, self.decl = name, decl
        self.variants = None                     # [(name, [(fname, Ty, default)])]
        self.ty = Ty("enum", "enum:" + name, name=name, info=self)

    def variant_ty(self, idx):
        name = self.decl.variants[idx].name
        return Ty("variant", "%s.%s" % (self.name, name), name=name, n=idx, info=self)


class Val:
    """A typed C expression.  `lit` is the Python value of a constant that has
    not yet taken a type; `lv` says the code is an lvalue; `extra` are the
    further results of a multi-result call."""
    __slots__ = ("code", "ty", "lit", "lv", "extra", "pure", "stk", "pay", "origin")

    def __init__(self, code, ty, lit=None, lv=False, extra=None, pure=False, stk=None, pay=None, origin=None):
        self.code, self.ty, self.lit, self.lv, self.extra, self.pure = code, ty, lit, lv, extra, pure
        self.stk = stk                           # the data stack a growable lives on
        self.pay = pay                           # the stack holding the payloads of its frozen elements
        self.origin = origin                     # frozen values: "static" (literal data) or "call" (a result)


def mangle(key):
    out = []
    for c in key:
        out.append(c if c.isalnum() else "_")
    return "".join(out)


# ---------------------------------------------------------------------------
# The runtime

RUNTIME = r'''
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

double strtod(const char *, char **);   /* shivyc's <stdlib.h> does not declare it */

typedef uint8_t gs_bool;

static void gs_abort(const char *msg, int line) {
    fflush(stdout);
    fprintf(stderr, "goose: %s (line %d)\n", msg, line);
    exit(1);
}
static int64_t gs_chk(int64_t i, int64_t n, int line) {
    if ((uint64_t)i >= (uint64_t)n) gs_abort("index out of range", line);
    return i;
}
#define GS_INTOPS(T, U, N) \
    static T gs_div_##N(T a, T b, int line) { if (b == 0) gs_abort("division by zero", line); return (T)(a / b); }
#define GS_SMOD(T, N) \
    static T gs_mod_##N(T a, T b, int line) { T r; if (b == 0) gs_abort("division by zero", line); \
        r = (T)(a % b); if (r < 0) r = (T)(r + (b < 0 ? -b : b)); return r; }
#define GS_UMOD(T, N) \
    static T gs_mod_##N(T a, T b, int line) { if (b == 0) gs_abort("division by zero", line); return (T)(a % b); }
GS_INTOPS(int8_t, uint32_t, i8)   GS_SMOD(int8_t, i8)
GS_INTOPS(int16_t, uint32_t, i16) GS_SMOD(int16_t, i16)
GS_INTOPS(int32_t, uint32_t, i32) GS_SMOD(int32_t, i32)
GS_INTOPS(int64_t, uint64_t, i64) GS_SMOD(int64_t, i64)
GS_INTOPS(uint8_t, uint32_t, u8)  GS_UMOD(uint8_t, u8)
GS_INTOPS(uint16_t, uint32_t, u16) GS_UMOD(uint16_t, u16)
GS_INTOPS(uint32_t, uint32_t, u32) GS_UMOD(uint32_t, u32)
GS_INTOPS(uint64_t, uint64_t, u64) GS_UMOD(uint64_t, u64)

/* Output goes to stdout, or into a byte array while `format`/`str` run. */
static uint8_t *gs_sbuf, **gs_stop;
static int64_t *gs_slen, gs_scap;
static void gs_write(const void *p, int64_t n) {
    if (gs_slen) {
        if (gs_scap >= 0 && *gs_slen + n > gs_scap) gs_abort("limited array is full", 0);
        memcpy(gs_sbuf + *gs_slen, p, (size_t)n);
        *gs_slen += n;
        if (gs_stop) *gs_stop += n;
    } else fwrite(p, 1, (size_t)n, stdout);
}
static void gs_p_lit(const char *s) { gs_write(s, (int64_t)strlen(s)); }
static void gs_p_i64(int64_t v) { char t[32]; int n = snprintf(t, sizeof t, "%lld", (long long)v); gs_write(t, n); }
static void gs_p_u64(uint64_t v) { char t[32]; int n = snprintf(t, sizeof t, "%llu", (unsigned long long)v); gs_write(t, n); }
static void gs_p_bool(uint8_t v) { gs_p_lit(v ? "true" : "false"); }
static void gs_p_raw(const uint8_t *p, int64_t n) { gs_write(p, n); }
static void gs_p_quoted(const uint8_t *p, int64_t n) { gs_write("\"", 1); gs_write(p, n); gs_write("\"", 1); }

#define GS_STACK_BYTES ((int64_t)1 << 28)
static int gs_rf;
static int gs_argc;
static char **gs_argv;
static uint8_t **gs_top, **gs_lim;
static void gs_stacks(int n) {
    int i;
    gs_top = (uint8_t **)malloc(sizeof(uint8_t *) * (size_t)(n + 1));
    gs_lim = (uint8_t **)malloc(sizeof(uint8_t *) * (size_t)(n + 1));
    for (i = 0; i < n; i++) {
        gs_top[i] = (uint8_t *)malloc((size_t)GS_STACK_BYTES);
        if (!gs_top[i]) gs_abort("cannot reserve a data stack", 0);
        gs_lim[i] = gs_top[i] + GS_STACK_BYTES;
    }
}
static void gs_p_float(double v, int is32) {
    char t[64];
    int p, lo = is32 ? 6 : 15, hi = is32 ? 9 : 17;
    if (v != v) { gs_p_lit("nan"); return; }
    if (v > 1.7976931348623157e308 || v < -1.7976931348623157e308) { gs_p_lit(v < 0 ? "-inf" : "inf"); return; }
    for (p = lo; p <= hi; p++) {
        snprintf(t, sizeof t, "%.*g", p, v);
        if (is32 ? ((float)strtod(t, 0) == (float)v) : (strtod(t, 0) == v)) break;
    }
    gs_p_lit(t);
    if (!strchr(t, '.') && !strchr(t, 'e')) gs_p_lit(".0");
}
'''


# ---------------------------------------------------------------------------
# The generator

class Var:
    __slots__ = ("code", "ty", "ref", "mutable", "stk", "lit", "pay")

    def __init__(self, code, ty, ref=False, mutable=True, stk=None, lit=None, pay=None):
        self.code, self.ty, self.ref, self.mutable, self.stk, self.lit, self.pay = code, ty, ref, mutable, stk, lit, pay


class Fn:
    """A function instance being or having been emitted."""
    def __init__(self, decl, cname):
        self.decl, self.cname = decl, cname
        self.rets = None                         # [Ty] once known
        self.params = None


class Env:
    """A lexical scope.  `parent` is where names resolve; `dyn` is the scope
    that was current when this one was entered (they differ for the body of an
    inlined function, which sees the globals, not its caller's locals), and is
    the chain a `return` releases data stacks along."""
    __slots__ = ("vars", "parent", "dyn", "frees")

    def __init__(self, parent=None, dyn=None):
        self.vars, self.parent, self.dyn, self.frees = {}, parent, dyn, []


class Frame:
    """What `return` means here: the real function, or an inlined one."""
    def __init__(self, kind, rets, label=None, env=None, trial=False):
        self.kind, self.rets, self.label, self.env, self.trial = kind, rets, label, env, trial
        self.name = None
        self.res = []                # inline: one Branch per result
        self.exit_env = None
        self.used = False


FNTY = Ty("fn", "fn")


class FnVal:
    """A static function value: a block, a named function or a nested function.
    Calls of it are expanded inline, where they are written."""
    ty = FNTY
    ref = False
    mutable = False
    stk = None
    lit = None
    pay = None

    def __init__(self, kind, env, frame, tenv, node=None, decls=None, name=None):
        self.kind, self.env, self.frame, self.tenv = kind, env, frame, tenv
        self.node, self.decls, self.name = node, decls, name
        self.ns = ""


class Ctx:
    """Per-function emission state."""
    def __init__(self, fn):
        self.fn = fn
        self.out = []
        self.indent = 1
        self.env = Env()
        self.cnames = set()
        self.frame = Frame("fn", None)
        self.ntmp = 0
        self.breakables = []                     # (kind, tmp, label, valty)
        self.rets_known = fn.rets is not None
        self.outs = []                           # out-pointer param names for results 1..


class Gen:
    def __init__(self, path="<goose>"):
        self.path = path
        self.structs = {}                        # name -> StructInfo
        self.gstructs = {}                      # name -> decl of a generic struct
        self.fn_env = {}
        self.rf_throws = {}
        self.rf_ids = {}
        self.deferred_decls = {}
        self.sinst = {}
        self.enums = {}
        self.helper_defs = []
        self.fns = {}                            # name -> [decl]
        self.libs = set()
        self.lib_globals = {}
        self.extern_done = set()
        self.globals = {}                        # name -> Var
        self.global_decls = {}
        self.aliases = {}
        self.types_out = []                      # typedef text, in dependency order
        self.defined = set()
        self.protos = []
        self.bodies = []
        self.printers = {}                       # key -> cname
        self.printer_defs = []
        self.printer_protos = []
        self.instances = {}                      # key -> Fn
        self.ninst = 0
        self.init_lines = []
        self.ctx = None
        self.ncname = 0
        self.nstacks = 0

    # -- output helpers --------------------------------------------------------
    def emit(self, line):
        self.ctx.out.append("    " * self.ctx.indent + line)

    def tmp(self, ty_c=None, init=None, ty=None):
        c = self.ctx
        c.ntmp += 1
        name = "t%d" % c.ntmp
        if ty_c is not None:
            self.emit("%s %s%s;" % (ty_c, name, "" if init is None else " = " + init))
        return name

    def spill(self, v):
        """Evaluate `v` once, into a temporary."""
        if v.lit is not None or v.pure:
            return v
        t = self.tmp(self.ctype(v.ty), v.code)
        return Val(t, v.ty, lv=True, pure=True)

    class _Capture:
        def __init__(self, gen):
            self.gen = gen

        def __enter__(self):
            self.saved = self.gen.ctx.out
            self.gen.ctx.out = self.lines = []
            return self.lines

        def __exit__(self, *a):
            self.gen.ctx.out = self.saved

    def capture(self):
        return Gen._Capture(self)

    # -- scopes --------------------------------------------------------------------
    def push(self):
        self.ctx.env = Env(self.ctx.env, self.ctx.env)

    def pop(self):
        e = self.ctx.env
        for k, base in reversed(e.frees):
            self.emit("gs_top[%s] = (uint8_t *)%s;" % (k, base))
        self.ctx.env = e.dyn

    def unwind(self, to_env):
        """Release the data stacks of every scope from the innermost out to
        (not including) `to_env`; None means all of them."""
        e = self.ctx.env
        while e is not None and e is not to_env:
            for k, base in reversed(e.frees):
                self.emit("gs_top[%s] = (uint8_t *)%s;" % (k, base))
            e = e.dyn

    def has_frees(self, to_env=None):
        e = self.ctx.env
        while e is not None and e is not to_env:
            if e.frees:
                return True
            e = e.dyn
        return False

    def new_stack(self):
        self.nstacks += 1
        return self.nstacks - 1

    def lookup(self, name):
        e = self.ctx.env
        while e is not None:
            if name in e.vars:
                return e.vars[name]
            e = e.parent
        g = self.globals.get(name)
        if g is None and name in self.lib_globals:
            g = self.materialize_global(name)
        return g

    def materialize_global(self, name):
        """A library global is set up the first time something names it."""
        d = self.lib_globals.pop(name)
        saved, saved_ns = self.ctx, getattr(self, "cur_ns", "")
        fake = Fn(None, "gs_init")
        fake.rets = []
        self.ctx = Ctx(fake)
        self.cur_ns = name.rpartition("::")[0]
        self.fn_env = {}
        try:
            self.global_decl(d)
            lines = self.ctx.out
        finally:
            self.ctx, self.cur_ns = saved, saved_ns
        self.init_lines.extend(lines)
        return self.globals.get(name)

    def declare(self, name, ty, mutable=True, ref=False, line=0, stk=None):
        c = self.ctx
        cname = "%s_g" % name
        if cname in c.cnames:                           # shadowing, or an inlined body: a fresh C name
            c.ntmp += 1
            cname = "%s_g%d" % (name, c.ntmp)
        c.cnames.add(cname)
        v = Var(cname, ty, ref, mutable, stk)
        c.env.vars[name] = v
        return v

    # -- types ---------------------------------------------------------------------
    def ctype(self, t):
        if t is None:
            return "void"
        k = t.k
        if k in ("int", "float", "bool"):
            return CPRIM[t.name]
        if k == "arr":
            name = "gs_arr_%s_%d" % (mangle(t.elem.key), t.n)
            if name not in self.defined:
                et = self.ctype(t.elem)
                self.defined.add(name)
                self.types_out.append("typedef struct { %s a[%d]; } %s;" % (et, max(t.n, 1), name))
            return name
        if k in ("slice", "frozen"):
            name = "gs_sl_%s" % mangle(t.elem.key)
            if name not in self.defined:
                et = self.ctype(t.elem)
                self.defined.add(name)
                self.types_out.append("typedef struct { %s *data; int64_t len; } %s;" % (et, name))
            return name
        if k in ("ref", "opt"):
            return self.ctype(t.elem) + " *"
        if k == "variant":
            return self.ctype(t.info.ty)
        if k == "enum":
            info = t.info
            name = "%s_g" % info.name
            if name not in self.defined:
                self.defined.add(name)
                variants = self.enum_variants(info)
                members = []
                for vname, fields in variants:
                    if fields:
                        members.append("struct { %s } %s;" % (
                            " ".join("%s f_%s;" % (self.ctype(ft), fn) for fn, ft, _ in fields), vname))
                union = "union { %s } u;" % " ".join(members) if members else "uint8_t pad;"
                self.types_out.append("typedef struct { uint8_t tag; %s } %s;" % (union, name))
            return name
        if k == "grow":
            name = "gs_gr_%s" % mangle(t.elem.key)
            if name not in self.defined:
                et = self.ctype(t.elem)
                self.defined.add(name)
                self.types_out.append("typedef struct { %s *base; int64_t len; int stk; } %s;" % (et, name))
            return name
        if k == "lim":
            name = "gs_lm_%s_%d" % (mangle(t.elem.key), t.n)
            if name not in self.defined:
                et = self.ctype(t.elem)
                self.defined.add(name)
                self.types_out.append("typedef struct { int64_t len; %s a[%d]; } %s;" % (et, max(t.n, 1), name))
            return name
        if k == "struct":
            info = t.info
            name = "%s_g" % info.cn
            if name not in self.defined:
                self.struct_fields(info)
                self.defined.add(name)
                self.types_out.append("typedef struct %s %s;" % (name, name))        # fields may point back
                parts = ["%s %s;" % (self.ctype(ft), "f_" + fn) for fn, ft, _ in info.fields]
                if not parts:
                    parts = ["uint8_t pad;"]
                self.types_out.append("struct %s { %s };" % (name, " ".join(parts)))
            return name
        raise AssertionError(k)

    def enum_variants(self, info):
        if info.variants is None:
            info.variants = []
            out = []
            for v in info.decl.variants:
                fields = []
                for f in (v.fields or []):
                    if f.kind == "Pad":
                        continue
                    fields.append((f.name, self.resolve(f.type, {}), f.default))
                out.append((v.name, fields))
            info.variants = out
        return info.variants

    def variant_ctor(self, info, idx):
        """The C function that builds variant `idx` of the enum from its fields."""
        vname, fields = self.enum_variants(info)[idx]
        name = "%s_mk_%s" % (info.name, vname)
        if name not in self.defined:
            self.defined.add(name)
            c = self.ctype(info.ty)
            params = ", ".join("%s f_%s" % (self.ctype(ft), fn) for fn, ft, _ in fields) or "void"
            body = ["static %s %s(%s) {" % (c, name, params),
                    "    %s r;" % c, "    memset(&r, 0, sizeof r);", "    r.tag = %d;" % idx]
            for fn, _, _ in fields:
                body.append("    r.u.%s.f_%s = f_%s;" % (vname, fn, fn))
            body += ["    return r;", "}"]
            self.helper_defs += body
        return name

    def struct_inst(self, name, args, line):
        decl = self.gstructs[name]
        names = [g[0] for g in decl.generics]
        if len(args) != len(names):
            refuse("%s takes %d type argument(s), found %d" % (name, len(names), len(args)), line)
        key = (name, tuple(a.key for a in args))
        info = self.sinst.get(key)
        if info is None:
            cn = "%s__%s" % (name, "_".join(mangle(a.key) for a in args))
            info = StructInfo(name, decl, dict(zip(names, args)), cn, args)
            info.display = "%s<%s>" % (name, ", ".join(a.info.display if a.k == "struct" else a.key for a in args))
            self.sinst[key] = info
        return info

    def struct_fields(self, info):
        if info.fields is None:
            info.fields = []                            # guards recursion
            fields = []
            for f in info.decl.fields:
                if f.kind == "Pad":
                    continue
                fields.append((f.name, self.resolve(f.type, info.env), f.default))
            info.fields = fields
        return info.fields

    def resolve(self, node, env):
        k = node.kind
        if k == "Prim":
            if node.name == "varint":
                unsupported("'varint'", node.line)
            return PRIM[node.name]
        if k == "Named":
            q = node.name
            if q.ns:
                unsupported("a namespaced type", node.line)
            if q.name in env:
                return env[q.name]
            if q.name in self.gstructs:
                return self.struct_inst(q.name, [self.resolve(a, env) for a in (node.args or [])], node.line).ty
            if node.args:
                unsupported("a generic type", node.line)
            if q.name in self.aliases:
                return self.resolve(self.aliases[q.name].type, env)
            if q.name in self.structs:
                return self.structs[q.name].ty
            if q.name in self.enums:
                return self.enums[q.name].ty
            refuse("unknown type '%s'" % q.name, node.line)
        if k == "VariantType":
            base = self.resolve(node.type, env)
            if base.k != "enum":
                refuse("%s is not an enum" % base.key, node.line)
            names = [v.name for v in base.info.decl.variants]
            if node.variant not in names:
                refuse("%s has no variant '%s'" % (base.name, node.variant), node.line)
            return base.info.variant_ty(names.index(node.variant))
        if k == "Array":
            if node.mode == "fixed":
                n = self.const_int(node.size, env)
                return arr_ty(self.resolve(node.elem, env), n)
            if node.mode in ("grow", "grow_shrink"):
                return grow_ty(self.resolve(node.elem, env), node.mode == "grow_shrink")
            if node.mode == "limited" and node.size is not None:
                return lim_ty(self.resolve(node.elem, env), self.const_int(node.size, env))
            if node.mode == "var" and node.size is None:
                return frozen_ty(self.resolve(node.elem, env))
            unsupported("the %s array kind" % (
                "T[%s]" % node.size if node.mode == "var" else "T[..] with a capacity of its own"), node.line)
        if k == "Slice":
            return slice_ty(self.resolve(node.elem, env))
        if k == "Const":
            return self.resolve(node.type, env)
        if k == "Opt":
            inner = self.resolve(node.type, env)
            width = 0
            if inner.k == "ref":
                width = inner.n
                inner = inner.elem
            if inner.k == "opt":
                refuse("'?' on an already optional type", node.line)
            return opt_ty(inner, width)
        if k == "Ref":
            # `T&<u32 in pool>` differs from `T&` only in size (an offset instead of an address);
            # as a pointer it means the same, so layouts that show the size are what is left out
            inner = self.resolve(node.type, env)
            if inner.k in ("ref", "opt"):
                refuse("a reference to a reference", node.line)
            width = int(node.rel[0][1:]) // 8 if node.rel is not None else 0
            return ref_ty(inner, width)
        if k == "VarMode":
            unsupported("a variable-mode enum", node.line)
        unsupported("this type", node.line)

    def const_int(self, node, env):
        k = node.kind
        if k == "Int":
            return node.value
        if k == "Paren":
            return self.const_int(node.expr, env)
        if k == "Name" and node.name in self.global_decls:
            d = self.global_decls[node.name]
            if d.inits:
                return self.const_int(d.inits[0], env)
        if k == "Binary" and node.op in ("+", "-", "*", "/", "%", "<<"):
            a, b = self.const_int(node.left, env), self.const_int(node.right, env)
            return {"+": a + b, "-": a - b, "*": a * b, "/": a // b if b else 0,
                    "%": a % b if b else 0, "<<": a << b}[node.op]
        refuse("expected a constant integer", node.line)

    # -- literals and coercion -----------------------------------------------------
    def lit_code(self, value, ty):
        if ty.k == "int":
            if ty.bits == 64 and not ty.signed:
                return "%dULL" % value
            if ty.bits == 64 and value == -(1 << 63):
                return "(-9223372036854775807LL - 1)"
            if ty.bits == 64:
                return "%dLL" % value
            return "((%s)%d)" % (CPRIM[ty.name], value)
        if ty.k == "float":
            r = repr(float(value))
            if "nan" in r:
                r = "(0.0/0.0)"
            elif "inf" in r:
                r = "(-1.0/0.0)" if r.startswith("-") else "(1.0/0.0)"
            return "((%s)%s)" % (CPRIM[ty.name], r)
        if ty.k == "bool":
            return "1" if value else "0"
        raise AssertionError

    def fits(self, value, ty):
        if ty.k == "float":
            return True
        if ty.k != "int" or isinstance(value, float):
            return False
        lo = -(1 << (ty.bits - 1)) if ty.signed else 0
        hi = (1 << (ty.bits - 1)) - 1 if ty.signed else (1 << ty.bits) - 1
        return lo <= value <= hi

    def default_lit(self, v):
        """A constant that met no type: i64 or f64."""
        if v.lit is None or v.ty is not None:
            return v
        ty = F64 if isinstance(v.lit, float) else I64
        if ty is I64 and not self.fits(v.lit, I64):
            ty = PRIM["u64"]
        return Val(self.lit_code(v.lit, ty), ty, lit=None, pure=True)

    def widen_ok(self, src, dst):
        if src == dst:
            return True
        if src.k == "int" and dst.k == "int":
            if src.signed == dst.signed:
                return dst.bits > src.bits
            return (not src.signed) and dst.signed and dst.bits > src.bits
        if src.k == "int" and dst.k == "float":
            return True                                  # implicit, rounding to the nearest float
        if src.k == "float" and dst.k == "float":
            return dst.bits > src.bits
        return False

    def coerce(self, v, ty, line, what="value"):
        """Make `v` a value of type `ty`, or refuse."""
        if ty.k in ("ref", "opt"):
            if v.ty is NULLTY:
                if ty.k == "opt":
                    return Val("0", ty, pure=True)
                refuse("'null' is not a %s" % ty.key, line)
            if v.ty == ty:
                return v
            if ty.k == "opt" and v.ty is not None and v.ty.k == "ref" and v.ty.elem == ty.elem:
                return Val(v.code, ty, pure=v.pure)
            if v.ty is not None and v.ty == ty.elem and v.lv:
                return Val(self.addr(v.code), ty, pure=v.pure, stk=v.stk, pay=v.pay)
            if v.ty is not None and v.ty.k == "opt" and ty.k == "ref" and v.ty.elem == ty.elem:
                refuse("this may be null: test it first (if / guard)", line)
            if v.ty is None:
                refuse("a reference needs a variable, not a constant", line)
            refuse("expected %s, found %s" % (ty.key, v.ty.key), line)
        if v.ty is NULLTY:
            refuse("expected %s, found null" % ty.key, line)
        if v.ty is None:                                # an untyped constant
            if v.lit is not None:
                if ty.k in ("int", "float") and (isinstance(v.lit, float) is False or ty.k == "float"):
                    if isinstance(v.lit, int) and not self.fits(v.lit, ty):
                        refuse("%s %d does not fit %s" % (what, v.lit, ty.key), line)
                    return Val(self.lit_code(v.lit, ty), ty, pure=True)
                refuse("expected %s, found a %s constant" % (ty.key, "float" if isinstance(v.lit, float) else "integer"), line)
        if v.ty == ty:
            return v
        if ty.k == "enum" and v.ty.k == "variant" and v.ty.info is ty.info:
            return Val(v.code, ty, lv=v.lv, pure=v.pure)
        if self.widen_ok(v.ty, ty):
            return Val("((%s)%s)" % (self.ctype(ty), v.code), ty, pure=v.pure)
        if ty.k == "slice" and v.ty.k in ("arr", "grow", "lim", "frozen") and v.ty.elem == ty.elem:
            return self.arr_to_slice(v, ty)
        if ty.k == "frozen" and v.ty.k in ("arr", "slice", "grow", "lim") and v.ty.elem == ty.elem:
            return self.make_frozen(v, ty, line)
        if ty.k == "lim" and v.ty.k in ("slice", "arr", "grow", "lim") and v.ty.elem == ty.elem:
            # a sequence copied into a limited array, as long as it fits
            v = self.spill(v) if not v.pure else v
            data, ln = self.seq_parts(v)
            t = self.tmp(self.ctype(ty))
            self.emit("memset(&%s, 0, sizeof %s);" % (t, t))
            self.emit("if ((int64_t)%s > %d) gs_abort(\"too long for %s\", %d);" % (ln, ty.n, ty.key, line))
            self.emit("if ((int64_t)%s > 0) memcpy(%s.a, %s, (size_t)%s * sizeof(%s));" %
                      (ln, t, data, ln, self.ctype(ty.elem)))
            self.emit("%s.len = (int64_t)%s;" % (t, ln))
            return Val(t, ty, lv=True, pure=True)
        refuse("expected %s, found %s" % (ty.key, v.ty.key if v.ty else "nothing"), line)

    def addr(self, code):
        if code.startswith("(*") and code.endswith(")") and code[2:-1].replace("_", "").isalnum():
            return code[2:-1]
        return "(&%s)" % code

    def make_frozen(self, v, ty, line, force=False):
        """A frozen array holding `v`'s elements: literal text stays where it is, anything
        else is copied onto a data stack of its own, released at the scope's end."""
        if v.origin == "static" and not force:
            return Val(v.code, ty, pure=True, origin="static")
        v = self.spill(v) if not v.pure else v
        data, ln = self.seq_parts(v)
        e = self.ctype(ty.elem)
        k = self.new_stack()
        base = self.tmp("uint8_t *", "gs_top[%d]" % k)
        self.ctx.env.frees.append((k, base))
        n = self.tmp("int64_t", "(int64_t)%s" % ln)
        self.emit("if (gs_top[%d] + (size_t)%s * sizeof(%s) > gs_lim[%d]) gs_abort(\"data stack overflow\", %d);" %
                  (k, n, e, k, line))
        p = self.tmp("%s *" % e, "(%s *)gs_top[%d]" % (e, k))
        self.emit("gs_top[%d] += (size_t)%s * sizeof(%s);" % (k, n, e))
        self.emit("if (%s > 0) memcpy(%s, %s, (size_t)%s * sizeof(%s));" % (n, p, data, n, e))
        return Val("((%s){%s, %s})" % (self.ctype(ty), p, n), ty, pure=True)

    def arr_to_slice(self, v, ty):
        v = self.spill(v) if not v.lv else v
        data, length = self.seq_parts(v)
        return Val("((%s){%s, %s})" % (self.ctype(ty), data, length), ty, pure=True)

    # -- top level -------------------------------------------------------------------
    def stdlib_path(self, name):
        roots = [os.environ.get("GOOSE_ROOT"),
                 os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "goose"),
                 os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "goose")]
        for r in roots:
            if not r:
                continue
            for sub in ("stdlib", "", "."):
                f = os.path.join(r, sub, name + ".goose")
                if os.path.exists(f):
                    return f
        return None

    def load_lib(self, name, line):
        """Register a library module's declarations (compiled only if used)."""
        if name in self.libs:
            return
        self.libs.add(name)
        f = self.stdlib_path(name)
        if f is None:
            unsupported("'import %s' (the Goose library source was not found; set GOOSE_ROOT)" % name, line)
        with open(f) as fh:
            decls = parse(fh.read(), f)
        for d in decls:
            k = d.kind
            if k == "FnDecl" and d.body is not None and not d.thread and "extern" not in d.quals \
                    and "export" not in d.quals:
                d.lib = True
                self.fns.setdefault(d.name, []).append(d)
            elif k == "FnDecl" and "extern" in d.quals and d.body is None and not d.thread:
                d.lib = True
                self.fns.setdefault(d.name, []).append(d)
            elif k == "Struct" and d.generics:
                self.gstructs.setdefault(d.name, d)
            elif k == "Struct":
                self.structs.setdefault(d.name, StructInfo(d.name, d))
            elif k == "Enum" and not d.generics:
                self.enums.setdefault(d.name, EnumInfo(d.name, d))
            elif k == "TypeAlias":
                self.aliases.setdefault(d.name, d)
            elif k == "Import" and d.source is None:
                self.load_lib(d.name, d.line)
            elif k == "Decl" and len(d.names) == 1:
                self.lib_globals[d.names[0]] = d

    def module(self, decls):
        order = []
        for d in decls:
            k = d.kind
            if k == "Struct":
                if d.generics:
                    self.gstructs[d.name] = d
                else:
                    self.structs[d.name] = StructInfo(d.name, d)
            elif k == "TypeAlias":
                self.aliases[d.name] = d
            elif k == "FnDecl":
                if d.thread:
                    unsupported("'thread_fn'", d.line)
                if "extern" in d.quals or "export" in d.quals:
                    unsupported("'extern fn'", d.line)
                self.fns.setdefault(d.name, []).append(d)
            elif k == "Decl":
                for n in d.names:
                    self.global_decls[n] = d
                order.append(d)
            elif k == "Import":
                if d.source is not None:
                    unsupported("'import ... from'", d.line)
                self.load_lib(d.name, d.line)
            elif k == "Enum":
                if d.generics:
                    unsupported("a generic enum", d.line)
                self.enums[d.name] = EnumInfo(d.name, d)
            elif k == "Namespace":
                unsupported("'namespace'", d.line)
            else:
                unsupported(k, d.line)
        self.scan_long_returns(decls)
        # globals first: they are initialised before main runs
        fake = Fn(None, "gs_init")
        fake.rets = []
        self.ctx = Ctx(fake)
        for d in order:
            self.global_decl(d)
        self.init_lines = self.init_lines + self.ctx.out
        self.ctx = None
        if "main" not in self.fns:
            refuse("no 'main' function", 1)
        main = self.instantiate(self.fns["main"][0], [], [], self.fns["main"][0].line)
        return self.assemble(main)

    def scan_long_returns(self, decls):
        """`return E from f` out of a function other than f: find which functions can throw
        to which targets, so that exactly the calls that may need it check a flag."""
        calls, direct, self.rf_ids = {}, {}, {}

        def walk(x, acc, fname):
            if isinstance(x, N):
                if x.kind == "FnDecl" and x.body is not None:        # a nested function is its own node
                    inner = calls.setdefault(x.name, set())
                    acc.add(x.name)
                    walk(x.body, inner, x.name)
                    return
                if x.kind == "Return" and x.frm is not None:
                    t = x.frm.name if hasattr(x.frm, "name") else str(x.frm)
                    direct.setdefault(fname, set()).add(t)
                    self.rf_ids.setdefault(t, len(self.rf_ids) + 1)
                if x.kind == "Call":
                    f = x.fn
                    if f.kind in ("Name", "Field"):
                        acc.add(f.name)
                for v in vars(x).values():
                    walk(v, acc, fname)
            elif isinstance(x, (list, tuple)):
                for v in x:
                    walk(v, acc, fname)

        for d in decls:
            if d.kind == "FnDecl" and d.body is not None:
                acc = calls.setdefault(d.name, set())
                walk(d.body, acc, d.name)
        throws = {f: set(t) for f, t in direct.items()}
        changed = True
        while changed and self.rf_ids:
            changed = False
            for f, callees in calls.items():
                cur = throws.setdefault(f, set())
                before = len(cur)
                for g in callees:
                    for key in (g, g.rpartition("::")[2]):
                        if key in throws:
                            cur |= {t for t in throws[key] if t != f}
                if len(cur) != before:
                    changed = True
        self.rf_throws = {f: t - {f} for f, t in throws.items() if t - {f}}
        self.rf_targets_decl = {}

    def rf_check(self, callee_name, line):
        """After a call of a function that may throw: land here or pass it on."""
        thrown = set()
        for key in (callee_name, callee_name.rpartition("::")[2]):
            thrown |= self.rf_throws.get(key, set())
        if not thrown:
            return
        fn = self.ctx.fn
        me = fn.decl.name if fn.decl else "main"
        mine = [t for t in thrown if t == me or t == me.rpartition("::")[2]]
        self.emit("if (gs_rf) {")
        self.ctx.indent += 1
        if mine:
            tid = self.rf_ids[mine[0]]
            self.emit("if (gs_rf == %d) {" % tid)
            self.ctx.indent += 1
            self.emit("gs_rf = 0;")
            self.unwind(None)
            rets = fn.rets or []
            for i in range(1, len(rets)):
                self.emit("*gs_o%d = gs_rv%d_%d;" % (i, tid, i))
            self.emit("return%s;" % (" gs_rv%d_0" % tid if rets else ""))
            self.ctx.indent -= 1
            self.emit("}")
        self.unwind(None)
        self.emit_junk_return(fn)
        self.ctx.indent -= 1
        self.emit("}")

    def may_throw(self, name):
        return bool(self.rf_throws.get(name) or self.rf_throws.get(name.rpartition("::")[2]))

    def emit_junk_return(self, fn):
        rets = fn.rets
        if not rets:
            self.emit("return;")
        elif rets[0].k in ("int", "float", "bool", "ref", "opt"):
            self.emit("return 0;")
        else:
            j = self.tmp(self.ctype(rets[0]))
            self.emit("memset(&%s, 0, sizeof %s);" % (j, j))
            self.emit("return %s;" % j)

    def global_decl(self, d):
        if d.reusable:
            unsupported("'reusable'", d.line)
        if len(d.names) != 1 or len(d.inits) > 1:
            unsupported("a multi-name global", d.line)
        name = d.names[0]
        ty = self.resolve(d.type, {}) if d.type else None
        if d.inits and d.inits[0].kind == "StructLit":
            rt = self.lit_struct_type(d.inits[0])
            if rt is not None and self.owns_stacks(rt):
                return self.declare_root(name, d.mode == "var", rt, d.inits[0], d.line, glob=True)
        if ty is not None and ty.k == "grow":
            k = self.new_stack()
            cname = "%s_g" % name.replace("::", "_")
            self.globals[name] = Var(cname, ty, False, d.mode == "var", stk=k)
            self.global_defs = getattr(self, "global_defs", [])
            self.global_defs.append("static %s %s;" % (self.ctype(ty), cname))
            self.emit("%s.base = (%s *)gs_top[%s]; %s.len = 0; %s.stk = %s;" % (cname, self.ctype(ty.elem), k, cname, cname, k))
            if d.inits:
                init = d.inits[0]
                if init.kind != "ArrayLit" or init.fill is not None or getattr(init, "cap", None):
                    unsupported("this initializer for a growable global", d.line)
                recv = Val(cname, ty, lv=True, pure=True, stk=k)
                for v in self.operands(init.items, [ty.elem] * len(init.items)):
                    self.emit_push(recv, self.coerce(v, ty.elem, d.line), d.line)
            return
        if d.inits:
            v = self.ex(d.inits[0], ty)
            if ty is None and v.ty is None and v.lit is not None and d.mode == "const":
                self.globals[name] = Var("", I64, False, False, lit=v.lit)
                return
            v = self.coerce(v, ty, d.line) if ty else self.default_lit(v)
            ty = v.ty
        if ty is None:
            refuse("a global needs a type or an initializer", d.line)
        cname = "%s_g" % name.replace("::", "_")
        self.globals[name] = Var(cname, ty, False, d.mode == "var")
        self.global_defs = getattr(self, "global_defs", [])
        self.global_defs.append("static %s %s;" % (self.ctype(ty), cname))
        if d.inits:
            self.emit("%s = %s;" % (cname, v.code))

    def assemble(self, main):
        out = ["/* Generated by tools/goose2c.py from a Goose program. Do not edit. */",
               RUNTIME.strip("\n"), ""]
        out += self.types_out
        out += getattr(self, "global_defs", [])
        out += self.helper_defs
        out += self.printer_protos
        out += self.protos
        out += self.printer_defs
        out += self.bodies
        out.append("static void gs_init(void) {")
        out.append("    gs_stacks(%d);" % self.nstacks)
        out += self.init_lines
        out.append("}")
        out.append("int main(int argc, char **argv) { gs_argc = argc; gs_argv = argv; gs_init(); %s(); fflush(stdout); return 0; }" % main.cname)
        return "\n".join(out) + "\n"

    # -- functions ---------------------------------------------------------------
    def instantiate(self, decl, arg_tys, targs, line):
        """The instance of `decl` for these parameter types (None for
        parameters whose type the declaration gives)."""
        key = (id(decl), tuple(t.key for t in arg_tys), tuple(t.key for t in targs))
        if key in self.instances:
            return self.instances[key]
        self.ninst += 1
        base = decl.name.replace("::", "_")
        cname = "%s_g%s" % (base, "" if len(self.fns.get(decl.name, [])) == 1 and not decl.generics
                            and all(p.type is not None for p in decl.params) else "_%d" % self.ninst)
        fn = Fn(decl, cname)
        self.instances[key] = fn
        env = {}
        ptys = []
        if len(arg_tys) != len(decl.params):
            raise AssertionError("arity")
        for p, at in zip(decl.params, arg_tys):
            ptys.append(at)
        fn.params = ptys
        if decl.generics:
            env.update(zip([g[0] for g in decl.generics], targs))
            self.bind_generics(decl, arg_tys, env)
        if decl.rets:
            fn.rets = [self.resolve(r, env) for r in decl.rets]
        saved, saved_env = self.ctx, self.fn_env
        saved_active, self.inline_active = getattr(self, "inline_active", []), []
        self.ctx = Ctx(fn)
        try:
            self.gen_fn(fn, env)
        finally:
            self.ctx, self.fn_env = saved, saved_env
            self.inline_active = saved_active
        return fn

    def default_value(self, targs, line):
        if len(targs) != 1:
            refuse("default takes one type", line)
        t = targs[0]
        if t.k in ("int", "float", "bool"):
            return Val(self.lit_code(0, t) if t.k != "bool" else "0", t, pure=True)
        if t.k in ("opt", "ref"):
            if t.k == "ref":
                refuse("a reference has no default", line)
            return Val("0", t, pure=True)
        v = self.tmp(self.ctype(t))
        self.emit("memset(&%s, 0, sizeof %s);" % (v, v))
        return Val(v, t, lv=True, pure=True)

    def bind_generics(self, decl, arg_tys, env):
        names = [g[0] for g in decl.generics]
        for p, at in zip(decl.params, arg_tys):
            if p.type is not None:
                self.unify(p.type, at, env, names)
        for nm in names:
            if nm not in env:
                unsupported("a function-value or unbound type parameter '%s'" % nm, decl.line)

    def unify(self, node, ty, env, names):
        k = node.kind
        if k == "Named" and node.name.name in names and node.args is None:
            env.setdefault(node.name.name, ty)                # the first argument to fix a type wins
        elif k == "Named" and node.args and ty.k == "struct" and ty.info.targs and ty.info.gname == node.name.name:
            for sub, a in zip(node.args, ty.info.targs):
                self.unify(sub, a, env, names)
        elif k == "Slice" and ty.k in ("slice", "arr", "grow", "lim", "frozen"):
            self.unify(node.elem, ty.elem, env, names)
        elif k == "Array" and ty.k == "arr":
            self.unify(node.elem, ty.elem, env, names)
        elif k in ("Ref", "Opt"):
            self.unify(node.type, ty.elem if ty.k in ("ref", "opt") else ty, env, names)

    def gen_fn(self, fn, env):
        saved_ns = getattr(self, "cur_ns", "")
        self.cur_ns = fn.decl.name.rpartition("::")[0]
        try:
            return self.gen_fn1(fn, env)
        except GooseError as e:
            self.annotate(e, fn.decl)
            raise
        finally:
            self.cur_ns = saved_ns

    def annotate(self, e, decl):
        if getattr(decl, "lib", False) and not getattr(e, "in_lib", False):
            e.in_lib = True
            e.msg = "%s (in the library function '%s')" % (e.msg, decl.name)

    def gen_fn1(self, fn, env):
        decl, c = fn.decl, self.ctx
        params = []
        self.push()
        for p, pt in zip(decl.params, fn.params):
            if pt.k == "lit":
                self.declare(p.name, I64, False).lit = pt.info
                continue
            if pt.k == "ref":
                v = self.declare(p.name, pt.elem, mutable=p.var, ref=True,
                                 stk=("%s_stk" % ("%s_g" % p.name)) if pt.elem.k == "grow" else None)
                params.append("%s %s" % (self.ctype(pt), v.code))
                if pt.elem.k == "grow":
                    params.append("int %s" % v.stk)
                    if pt.elem.elem.k == "frozen":
                        v.pay = "%s_pay" % v.code
                        params.append("int %s" % v.pay)
                continue
            v = self.declare(p.name, pt, mutable=p.var)
            params.append("%s %s" % (self.ctype(pt), v.code))
        body = decl.body
        if body is None:
            refuse("function '%s' has no body" % decl.name, decl.line)
        # results
        self.gen_body(fn, body, env)
        self.pop()
        rets = fn.rets or []
        for i in range(1, len(rets)):
            params.append("%s *gs_o%d" % (self.ctype(rets[i]), i))
        sig = "static %s %s(%s)" % (self.ctype(rets[0]) if rets else "void", fn.cname,
                                    ", ".join(params) or "void")
        self.protos.append(sig + ";")
        self.bodies.append(sig + " {")
        self.bodies += c.out
        self.bodies.append("}")

    def gen_body(self, fn, body, env):
        self.fn_env = env
        c = self.ctx
        val = self.block(body, want=(fn.rets[0] if fn.rets and len(fn.rets) == 1 else None), tail=True)
        if val is not None:
            if fn.rets is None:
                v = self.default_lit(val)
                fn.rets = [v.ty]
                self.emit("return %s;" % v.code)
            elif len(fn.rets) == 1:
                v = self.coerce(val, fn.rets[0], body.line, "result")
                self.emit("return %s;" % v.code)
            else:
                refuse("function '%s' returns %d values; its last expression is one" %
                       (fn.decl.name, len(fn.rets)), body.line)
        elif fn.rets is None:
            fn.rets = []

    # -- statements ------------------------------------------------------------------
    def block(self, node, want=None, tail=False):
        """Emit a block's statements; return the tail value (a Val) or None."""
        self.push()
        for s in node.stmts:
            self.stmt(s)
        val = None
        if node.value is not None:
            val = self.ex(node.value, want, allow_void=True)
            if val is not None and val.ty is None and val.lit is None:
                val = None
        self.pop()
        return val

    def scoped(self, node, want=None):
        """`{ .. }` as a C block; returns the tail Val computed inside it."""
        self.emit("{")
        self.ctx.indent += 1
        v = self.block(node, want)
        return v

    def end_scope(self):
        self.ctx.indent -= 1
        self.emit("}")

    def stmt(self, s):
        k = s.kind
        m = getattr(self, "s_" + k, None)
        if m is None:
            unsupported({"Guard": "'guard'", "Defer": "'defer'", "ConstIf": "'const if'",
                         "ConstFor": "'const for'", "FnDecl": "a nested function",
                         "MultiAssign": "multiple assignment", "Struct": "a local struct",
                         "Enum": "'enum'"}.get(k, k), s.line)
        m(s)

    def s_ExprStmt(self, s):
        v = self.ex(s.expr, None, allow_void=True)
        if v is not None and v.ty is not None and not v.pure and v.lit is None:
            self.emit("(void)%s;" % v.code)

    def s_Decl(self, s):
        if s.reusable:
            unsupported("'reusable'", s.line)
        ty = self.resolve(s.type, self.fn_env) if s.type else None
        names = s.names
        if s.by_ref:
            if len(names) != 1 or len(s.inits) != 1:
                refuse("'.=' binds one reference", s.line)
            v = self.ex(s.inits[0], ty.elem if ty is not None and ty.k in ("ref", "opt") else ty)
            if ty is not None and ty.k == "opt":
                v = self.coerce(v, ty, s.line)
                var = self.declare(names[0], ty, s.mode == "var")
                self.emit("%s %s = %s;" % (self.ctype(ty), var.code, v.code))
                return
            if v.ty is None or v.ty is NULLTY or not v.lv:
                refuse("'.=' needs something to point at (a variable, element or field)", s.line)
            if ty is not None and ty.k == "ref" and ty.elem != v.ty:
                refuse("expected %s, found %s" % (ty.key, v.ty.key), s.line)
            var = self.declare(names[0], v.ty, s.mode == "var", ref=True, stk=v.stk)
            self.emit("%s *%s = %s;" % (self.ctype(v.ty), var.code, self.addr(v.code)))
            return
        if len(names) == 1 and s.inits and s.inits[0].kind == "StructLit":
            rt = self.lit_struct_type(s.inits[0])
            if rt is not None and self.owns_stacks(rt):
                return self.declare_root(names[0], s.mode == "var", rt, s.inits[0], s.line)
        if len(names) == 1 and ((ty is not None and ty.k == "grow") or
                                (ty is None and s.inits and self.is_ctor(s.inits[0])) or
                                (ty is None and s.inits and s.inits[0].kind == "ArrayLit" and
                                 not s.inits[0].items and s.inits[0].fill is None and s.mode == "var")):
            return self.decl_grow(s, ty)
        if not s.inits:
            if ty is None:
                refuse("'%s' needs a type or an initializer" % names[0], s.line)
            if len(names) != 1:
                unsupported("a multi-name declaration without initializer", s.line)
            v = self.declare(names[0], ty, s.mode == "var")
            self.emit("%s %s;" % (self.ctype(ty), v.code))
            self.emit("memset(&%s, 0, sizeof %s);" % (v.code, v.code))
            return
        if len(s.inits) == 1 and len(names) > 1:
            val = self.ex(s.inits[0], None)
            if val.extra is None or len(val.extra) + 1 != len(names):
                refuse("%d names but the call returns %d value(s)" %
                       (len(names), 1 + len(val.extra or [])), s.line)
            vals = [val] + val.extra
        elif len(s.inits) == len(names):
            if len(names) == 1:
                vals = [self.ex(s.inits[0], ty)]
            else:
                vals = self.operands(s.inits, [ty] * len(names))
        else:
            refuse("%d names but %d initializers" % (len(names), len(s.inits)), s.line)
        if ty is not None and ty.k == "ref":              # `let x: T& = ..`: bind the reference
            v = self.coerce(vals[0], ty, s.line)
            var = self.declare(names[0], ty.elem, s.mode == "var", ref=True, stk=vals[0].stk)
            self.emit("%s %s = %s;" % (self.ctype(ty), var.code, v.code))
            return
        # evaluate every initializer before any name comes into scope
        made = []
        for nm, v in zip(names, vals):
            if nm == "_":
                made.append(None)
                continue
            if v.ty is None and v.lit is None:
                refuse("'%s' has no value" % nm, s.line)
            if ty is None and v.ty is None and s.mode == "const":   # a named constant: stays untyped
                made.append(v)
                continue
            v = self.coerce(v, ty, s.line) if ty else self.default_lit(v)
            made.append(v)
        for nm, v in zip(names, made):
            if v is None:
                continue
            if v.ty is not None and v.ty.k == "frozen" and v.origin == "call":
                v = self.make_frozen(v, v.ty, s.line, force=True)       # the callee's stack is gone
            if v.ty is not None and v.ty.k == "grow":
                if len(names) != 1 and not v.pure:
                    unsupported("a growable array result among several", s.line)
                self.grow_from(nm, s.mode == "var", v, s.line)
                continue
            if v.ty is None:
                self.declare(nm, I64, False).lit = v.lit
                continue
            var = self.declare(nm, v.ty, s.mode == "var")
            self.emit("%s %s = %s;" % (self.ctype(v.ty), var.code, v.code))

    def pay_stack(self, var, elem):
        """A growable of frozen arrays keeps their payloads on a second stack of its own."""
        if elem.k != "frozen":
            return
        pk = self.new_stack()
        base = self.tmp("uint8_t *", "gs_top[%d]" % pk)
        self.ctx.env.frees.append((pk, base))
        var.pay = pk

    def grow_from(self, name, mutable, src, line):
        """A new growable variable holding a copy of `src`, on a data stack of its own."""
        ty = grow_ty(src.ty.elem)
        k = self.new_stack()
        var = self.declare(name, ty, mutable, stk=k)
        c, e = self.ctype(ty), self.ctype(src.ty.elem)
        self.emit("%s %s; %s.base = (%s *)gs_top[%s]; %s.len = 0; %s.stk = %s;" % (c, var.code, var.code, e, k, var.code, var.code, k))
        self.ctx.env.frees.append((k, var.code + ".base"))
        self.pay_stack(var, src.ty.elem)
        recv = Val(var.code, ty, lv=True, pure=True, stk=k, pay=var.pay)
        self.emit_append(recv, self.coerce(src, slice_ty(src.ty.elem), line), line)

    def lit_struct_type(self, node):
        ty = node.type
        if ty.kind != "Name" or ty.ns:
            return None
        if not (ty.name in self.structs or ty.name in self.gstructs or ty.name in self.aliases):
            return None
        try:
            st = self.resolve(N("Named", ty.line, name=ty, args=getattr(ty, "args", None)), self.fn_env)
        except GooseError:
            return None
        return st if st.k == "struct" else None

    def owns_stacks(self, t):
        """A struct with a growable field (itself or nested) owns data stacks: a root."""
        if t.k != "struct":
            return False
        if t.info.root:
            return True
        return any(ft.k == "grow" or self.owns_stacks(ft) for _, ft, _ in self.struct_fields(t.info))

    def root_init(self, code, t, line):
        """Give every growable field of a root struct a data stack of its own."""
        for fname, ft, _ in self.struct_fields(t.info):
            fc = "%s.f_%s" % (code, fname)
            if ft.k == "grow":
                k = self.new_stack()
                self.emit("%s.base = (%s *)gs_top[%d]; %s.len = 0; %s.stk = %d;" %
                          (fc, self.ctype(ft.elem), k, fc, fc, k))
                self.ctx.env.frees.append((k, fc + ".base"))
            elif ft.k == "struct" and self.owns_stacks(ft):
                self.root_init(fc, ft, line)

    def declare_root(self, name, mutable, t, lit, line, glob=False):
        """`var pool = slot_pool<T> {}`: a struct that owns its growable fields' stacks."""
        if glob:
            cname = "%s_g" % name.replace("::", "_")
            self.globals[name] = Var(cname, t, False, mutable)
            self.global_defs = getattr(self, "global_defs", [])
            self.global_defs.append("static %s %s;" % (self.ctype(t), cname))
            code = cname
        else:
            var = self.declare(name, t, mutable)
            code = var.code
            self.emit("%s %s;" % (self.ctype(t), code))
        self.emit("memset(&%s, 0, sizeof %s);" % (code, code))
        fields = self.struct_fields(t.info)
        saved_frees = self.ctx.env.frees
        if glob:
            self.ctx.env.frees = []
        self.root_init(code, t, line)
        if glob:
            self.ctx.env.frees = saved_frees
        given = {i.name: i for i in lit.inits if i.name is not None}
        if lit.inits and len(given) != len(lit.inits):
            unsupported("a positional root struct literal", line)
        for fname, ft, default in fields:
            node = given[fname].value if fname in given else default
            if node is None or ft.k in ("grow", "struct") and (node.kind == "ArrayLit" and not node.items):
                continue
            if ft.k == "grow" or (ft.k == "struct" and self.owns_stacks(ft)):
                unsupported("an initial value for the growable field '%s'" % fname, line)
            v = self.coerce(self.ex(node, ft), ft, line)
            self.emit("%s.f_%s = %s;" % (code, fname, v.code))

    CTORS = ("copy", "str")

    def is_ctor(self, node):
        return node.kind == "Call" and node.fn.kind == "Name" and node.fn.ns is None \
            and node.fn.name in self.CTORS and node.fn.name not in self.fns

    def decl_grow(self, s, ty):
        """`var v: T[>..] = ..` / `var v = copy(x)` / `var s = str(..)`: the
        value is built in place, on a data stack of its own."""
        node = s.inits[0] if s.inits else None
        elem = ty.elem if ty is not None else None
        src = items = vals = None
        if node is not None and node.kind == "Call" and self.is_ctor(node):
            if node.fn.name == "copy":
                if len(node.args) != 1:
                    refuse("copy takes one argument", node.line)
                src = self.ex(node.args[0], None)
                if src.ty is None or src.ty.k not in SEQ:
                    refuse("copy needs an array or slice", node.line)
                src = self.spill_unless_pure(src)
                if elem is not None and src.ty.elem != elem:
                    refuse("expected %s, found %s" % (ty.key, src.ty.key), node.line)
                elem = src.ty.elem
            else:
                vals = [self.default_lit(v) if v.ty is None else v
                        for v in self.operands(node.args, [None] * len(node.args))]
                elem = U8
        elif node is not None and node.kind == "ArrayLit" and node.fill is None and not getattr(node, "cap", None):
            items = node.items
            if items:
                iv = self.operands(items, [elem] * len(items))
                if elem is None:
                    typed = [v.ty for v in iv if v.ty is not None]
                    elem = typed[0] if typed else (F64 if any(isinstance(v.lit, float) for v in iv) else I64)
                items = [self.coerce(v, elem, s.line) for v in iv]
        elif node is not None:
            # a growable array a function returned: its data sits above the callee's
            # released stack, so it is copied onto this variable's own stack
            src = self.ex(node, ty)
            if src.ty is None or src.ty.k not in ("grow", "lim", "slice", "arr") or \
                    (ty is not None and src.ty.elem != ty.elem):
                refuse("expected %s, found %s" % (ty.key if ty else "an array",
                                                  src.ty.key if src.ty else "a constant"), node.line)
            src = self.spill_unless_pure(src)
            elem = src.ty.elem
        deferred = False
        if ty is None:
            if elem is None:
                if node is not None and node.kind == "ArrayLit" and not node.items and node.fill is None:
                    deferred = True              # `var out = [];`: the element type is the first push's
                    ty = Ty("grow", "?[>..]", elem=None)
                else:
                    refuse("'%s' needs a type or an initializer" % s.names[0], s.line)
            else:
                ty = grow_ty(elem)
        k = self.new_stack()
        var = self.declare(s.names[0], ty, s.mode == "var", stk=k)
        if deferred:
            self.deferred_decls[id(ty)] = (self.ctx.out, len(self.ctx.out), self.ctx.indent, var, k)
            self.emit("")
            self.ctx.env.frees.append((k, var.code + ".base"))
            return
        c, e = self.ctype(ty), self.ctype(elem)
        self.emit("%s %s; %s.base = (%s *)gs_top[%s]; %s.len = 0; %s.stk = %s;" % (c, var.code, var.code, e, k, var.code, var.code, k))
        self.ctx.env.frees.append((k, var.code + ".base"))
        self.pay_stack(var, elem)
        recv = Val(var.code, ty, lv=True, pure=True, stk=k, pay=var.pay)
        if items:
            for v in items:
                self.emit_push(recv, v, s.line)
        if src is not None:
            self.emit_append(recv, self.coerce(src, slice_ty(elem), s.line), s.line)
        if vals is not None:
            self.sink_open(recv, s.line)
            for v in vals:
                self.print_val(v, True)
            self.emit("gs_slen = 0;")

    def resolve_deferred(self, ty, elem, line):
        """The first push into `var out = [];` fixes its element type; the declaration,
        left blank when `out` was introduced, is written now."""
        out, at, indent, var, k = self.deferred_decls.pop(id(ty))
        ty.elem, ty.key = elem, "%s[>..]" % elem.key
        c, e = self.ctype(ty), self.ctype(elem)
        out[at] = "    " * indent + "%s %s; %s.base = (%s *)gs_top[%s]; %s.len = 0; %s.stk = %s;" % (
            c, var.code, var.code, e, k, var.code, var.code, k)

    def seq_check(self, recv, line):
        if recv.ty.k == "grow" and recv.stk is None:
            unsupported("growing a growable array that is not a local or global variable", line)
        if not recv.lv:
            refuse("this needs an array variable", line)

    def emit_push(self, recv, v, line):
        t = recv.ty
        if t.k == "grow":
            e = self.ctype(t.elem)
            k = recv.stk
            if t.elem.k == "frozen" and v.origin != "static":
                if recv.pay is None:
                    unsupported("pushing a frozen array here", line)
                v = self.spill(v) if not v.pure else v
                pe = self.ctype(t.elem.elem)
                pp = self.tmp("%s *" % pe, "(%s *)gs_top[%s]" % (pe, recv.pay))
                self.emit("if (gs_top[%s] + (size_t)%s.len * sizeof(%s) > gs_lim[%s]) gs_abort(\"data stack overflow\", %d);" %
                          (recv.pay, v.code, pe, recv.pay, line))
                self.emit("gs_top[%s] += (size_t)%s.len * sizeof(%s);" % (recv.pay, v.code, pe))
                self.emit("if (%s.len > 0) memcpy(%s, %s.data, (size_t)%s.len * sizeof(%s));" %
                          (v.code, pp, v.code, v.code, pe))
                v = Val("((%s){%s, %s.len})" % (e, pp, v.code), t.elem, pure=True)
            self.emit("if (gs_top[%s] + sizeof(%s) > gs_lim[%s]) gs_abort(\"data stack overflow\", %d);" % (k, e, k, line))
            self.emit("((%s *)gs_top[%s])[0] = %s; gs_top[%s] += sizeof(%s); %s.len++;" %
                      (e, k, v.code, k, e, recv.code))
            return Val("%s.base[%s.len - 1]" % (recv.code, recv.code), t.elem, lv=True)
        self.emit("if (%s.len >= %d) gs_abort(\"limited array is full\", %d);" % (recv.code, t.n, line))
        self.emit("%s.a[%s.len++] = %s;" % (recv.code, recv.code, v.code))
        return Val("%s.a[%s.len - 1]" % (recv.code, recv.code), t.elem, lv=True)

    def emit_append(self, recv, sl, line):
        t = recv.ty
        e = self.ctype(t.elem)
        sl = self.spill(sl)
        if t.k == "grow":
            k = recv.stk
            self.emit("if (gs_top[%s] + %s.len * sizeof(%s) > gs_lim[%s]) gs_abort(\"data stack overflow\", %d);" %
                      (k, sl.code, e, k, line))
            self.emit("memmove(gs_top[%s], %s.data, %s.len * sizeof(%s)); gs_top[%s] += %s.len * sizeof(%s); %s.len += %s.len;" %
                      (k, sl.code, sl.code, e, k, sl.code, e, recv.code, sl.code))
        else:
            self.emit("if (%s.len + %s.len > %d) gs_abort(\"limited array is full\", %d);" % (recv.code, sl.code, t.n, line))
            self.emit("memmove(%s.a + %s.len, %s.data, %s.len * sizeof(%s)); %s.len += %s.len;" %
                      (recv.code, recv.code, sl.code, sl.code, e, recv.code, sl.code))

    def sink_open(self, recv, line):
        t = recv.ty
        if t.elem != U8:
            refuse("'format' needs a u8 array, found %s" % t.key, line)
        self.seq_check(recv, line)
        if t.k == "grow":
            self.emit("gs_sbuf = (uint8_t *)%s.base; gs_slen = &%s.len; gs_stop = &gs_top[%s]; gs_scap = -1;" %
                      (recv.code, recv.code, recv.stk))
        else:
            self.emit("gs_sbuf = (uint8_t *)%s.a; gs_slen = &%s.len; gs_stop = 0; gs_scap = %d;" %
                      (recv.code, recv.code, t.n))

    SEQ_METHODS = ("push", "pop", "clear", "resize", "append")

    def seq_method(self, m, recv, args, line):
        t = recv.ty
        self.seq_check(recv, line)
        if m == "push":
            if len(args) != 1:
                refuse("push takes one argument", line)
            if t.k == "grow" and t.elem is None:
                v0 = self.ex(args[0], None)
                if v0.ty is None:
                    v0 = self.default_lit(v0)
                self.resolve_deferred(t, v0.ty, line)
                v = self.coerce(v0, t.elem, line, "element")
            else:
                v = self.coerce(self.ex(args[0], t.elem), t.elem, line, "element")
            return self.emit_push(recv, v, line)
        if m == "pop":
            if args:
                refuse("pop takes no argument", line)
            # Goose allows pop on a grow-only array where no reference into it is live; that
            # flow check is not done here (the front end trusts a program Goose accepts).
            self.emit("if (%s.len == 0) gs_abort(\"pop of an empty array\", %d);" % (recv.code, line))
            data, _ = self.seq_parts(recv)
            r = self.tmp(self.ctype(t.elem), "%s[%s.len - 1]" % (data, recv.code))
            self.emit("%s.len--;" % recv.code)
            if t.k == "grow":
                self.emit("gs_top[%s] = (uint8_t *)(%s.base + %s.len);" % (recv.stk, recv.code, recv.code))
            return Val(r, t.elem, lv=True, pure=True)
        if m == "clear":
            self.emit("%s.len = 0;" % recv.code)
            if t.k == "grow":
                self.emit("gs_top[%s] = (uint8_t *)%s.base;" % (recv.stk, recv.code))
            return None
        if m == "resize":
            if not 1 <= len(args) <= 2:
                refuse("resize takes a length and an optional fill value", line)
            nv, fv = self.operands(args, [I64, t.elem] if len(args) == 2 else [I64])[0:2] \
                if len(args) == 2 else (self.operands(args, [I64])[0], None)
            nv = self.index_val(nv, line)
            nv = self.spill(nv)
            fill = self.coerce(fv, t.elem, line, "fill value").code if fv is not None else self.zero_code_elem(t.elem)
            data, length = self.seq_parts(recv)
            e = self.ctype(t.elem)
            if t.k == "grow":
                # two statements, not `a || b`: shivyc miscompiles that when `a` is a
                # constant comparison and `b` compares pointers
                self.emit("if (%s < 0) gs_abort(\"bad resize\", %d);" % (nv.code, line))
                self.emit("if (gs_top[%s] + (%s - %s.len) * sizeof(%s) > gs_lim[%s]) gs_abort(\"bad resize\", %d);" %
                          (recv.stk, nv.code, recv.code, e, recv.stk, line))
            else:
                self.emit("if (%s < 0) gs_abort(\"bad resize\", %d);" % (nv.code, line))
                self.emit("if (%s > %d) gs_abort(\"bad resize\", %d);" % (nv.code, t.n, line))
            self.emit("{ int64_t i_; for (i_ = %s; i_ < %s; i_++) %s[i_] = %s; }" % (length, nv.code, data, fill))
            self.emit("%s.len = %s;" % (recv.code, nv.code))
            if t.k == "grow":
                self.emit("gs_top[%s] = (uint8_t *)(%s.base + %s.len);" % (recv.stk, recv.code, recv.code))
            return None
        if m == "append":
            if len(args) != 1:
                refuse("append takes one argument", line)
            sl = self.coerce(self.ex(args[0], None), slice_ty(t.elem), line, "argument")
            self.emit_append(recv, sl, line)
            return None
        unsupported("the method '.%s()'" % m, line)

    def zero_code_elem(self, ty):
        if ty.k in ("int", "float", "bool"):
            return "0"
        unsupported("resize of %s elements without a fill value" % ty.key, 0)

    def builtin_call(self, name, n):
        if name == "format":
            if len(n.args) < 1:
                refuse("format needs a destination", n.line)
            dst = self.ex(n.args[0], None)
            if dst.ty is None or dst.ty.k not in ("grow", "lim"):
                refuse("format writes into a u8 array variable", n.line)
            vals = [self.default_lit(v) if v.ty is None else v
                    for v in self.operands(n.args[1:], [None] * (len(n.args) - 1))]
            self.sink_open(dst, n.line)
            for v in vals:
                self.print_val(v, True)
            self.emit("gs_slen = 0;")
            return None
        if name == "bytes_of":
            if len(n.args) != 1:
                refuse("bytes_of takes one value", n.line)
            v = self.ex(n.args[0], None)
            if v.ty is None:
                v = self.default_lit(v)
            v = self.spill(v) if not v.lv else v
            sl = slice_ty(U8)
            if v.ty.k in SEQ:
                data, ln = self.seq_parts(v)
                size = self.rel_size(v.ty.elem)
                size = "(int64_t)sizeof(%s)" % self.ctype(v.ty.elem) if size is None else "%dLL" % size
                code = "((%s){(uint8_t *)%s, (int64_t)%s * %s})" % (self.ctype(sl), data, ln, size)
            else:
                size = self.rel_size(v.ty)
                size = "(int64_t)sizeof(%s)" % v.code if size is None else "%dLL" % size
                code = "((%s){(uint8_t *)&%s, %s})" % (self.ctype(sl), v.code, size)
            return Val(code, sl)
        if name == "exit":
            if len(n.args) != 1:
                refuse("exit takes a status", n.line)
            v = self.coerce(self.ex(n.args[0], I64), I64, n.line)
            self.emit("fflush(stdout); exit((int)%s);" % v.code)
            return None
        if name == "abort":
            msg = "abort"
            if n.args:
                a = n.args[0]
                if a.kind != "Str":
                    unsupported("'abort' with a message that is not a string literal", n.line)
                msg = a.value.decode("latin-1").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
            self.emit("gs_abort(\"%s\", %d);" % (msg, n.line))
            return None
        if name == "assert":
            if len(n.args) != 1:
                refuse("assert takes one condition", n.line)
            names = self.narrowing(n.args[0])
            v = self.ex(n.args[0], BOOL)
            if v.ty is not None and v.ty.k == "opt":
                v = Val("(%s != 0)" % v.code, BOOL)
            elif v.ty != BOOL:
                refuse("assert needs a bool", n.line)
            self.emit("if (!%s) gs_abort(\"assertion failed\", %d);" % (v.code, n.line))
            if n.args[0].kind == "Name":
                nm = n.args[0].name
                ov = self.lookup(nm)
                if ov is not None and ov.ty.k == "opt" and not ov.ref:
                    self.ctx.env.vars[nm] = Var(ov.code, ov.ty.elem, True, ov.mutable, ov.stk)
            else:
                self.narrow(names)
            return None
        if name in self.CTORS:
            # outside a declaration: a growable of its own, released at the end of the scope
            if name == "copy":
                if len(n.args) != 1:
                    refuse("copy takes one argument", n.line)
                src = self.ex(n.args[0], None)
                if src.ty is not None and src.ty.k not in SEQ:        # copy of a plain value: the value
                    return self.spill(src) if src.lv else src
                if src.ty is None or src.ty.k not in SEQ:
                    refuse("copy needs an array or slice", n.line)
                src = self.spill_unless_pure(src)
                ty = grow_ty(src.ty.elem)
            else:
                vals = [self.default_lit(v) if v.ty is None else v
                        for v in self.operands(n.args, [None] * len(n.args))]
                ty = grow_ty(U8)
            k = self.new_stack()
            t = self.tmp(self.ctype(ty))
            self.emit("%s.base = (%s *)gs_top[%s]; %s.len = 0; %s.stk = %s;" % (t, self.ctype(ty.elem), k, t, t, k))
            self.ctx.env.frees.append((k, t + ".base"))
            recv = Val(t, ty, lv=True, pure=True, stk=k)
            if name == "copy":
                self.emit_append(recv, self.coerce(src, slice_ty(src.ty.elem), n.line), n.line)
            else:
                self.sink_open(recv, n.line)
                for v in vals:
                    self.print_val(v, True)
                self.emit("gs_slen = 0;")
            return recv
        return False

    def s_Assign(self, s):
        if s.op == ".=":
            return self.rebind(s)
        self.check_mutable(s.target, s.line)
        lv = self.ex(s.target, None, lvalue=True)
        if not lv.lv:
            refuse("cannot assign to this expression", s.line)
        if s.op == "=":
            v = self.ex(s.value, lv.ty)
            if lv.ty.k == "frozen" and v.ty is not None and v.ty.k == "frozen" and v.origin == "call":
                v = self.make_frozen(v, v.ty, s.line, force=True)
            v = self.coerce(v, lv.ty, s.line)
            self.emit("%s = %s;" % (lv.code, v.code))
            return
        op = s.op[:-1]
        target = lv
        if not lv.pure and not self.simple_lvalue(s.target):
            p = self.tmp("%s *" % self.ctype(lv.ty), "&%s" % lv.code)
            target = Val("(*%s)" % p, lv.ty, lv=True, pure=True)
        rhs = self.ex(s.value, lv.ty)
        res = self.binop(op, target, rhs, s.line)
        res = self.coerce(res, lv.ty, s.line)
        self.emit("%s = %s;" % (target.code, res.code))

    def s_MultiAssign(self, s):
        if s.op != "=":
            unsupported("'.=' in a multiple assignment", s.line)
        tg = s.targets
        if len(s.values) == 1 and len(tg) > 1:
            v = self.ex(s.values[0], None)
            if v.extra is None or len(v.extra) + 1 != len(tg):
                refuse("%d targets but the call returns %d value(s)" % (len(tg), 1 + len(v.extra or [])), s.line)
            vals = [v] + list(v.extra)
        elif len(s.values) == len(tg):
            vals = self.operands(s.values, [None] * len(tg))
        else:
            refuse("%d targets but %d values" % (len(tg), len(s.values)), s.line)
        # every right-hand side is read before any target is written
        held = []
        for v in vals:
            if v.ty is None:
                held.append(v)
            else:
                t = self.tmp(self.ctype(v.ty), v.code)
                held.append(Val(t, v.ty, lv=True, pure=True))
        for node, v in zip(tg, held):
            if node.kind == "Name" and node.name == "_":
                continue
            self.check_mutable(node, s.line)
            lv = self.ex(node, None, lvalue=True)
            if not lv.lv or lv.ty is None:
                refuse("cannot assign to this expression", s.line)
            self.emit("%s = %s;" % (lv.code, self.coerce(v, lv.ty, s.line).code))

    def rebind(self, s):
        t = s.target
        if t.kind != "Name" or t.ns is not None:
            # an element or a field: `.=` is the same as `=` for a plain value
            self.check_mutable(t, s.line)
            lv = self.ex(t, None, lvalue=True)
            if not lv.lv or lv.ty is None:
                refuse("cannot assign to this expression", s.line)
            if lv.ty.k not in ("ref", "opt"):
                if not self.fn_env:                 # generic code uses `.=` for any element type
                    refuse(".= rebinds references; the target has type %s" % lv.ty.key, s.line)
                v = self.coerce(self.ex(s.value, lv.ty), lv.ty, s.line)
                self.emit("%s = %s;" % (lv.code, v.code))
                return
            if lv.ty.k == "opt":
                v = self.coerce(self.ex(s.value, lv.ty), lv.ty, s.line)
                self.emit("%s = %s;" % (lv.code, v.code))
                return
            v = self.ex(s.value, lv.ty.elem)
            if v.ty == lv.ty:
                self.emit("%s = %s;" % (lv.code, v.code))
            elif v.ty == lv.ty.elem and v.lv:
                self.emit("%s = %s;" % (lv.code, self.addr(v.code)))
            else:
                refuse("'.=' needs a %s to point at" % lv.ty.elem.key, s.line)
            return
        var = self.lookup(t.name)
        if var is None:
            refuse("unknown name '%s'" % t.name, s.line)
        if not var.mutable:
            refuse("cannot rebind '%s', which is not a 'var'" % t.name, s.line)
        v = self.ex(s.value, var.ty)
        if var.ref:
            if v.ty is None or v.ty != var.ty or not v.lv:
                refuse("'.=' needs a %s to point at" % var.ty.key, s.line)
            self.emit("%s = %s;" % (var.code, self.addr(v.code)))
        elif var.ty.k == "opt":
            v = self.coerce(v, var.ty, s.line)
            self.emit("%s = %s;" % (var.code, v.code))
        else:
            refuse("'%s' is not a reference" % t.name, s.line)

    def simple_lvalue(self, node):
        k = node.kind
        if k == "Name":
            return True
        if k == "Field":
            return self.simple_lvalue(node.base)
        return False

    def check_mutable(self, node, line):
        steps = []
        while node.kind in ("Field", "Index", "Paren"):
            steps.append(node.kind)
            node = node.base if node.kind != "Paren" else node.expr
        if node.kind == "Name":
            v = self.lookup(node.name)
            if v is not None and not v.mutable and not v.ref:
                # a slice is a view: its elements are written through it
                steps = [x for x in steps if x != "Paren"]
                if v.ty.k == "slice" and steps and steps[-1] == "Index":
                    return
                refuse("cannot assign to '%s', which is not a 'var'" % node.name, line)

    def s_IncDec(self, s):
        one = N("Int", s.line, value=1, text="1")
        self.s_Assign(N("Assign", s.line, op="+=" if s.op == "++" else "-=", target=s.target, value=one))

    # -- expressions --------------------------------------------------------------------
    def operands(self, nodes, wants):
        """Evaluate in order; spill earlier values when a later one emits code."""
        vals = []
        for n, w in zip(nodes, wants):
            with self.capture() as lines:
                v = self.ex(n, w)
            if lines:
                vals = [self.spill(x) if x.ty is not None else x for x in vals]
                self.ctx.out.extend(lines)
            vals.append(v)
        return vals

    def ex(self, node, want=None, lvalue=False, allow_void=False):
        m = getattr(self, "e_" + node.kind, None)
        if m is None:
            unsupported({"Null": "'null'"}.get(node.kind, node.kind), node.line)
        v = m(node, want)
        if v is None and not allow_void:
            refuse("this expression has no value", node.line)
        return v

    def e_Int(self, n, want):
        return Val("", None, lit=n.value)

    def e_Float(self, n, want):
        return Val("", None, lit=n.value)

    def e_Null(self, n, want):
        return Val("0", NULLTY, pure=True)

    def e_Bool(self, n, want):
        return Val("1" if n.value else "0", BOOL, pure=True)

    def e_Str(self, n, want):
        b = n.value
        esc = "".join("\\x%02x" % c if not (32 <= c < 127) or c in (34, 92, 63) else chr(c) for c in b)
        # \xNN swallows following hex digits in C: split the literal after each escape
        parts, cur, i = [], "", 0
        s = ""
        for c in b:
            if 32 <= c < 127 and c not in (34, 92, 63):
                s += chr(c)
            else:
                s += "\\%03o" % c
        sl = slice_ty(U8)
        return Val("((%s){(uint8_t *)\"%s\", %d})" % (self.ctype(sl), s, len(b)), sl, pure=True, origin="static")

    def e_Paren(self, n, want):
        return self.ex(n.expr, want)

    def e_Name(self, n, want):
        if getattr(n, "args", None) or n.ns == "":
            unsupported("a generic or global-scope name", n.line)
        if n.ns:
            v = self.lookup("%s::%s" % (n.ns, n.name))
        else:
            v = self.lookup(n.name)
            cur = getattr(self, "cur_ns", "")
            if cur and (v is None or v.lit is None and "%s::%s" % (cur, n.name) in self.lib_globals):
                v = self.lookup("%s::%s" % (cur, n.name)) or v
        if v is None:
            if n.name in self.fns:
                unsupported("a function used as a value", n.line)
            refuse("unknown name '%s'" % n.name, n.line)
        if isinstance(v, FnVal):
            unsupported("passing a function value on", n.line)
        if v.lit is not None:
            return Val("", None, lit=v.lit)
        return Val("(*%s)" % v.code if v.ref else v.code, v.ty, lv=True, pure=True, stk=v.stk, pay=v.pay)

    def e_Cast(self, n, want):
        ty = self.resolve(n.type, self.fn_env)
        v = self.ex(n.expr, None)
        if v.ty is None:
            v = self.default_lit(v) if not (ty.is_num) else self.coerce(v, ty, n.line) if False else v
            if v.ty is None:
                # a constant cast to a number: fold into the target type, wrapping like C
                val = v.lit
                if ty.k == "int" and isinstance(val, float):
                    val = int(val)
                if ty.k == "int":
                    val &= (1 << ty.bits) - 1
                    if ty.signed and val >= 1 << (ty.bits - 1):
                        val -= 1 << ty.bits
                return Val(self.lit_code(val, ty), ty, pure=True)
        if v.ty.is_num and ty.is_num or (v.ty.k == "bool" and ty.k == "int") or v.ty == ty:
            return Val("((%s)%s)" % (self.ctype(ty), v.code), ty, pure=v.pure)
        refuse("cannot cast %s to %s" % (v.ty.key, ty.key), n.line)

    def e_Unary(self, n, want):
        op = n.op
        if op == "&":
            v = self.ex(n.operand, None)
            if v.ty is None or not v.lv or v.ty.k in ("ref", "opt"):
                refuse("'&' needs a variable, element or field", n.line)
            return Val(self.addr(v.code), ref_ty(v.ty), pure=v.pure, stk=v.stk, pay=v.pay)
        v = self.ex(n.operand, want)
        if v.ty is None:                                # constant folding
            if op == "-":
                return Val("", None, lit=-v.lit)
            if op == "~" and isinstance(v.lit, int):
                return Val("", None, lit=~v.lit)
            refuse("bad operand for '%s'" % op, n.line)
        t = v.ty
        if op == "-" and t.is_num:
            if t.k == "float":
                return Val("(-%s)" % v.code, t)
            if not t.signed:
                refuse("cannot negate %s" % t.key, n.line)
            return Val("((%s)(0 - (%s)(%s)))" % (self.ctype(t), self.utype(t), v.code), t)
        if op == "!" and t.k == "bool":
            return Val("(!%s)" % v.code, t)
        if op == "~" and t.k == "int":
            return Val("((%s)~%s)" % (self.ctype(t), v.code), t)
        refuse("bad operand type %s for '%s'" % (t.key, op), n.line)

    def utype(self, t):
        return "uint64_t" if t.bits == 64 else "uint32_t"

    def e_Binary(self, n, want):
        op = n.op
        if op in ("&&", "||"):
            return self.logic(n)
        if op in (".==", ".!="):
            a, b = self.operands([n.left, n.right], [None, None])
            pa, pb = self.ptr_of(a, n.line), self.ptr_of(b, n.line)
            return Val("(%s %s %s)" % (pa, "==" if op == ".==" else "!=", pb), BOOL)
        cmp_ = op in ("==", "!=", "<", "<=", ">", ">=")
        w = None if cmp_ else want
        a, b = self.operands([n.left, n.right], [w, w])
        return self.binop(op, a, b, n.line, want)

    @staticmethod
    def truth(v):
        """An optional used as a condition is true when it holds a value."""
        if v.ty is not None and v.ty.k == "opt":
            return Val("(%s != 0)" % v.code, BOOL)
        return v

    def logic(self, n):
        a = self.truth(self.ex(n.left, BOOL))
        if a.ty != BOOL:
            refuse("expected bool for '%s', found %s" % (n.op, a.ty.key if a.ty else "a number"), n.line)
        with self.capture() as lines:
            self.push()
            if n.op == "&&":
                self.narrow(self.narrowing(n.left))        # `l && l.key > 0`: l is present on the right
            b = self.truth(self.ex(n.right, BOOL))
            self.ctx.env = self.ctx.env.dyn
        if b.ty != BOOL:
            refuse("expected bool for '%s'" % n.op, n.line)
        if not lines:
            return Val("(%s %s %s)" % (a.code, n.op, b.code), BOOL)
        r = self.tmp("uint8_t", a.code)
        self.emit("if (%s%s) {" % ("" if n.op == "&&" else "!", r))
        self.ctx.out.extend("    " + l for l in lines)
        self.emit("    %s = %s;" % (r, b.code))
        self.emit("}")
        return Val(r, BOOL, lv=True, pure=True)

    def ptr_of(self, v, line):
        if v.ty is not None and v.ty.k == "opt":
            return v.code
        if v.ty is not None and v.lv:
            return self.addr(v.code)
        refuse("identity comparison needs references or variables", line)

    def common(self, a, b, line, op):
        """Unify two operands' types: a constant takes the other's type."""
        if a.ty is None and b.ty is None:
            return None
        if a.ty is None:
            return b.ty
        if b.ty is None:
            return a.ty
        if a.ty == b.ty:
            return a.ty
        if self.widen_ok(a.ty, b.ty):
            return b.ty
        if self.widen_ok(b.ty, a.ty):
            return a.ty
        if a.ty.k == "float" and b.ty.k == "int":       # an integer meeting a float becomes that float
            return a.ty
        if b.ty.k == "float" and a.ty.k == "int":
            return b.ty
        refuse("cannot apply '%s' to %s and %s" % (op, a.ty.key, b.ty.key), line)

    def binop(self, op, a, b, line, want=None):
        if a.ty is NULLTY or b.ty is NULLTY:
            o = b if a.ty is NULLTY else a
            if op not in ("==", "!="):
                refuse("cannot apply '%s' to null" % op, line)
            if o.ty is NULLTY:
                return Val("1" if op == "==" else "0", BOOL, pure=True)
            if o.ty is None or o.ty.k != "opt":
                refuse("only an optional (T?) can be compared with null, not %s" %
                       (o.ty.key if o.ty else "a constant"), line)
            return Val("(%s %s 0)" % (o.code, op), BOOL)
        if a.ty is not None and a.ty.k == "opt" and a.ty == getattr(b, "ty", None) and op in ("==", "!="):
            return Val("(%s %s %s)" % (a.code, op, b.code), BOOL)
        if op in ("<<", ">>") and a.ty is None and b.ty is not None and b.ty.k == "int" \
                and isinstance(a.lit, int):             # the count never gives the constant its type
            a = self.coerce(a, want if want is not None and want.k == "int" else I64, line)
        if a.ty is None and b.ty is None:                # two constants: fold
            return self.fold(op, a, b, line)
        agg = (a.ty is not None and a.ty.k in ("struct", "arr")) or (b.ty is not None and b.ty.k in ("struct", "arr"))
        if agg and op in ("+", "-", "*", "/", "%"):
            return self.memberwise(op, a, b, line)
        # an integer meeting a float constant becomes a float: the context's, else f64
        for x, y in ((a, b), (b, a)):
            if x.ty is not None and x.ty.k == "int" and y.ty is None and isinstance(y.lit, float):
                ft = want if want is not None and want.k == "float" else F64
                conv = Val("((%s)%s)" % (self.ctype(ft), x.code), ft, pure=x.pure)
                a, b = (conv, b) if x is a else (a, conv)
                break
        if op in ("==", "!=") and a.ty is not None and b.ty is not None and a.ty.k in SEQ \
                and b.ty.k in SEQ and a.ty.elem == b.ty.elem and not (a.ty.k == "arr" and b.ty.k == "arr"):
            a, b = self.spill(a), self.spill(b)
            da, la = self.seq_parts(a)
            db, lb = self.seq_parts(b)
            r = "%s(%s, %s, %s, %s)" % (self.seq_eqfn(a.ty.elem), da, la, db, lb)
            return Val(r if op == "==" else "(!%s)" % r, BOOL)
        if a.ty is not None and b.ty is not None and a.ty.k in ("enum", "variant") \
                and b.ty.k in ("enum", "variant") and a.ty.info is b.ty.info:
            t = a.ty.info.ty
        elif op in ("<<", ">>") and a.ty is not None and a.ty.k == "int" and b.ty is not None and b.ty.k == "int":
            t = a.ty                                  # the count may be any integer type
            b = Val("((int64_t)%s)" % b.code, I64, pure=b.pure)
            b = Val(b.code, t, pure=b.pure)
        else:
            t = self.common(a, b, line, op)
        if t.k == "float":
            a, b = [Val("((%s)%s)" % (self.ctype(t), x.code), t, pure=x.pure)
                    if x.ty is not None and x.ty.k == "int" else x for x in (a, b)]
        a, b = self.coerce(a, t, line), self.coerce(b, t, line)
        cmp_ = op in ("==", "!=", "<", "<=", ">", ">=")
        if t.k in ("struct", "enum", "arr") and op in ("==", "!="):
            a, b = self.spill(a), self.spill(b)
            r = "%s(&%s, &%s)" % (self.eqfn(t), a.code, b.code)
            return Val(r if op == "==" else "(!%s)" % r, BOOL)
        if t.k in ("struct", "enum", "arr", "slice", "grow", "lim"):
            unsupported("'%s' on %s" % (op, t.key), line)
        if t.k == "bool":
            if op in ("==", "!="):
                return Val("(%s %s %s)" % (a.code, op, b.code), BOOL)
            refuse("cannot apply '%s' to bool" % op, line)
        if cmp_:
            return Val("(%s %s %s)" % (a.code, op, b.code), BOOL)
        c = self.ctype(t)
        if t.k == "float":
            if op in ("+", "-", "*", "/"):
                return Val("((%s)(%s %s %s))" % (c, a.code, op, b.code), t)
            if op == "%":
                fn = "fmod" if t.bits == 64 else "fmodf"
                return Val("((%s)%s(%s, %s))" % (c, fn, a.code, b.code), t)
            refuse("cannot apply '%s' to %s" % (op, t.key), line)
        u = self.utype(t)
        if op in ("+", "-", "*"):
            return Val("((%s)((%s)%s %s (%s)%s))" % (c, u, a.code, op, u, b.code), t)
        if op in ("&", "|", "^"):
            return Val("((%s)(%s %s %s))" % (c, a.code, op, b.code), t)
        if op == "/":
            return Val("gs_div_%s(%s, %s, %d)" % (t.name, a.code, b.code, line), t)
        if op == "%":
            return Val("gs_mod_%s(%s, %s, %d)" % (t.name, a.code, b.code, line), t)
        if op in ("<<", ">>"):
            if op == "<<":
                return Val("((%s)((%s)%s << (%s & %d)))" % (c, u, a.code, b.code, t.bits - 1), t)
            return Val("((%s)(%s >> (%s & %d)))" % (c, a.code, b.code, t.bits - 1), t)
        refuse("cannot apply '%s' to %s" % (op, t.key), line)

    def leaf_type(self, t, line):
        """The scalar type every leaf of a struct or fixed array has, else refuse."""
        if t.k in ("int", "float"):
            return t
        if t.k == "arr":
            return self.leaf_type(t.elem, line)
        if t.k == "struct":
            leaves = {self.leaf_type(ft, line) for _, ft, _ in self.struct_fields(t.info)}
            if len(leaves) != 1:
                refuse("arithmetic on %s needs every field to be an integer, or every one a float" % t.key, line)
            return leaves.pop()
        refuse("arithmetic is not defined on %s" % t.key, line)

    def memberwise(self, op, a, b, line):
        """`+ - * / %` apply leaf by leaf to two values of one struct or fixed-array type;
        `*` and `/` also take one scalar."""
        agg = a.ty if a.ty is not None and a.ty.k in ("struct", "arr") else b.ty
        leaf = self.leaf_type(agg, line)
        a_agg = a.ty is not None and a.ty.k in ("struct", "arr")
        b_agg = b.ty is not None and b.ty.k in ("struct", "arr")
        if a_agg and b_agg:
            if a.ty != b.ty:
                refuse("cannot apply '%s' to %s and %s" % (op, a.ty.key, b.ty.key), line)
        else:
            if op not in ("*", "/") or (op == "/" and not a_agg):
                refuse("cannot apply '%s' to %s and a scalar" % (op, agg.key), line)
            sc = b if a_agg else a
            if sc.ty is not None and sc.ty != leaf or sc.ty is not None and sc.ty.k not in ("int", "float"):
                refuse("a scalar meeting %s must be a %s" % (agg.key, leaf.key), line)
            sc = self.coerce(sc, leaf, line)
            sc = self.spill(sc)
            a, b = (a, sc) if a_agg else (sc, b)
        a = self.spill(a) if a.ty is not None and a.ty.k in ("struct", "arr") and not a.pure else a
        b = self.spill(b) if b.ty is not None and b.ty.k in ("struct", "arr") and not b.pure else b
        out = self.tmp(self.ctype(agg))
        self.mw_leaves(op, a.code, not a_agg, b.code, not b_agg, agg, out, line)
        return Val(out, agg, lv=True, pure=True)

    def mw_leaves(self, op, a, a_sc, b, b_sc, t, dst, line):
        """dst = a op b over every leaf of `t`; an operand flagged scalar is used as it is."""
        if t.k in ("int", "float"):
            r = self.binop(op, Val(a, t, pure=True), Val(b, t, pure=True), line)
            self.emit("%s = %s;" % (dst, r.code))
            return
        if t.k == "struct":
            for fname, ft, _ in self.struct_fields(t.info):
                self.mw_leaves(op, a if a_sc else "%s.f_%s" % (a, fname), a_sc,
                               b if b_sc else "%s.f_%s" % (b, fname), b_sc, ft,
                               "%s.f_%s" % (dst, fname), line)
            return
        self.ctx.ntmp += 1
        i = "mi%d" % self.ctx.ntmp
        self.emit("{ int64_t %s; for (%s = 0; %s < %d; %s++) {" % (i, i, i, t.n, i))
        self.ctx.indent += 1
        self.mw_leaves(op, a if a_sc else "%s.a[%s]" % (a, i), a_sc, b if b_sc else "%s.a[%s]" % (b, i), b_sc,
                       t.elem, "%s.a[%s]" % (dst, i), line)
        self.ctx.indent -= 1
        self.emit("} }")

    def fold(self, op, a, b, line):
        x, y = a.lit, b.lit
        isf = isinstance(x, float) or isinstance(y, float)
        try:
            if op == "+": r = x + y
            elif op == "-": r = x - y
            elif op == "*": r = x * y
            elif op == "/":
                r = x / y if isf else int(x / y) if False else (abs(x) // abs(y)) * (1 if (x < 0) == (y < 0) else -1)
            elif op == "%":
                if isf:
                    import math
                    r = math.fmod(x, y)
                else:
                    r = x % abs(y)
            elif op == "<<": r = x << y
            elif op == ">>": r = x >> y
            elif op == "&": r = x & y
            elif op == "|": r = x | y
            elif op == "^": r = x ^ y
            elif op in ("==", "!=", "<", "<=", ">", ">="):
                r = {"==": x == y, "!=": x != y, "<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[op]
                return Val("1" if r else "0", BOOL, pure=True)
            else:
                refuse("cannot apply '%s' to constants" % op, line)
        except ZeroDivisionError:
            if isf and op == "/":
                r = float("nan") if x == 0 or x != x else (float("inf") if x > 0 else float("-inf"))
            else:
                refuse("division by zero in a constant", line)
        return Val("", None, lit=r)


    # -- value-producing constructs ------------------------------------------------------
    def branch(self, want):
        return Branch(self, want)

    def cond(self, node):
        v = self.ex(node, BOOL)
        if v.ty is not None and v.ty.k == "opt":
            return Val("(%s != 0)" % v.code, BOOL)
        if v.ty != BOOL:
            refuse("expected a bool condition, found %s" % (v.ty.key if v.ty else "a number"), node.line)
        return v

    def arm(self, node, res, line):
        """Emit a block as one arm of a value construct."""
        self.ctx.indent += 1
        v = self.block(node, res.want if res else None)
        if res is not None and not (v is None and self.diverges(node)):
            res.give(v, line)
        self.ctx.indent -= 1

    @staticmethod
    def diverges(node):
        """A block whose last statement leaves it (return, break, continue) gives no value, and needs none."""
        if getattr(node, "value", None) is not None or not getattr(node, "stmts", None):
            return False
        last = node.stmts[-1]
        e = last.expr if last.kind == "ExprStmt" else last
        return e.kind in ("Return", "Break", "Continue")

    def narrowing(self, node):
        """Names of optionals known present when `node` is true."""
        k = node.kind
        if k == "Paren":
            return self.narrowing(node.expr)
        if k == "Name" and node.ns is None:
            v = self.lookup(node.name)
            return [node.name] if v is not None and v.ty.k == "opt" and not v.ref else []
        if k == "Binary" and node.op == "!=" and node.right.kind == "Null":
            return self.narrowing(node.left)
        if k == "Binary" and node.op == "&&":
            return self.narrowing(node.left) + self.narrowing(node.right)
        return []

    def narrow(self, names):
        for nm in names:
            v = self.lookup(nm)
            if v is not None and v.ty.k == "opt" and not v.ref:
                self.ctx.env.vars[nm] = Var(v.code, v.ty.elem, True, v.mutable, v.stk)

    def e_If(self, n, want):
        names = self.narrowing(n.cond)
        c = self.cond(n.cond)
        res = Branch(self, want) if n.els is not None else None
        self.emit("if (%s) {" % c.code)
        self.push()
        self.narrow(names)
        self.arm(n.then, res, n.line)
        self.pop()
        if n.els is not None:
            self.emit("} else {")
            if n.els.kind == "If":
                self.ctx.indent += 1
                v = self.e_If(n.els, want)
                res.give(v, n.els.line)
                self.ctx.indent -= 1
            else:
                self.arm(n.els, res, n.els.line)
        self.emit("}")
        return res.finish(n.line) if res else None

    def e_Block(self, n, want):
        self.emit("{")
        self.ctx.indent += 1
        v = self.block(n, want)
        res = None
        if v is not None:
            res = Branch(self, want)
            res.give(v, n.line)
        self.ctx.indent -= 1
        self.emit("}")
        return res.finish(n.line) if res else None

    def e_BlockExpr(self, n, want):
        self.ctx.ntmp += 1
        label = "end%d" % self.ctx.ntmp
        res = Branch(self, want)
        self.ctx.breakables.append(("block", res, label, self.ctx.env))
        self.emit("{")
        self.ctx.indent += 1
        v = self.block(n.body, want)
        if v is not None:
            res.give(v, n.line)
        self.ctx.indent -= 1
        self.emit("}")
        self.ctx.breakables.pop()
        self.emit("%s:;" % label)
        return res.finish(n.line)

    def e_Match(self, n, want):
        sv = self.ex(n.scrutinee, None)
        sv = self.default_lit(sv) if sv.ty is None else sv
        if sv.ty.k in ("enum", "variant"):
            return self.match_enum(n, sv, want)
        if sv.ty.k not in ("int", "bool"):
            unsupported("'match' on %s" % sv.ty.key, n.line)
        sv = self.spill(sv)
        res = Branch(self, want)
        opened = 0
        first = True
        for a in n.arms:
            p = a.pattern
            if p.kind == "PVariant":
                unsupported("a variant pattern", p.line)
            if p.kind == "PWild":
                cond = None
            else:
                parts = []
                for lo, hi in p.items:
                    lov = self.ex(lo, sv.ty)
                    lov = self.coerce(lov, sv.ty, p.line)
                    if hi is None:
                        parts.append("%s == %s" % (sv.code, lov.code))
                    else:
                        hiv = self.coerce(self.ex(hi, sv.ty), sv.ty, p.line)
                        parts.append("(%s <= %s && %s < %s)" % (lov.code, sv.code, sv.code, hiv.code))
                cond = " || ".join(parts)
            if cond is None:
                self.emit("{" if first else "} else {")
            else:
                self.emit(("if (%s) {" if first else "} else if (%s) {") % cond)
            first = False
            self.ctx.indent += 1
            body = a.body
            if body.kind == "Block":
                v = self.block(body, want)
            else:
                v = self.ex(body, want, allow_void=True)
            if not (v is None and (body.kind in ("Return", "Break", "Continue") or
                                   body.kind == "Block" and self.diverges(body))):
                res.give(v, a.line)
            self.ctx.indent -= 1
            if cond is None:
                break
        if not first:
            self.emit("}")
        return res.finish(n.line)

    def match_enum(self, n, sv, want):
        t = sv.ty if sv.ty.k == "enum" else sv.ty.info.ty
        info = t.info
        names = [v.name for v in info.decl.variants]
        sv = self.spill(sv)
        res = Branch(self, want)
        covered, wild, first = set(), False, True

        def vname_of(node):
            if node.kind == "Name" and node.ns is None:
                return node.name
            if node.kind == "Field" and node.base.kind == "Name" and node.base.name == info.name:
                return node.name
            refuse("expected a variant of %s" % info.name, node.line)

        for a in n.arms:
            p = a.pattern
            bind = None
            if p.kind == "PWild":
                cond, wild = None, True
            elif p.kind == "PVariant":
                vn = vname_of(p.variant)
                if vn not in names:
                    refuse("%s has no variant '%s'" % (info.name, vn), p.line)
                if p.ref:
                    refuse("cannot bind the payload of fixed-mode %s by reference (spec 3.5); bind by value: %s %s"
                           % (info.name, vn, p.bind), p.line)
                covered.add(vn)
                cond = "%s.tag == %d" % (sv.code, names.index(vn))
                bind = (p.bind, names.index(vn))
            else:
                parts = []
                for lo, hi in p.items:
                    if hi is not None:
                        refuse("a range does not match an enum", p.line)
                    vn = vname_of(lo)
                    if vn not in names:
                        refuse("%s has no variant '%s'" % (info.name, vn), p.line)
                    covered.add(vn)
                    parts.append("%s.tag == %d" % (sv.code, names.index(vn)))
                cond = " || ".join(parts)
            if cond is None:
                self.emit("{" if first else "} else {")
            else:
                self.emit(("if (%s) {" if first else "} else if (%s) {") % cond)
            first = False
            self.ctx.indent += 1
            self.push()
            if bind is not None:
                var = self.declare(bind[0], info.variant_ty(bind[1]), True)
                self.emit("%s %s = %s;" % (self.ctype(t), var.code, sv.code))
            body = a.body
            if body.kind == "Block":
                v = self.block(body, want)
            else:
                v = self.ex(body, want, allow_void=True)
            if not (v is None and (body.kind in ("Return", "Break", "Continue") or
                                   body.kind == "Block" and self.diverges(body))):
                res.give(v, a.line)
            self.pop()
            self.ctx.indent -= 1
            if cond is None:
                break
        if not first:
            self.emit("}")
        if not wild:
            missing = [x for x in names if x not in covered]
            if missing:
                refuse("this match does not cover %s" % ", ".join("%s.%s" % (info.name, m) for m in missing), n.line)
        return res.finish(n.line)

    # -- loops ------------------------------------------------------------------------
    def e_While(self, n, want):
        with self.capture() as lines:
            c = self.cond(n.cond)
        res = Branch(self, None)
        self.ctx.breakables.append(("loop", res, None, self.ctx.env))
        if not lines:
            self.emit("while (%s) {" % c.code)
        else:
            self.emit("for (;;) {")
            self.ctx.indent += 1
            self.ctx.out.extend(lines)
            self.emit("if (!%s) break;" % c.code)
            self.ctx.indent -= 1
        self.ctx.indent += 1
        self.block(n.body)
        self.ctx.indent -= 1
        self.emit("}")
        self.ctx.breakables.pop()
        return None

    def e_Loop(self, n, want):
        res = Branch(self, want)
        self.ctx.breakables.append(("loop", res, None, self.ctx.env))
        self.emit("for (;;) {")
        self.ctx.indent += 1
        self.block(n.body)
        self.ctx.indent -= 1
        self.emit("}")
        self.ctx.breakables.pop()
        return res.finish(n.line) if res.tmp_used else None

    def e_For(self, n, want):
        binds = n.binds
        res = Branch(self, None)
        if n.hi is not None or (not n.ref and len(binds) == 1 and False):
            return self.for_range(n, res)
        it = self.ex(n.iter, None)
        if it.ty is None or it.ty.k == "int":
            if n.ref or len(binds) != 1:
                refuse("'for' over a count binds one name", n.line)
            return self.for_range(n, res)
        if it.ty.k not in SEQ:
            unsupported("'for' over %s" % it.ty.key, n.line)
        if n.ref and not (it.lv or it.ty.k in ("slice", "grow")):
            refuse("'for &x' needs a variable to point into", n.line)
        it = self.spill(it) if not it.pure else it
        elem = it.ty.elem
        data, length = self.seq_parts(it)
        self.ctx.ntmp += 1
        ix = "i%d" % self.ctx.ntmp
        self.ctx.breakables.append(("loop", res, None, self.ctx.env))
        self.emit("{")
        self.ctx.indent += 1
        self.emit("int64_t n%s = %s;" % (ix, length))
        self.emit("int64_t %s;" % ix)
        self.emit("for (%s = 0; %s < n%s; %s++) {" % (ix, ix, ix, ix))
        self.ctx.indent += 1
        self.push()
        name, bty = binds[0]
        if bty is not None:
            bt = self.resolve(bty, self.fn_env)
            if bt != elem:
                refuse("the loop element is %s, not %s" % (elem.key, bt.key), n.line)
        if n.ref:
            v = self.declare(name, elem, True, ref=True)
            self.emit("%s *%s = &%s[%s];" % (self.ctype(elem), v.code, data, ix))
        else:
            v = self.declare(name, elem, False)
            self.emit("%s %s = %s[%s];" % (self.ctype(elem), v.code, data, ix))
        if len(binds) == 2:
            iv = self.declare(binds[1][0], I64, False)
            self.emit("int64_t %s = %s;" % (iv.code, ix))
        self.block(n.body)
        self.pop()
        self.ctx.indent -= 1
        self.emit("}")
        self.ctx.indent -= 1
        self.emit("}")
        self.ctx.breakables.pop()
        return None

    def for_range(self, n, res):
        name, bty = n.binds[0]
        if n.hi is None:
            lo, hi = None, n.iter
        else:
            lo, hi = n.iter, n.hi
        want = self.resolve(bty, self.fn_env) if bty is not None else None
        if want is not None and want.k != "int":
            refuse("a 'for' counter is an integer, not %s" % want.key, n.line)
        nodes = ([lo] if lo is not None else []) + [hi]
        vals = self.operands(nodes, [want] * len(nodes))
        ty = want
        if ty is None:
            typed = [v.ty for v in vals if v.ty is not None]
            ty = typed[0] if typed else I64
            for v in vals:
                if v.ty is not None and v.ty != ty:
                    ty = self.common(Val("", ty), v, n.line, "..")
        if ty.k != "int":
            refuse("'for' needs integers, found %s" % ty.key, n.line)
        vals = [self.coerce(v, ty, n.line) for v in vals]
        lov = vals[0] if lo is not None else Val(self.lit_code(0, ty), ty, pure=True)
        hiv = vals[-1]
        self.ctx.breakables.append(("loop", res, None, self.ctx.env))
        self.emit("{")
        self.ctx.indent += 1
        self.ctx.ntmp += 1
        hn = "h%d" % self.ctx.ntmp
        self.emit("%s %s = %s;" % (self.ctype(ty), hn, hiv.code))
        self.push()
        v = self.declare(name, ty, False)
        self.emit("%s %s;" % (self.ctype(ty), v.code))
        self.emit("for (%s = %s; %s < %s; %s++) {" % (v.code, lov.code, v.code, hn, v.code))
        self.ctx.indent += 1
        self.block(n.body)
        self.ctx.indent -= 1
        self.emit("}")
        self.pop()
        self.ctx.indent -= 1
        self.emit("}")
        self.ctx.breakables.pop()
        return None

    def e_Break(self, n, want):
        c = self.ctx
        if not c.breakables:
            refuse("'break' outside a loop or block", n.line)
        kind, res, label, depth = c.breakables[-1]
        if n.value is not None:
            v = self.ex(n.value, res.want)
            res.give(v, n.line)
        self.unwind(depth)
        if kind == "block":
            self.emit("goto %s;" % label)
        else:
            self.emit("break;")
        return None

    def e_Continue(self, n, want):
        for kind, _, _, depth in reversed(self.ctx.breakables):
            if kind == "loop":
                self.unwind(depth)
                self.emit("continue;")
                return None
        refuse("'continue' outside a loop", n.line)

    def s_Guard(self, s):
        names = self.narrowing(s.cond)
        c = self.cond(s.cond)
        if s.els is None:
            self.emit("if (!%s) gs_abort(\"guard failed\", %d);" % (c.code, s.line))
            self.narrow(names)
            return
        self.emit("if (!%s) {" % c.code)
        self.ctx.indent += 1
        self.block(s.els)
        self.ctx.indent -= 1
        self.emit("}")
        self.narrow(names)

    def long_return(self, n, target):
        """`return E from f` from a function called (somewhere below) from f."""
        fn = self.ctx.fn
        decls = self.fns.get(target)
        if not decls or decls[0].rets is None or not decls[0].rets:
            refuse("'%s' needs a declared result for 'return ... from %s'" % (target, target), n.line)
        rets = [self.resolve(r, {}) for r in decls[0].rets]
        if fn.rets is None:
            fn.rets = []                      # nothing to give back on the way out: a procedure
        tid = self.rf_ids[target]
        nodes = n.values
        spread = len(rets) > 1 and len(nodes) == 1
        if len(nodes) != len(rets) and not spread:
            refuse("'%s' returns %d value(s), but this returns %d" % (target, len(rets), len(nodes)), n.line)
        vals = self.operands(nodes, [None] if spread else rets)
        if spread:
            if vals[0].extra is None or len(vals[0].extra) + 1 != len(rets):
                refuse("this returns 1 value, but '%s' returns %d" % (target, len(rets)), n.line)
            vals = [vals[0]] + list(vals[0].extra)
        for i, (v, t) in enumerate(zip(vals, rets)):
            v = self.coerce(v, t, n.line, "result")
            self.global_defs = getattr(self, "global_defs", [])
            nm = "gs_rv%d_%d" % (tid, i)
            if nm not in self.extern_done:
                self.extern_done.add(nm)
                self.global_defs.append("static %s %s;" % (self.ctype(t), nm))
            self.emit("%s = %s;" % (nm, v.code))
        self.emit("gs_rf = %d;" % tid)
        self.unwind(None)
        self.emit_junk_return(fn)
        return None

    def e_Return(self, n, want):
        fn = self.ctx.fn
        frame = self.ctx.frame
        if n.frm is not None:
            target = n.frm.name if hasattr(n.frm, "name") else str(n.frm)
            cur = frame.name if frame.kind == "inline" else (fn.decl.name if fn.decl else "main")
            if cur != target and cur.rpartition("::")[2] != target:
                return self.long_return(n, target)
        if frame.kind == "inline":
            wants = frame.rets if frame.rets else [None] * len(n.values)
            if len(frame.res) > 1 and len(n.values) == 1:
                wants = [None]
            elif len(frame.res) != len(n.values) and (frame.rets or n.values):
                refuse("this returns %d value(s), but the function gives %d" %
                       (len(n.values), len(frame.res)), n.line)
            vals = self.operands(n.values, wants) if n.values else []
            if len(vals) == 1 and len(frame.res) > 1:
                if vals[0].extra is None or len(vals[0].extra) + 1 != len(frame.res):
                    refuse("this returns 1 value, but the function gives %d" % len(frame.res), n.line)
                vals = [vals[0]] + list(vals[0].extra)
            for r, v in zip(frame.res, vals):
                r.give(v, n.line)
            frame.used = True
            self.unwind(frame.exit_env)
            self.emit("goto %s;" % frame.label)
            return None
        nodes = n.values
        spread = fn.rets is not None and len(fn.rets) > 1 and len(nodes) == 1
        if fn.rets is not None and len(nodes) != len(fn.rets) and not spread:
            refuse("'%s' returns %d value(s), but this returns %d" %
                   (fn.decl.name if fn.decl else "main", len(fn.rets), len(nodes)), n.line)
        wants = fn.rets if fn.rets is not None else [None] * len(nodes)
        if spread:
            wants = [None]
        vals = self.operands(nodes, wants) if nodes else []
        if spread:
            if vals[0].extra is None or len(vals[0].extra) + 1 != len(fn.rets):
                refuse("'%s' returns %d value(s), but this returns 1" %
                       (fn.decl.name if fn.decl else "main", len(fn.rets)), n.line)
            vals = [vals[0]] + list(vals[0].extra)
        if fn.rets is None:
            vals = [self.default_lit(v) for v in vals]
            fn.rets = [v.ty for v in vals]
        vals = [self.coerce(v, t, n.line, "result") for v, t in zip(vals, fn.rets)]
        if self.has_frees(None):
            vals = [self.spill(v) for v in vals]
        for i in range(1, len(vals)):
            self.emit("*gs_o%d = %s;" % (i, vals[i].code))
        self.unwind(None)
        self.emit("return%s;" % (" " + vals[0].code if vals else ""))
        return None

    # -- aggregates -----------------------------------------------------------------------
    def seq_parts(self, v):
        """(data pointer code, length code) of a pure sequence value."""
        t = v.ty
        if t.k == "arr":
            return "%s.a" % v.code, "%d" % t.n
        if t.k == "lim":
            return "%s.a" % v.code, "%s.len" % v.code
        if t.k == "grow":
            return "%s.base" % v.code, "%s.len" % v.code
        return "%s.data" % v.code, "%s.len" % v.code

    def e_Field(self, n, want):
        b = n.base
        if b.kind == "Name" and b.ns is None and b.name in self.enums and self.lookup(b.name) is None:
            info = self.enums[b.name]
            names = [v.name for v in info.decl.variants]
            if n.name not in names:
                refuse("%s has no variant '%s'" % (info.name, n.name), n.line)
            idx = names.index(n.name)
            if self.enum_variants(info)[idx][1]:
                refuse("%s.%s has fields: write %s.%s { .. }" % (info.name, n.name, info.name, n.name), n.line)
            return Val("%s()" % self.variant_ctor(info, idx), info.variant_ty(idx))
        base = self.ex(n.base, None)
        if base.ty is None:
            refuse("a constant has no field '%s'" % n.name, n.line)
        t = base.ty
        if t.k in SEQ and n.name == "len":
            return Val("((int64_t)%d)" % t.n if t.k == "arr" else "%s.len" % base.code, I64, pure=base.pure)
        if t.k == "lim" and n.name == "cap":
            return Val("((int64_t)%d)" % t.n, I64, pure=True)
        if t.k == "variant":
            vname, fields = self.enum_variants(t.info)[t.n]
            for fname, ft, _ in fields:
                if fname == n.name:
                    return Val("%s.u.%s.f_%s" % (base.code, vname, fname), ft, lv=base.lv, pure=base.pure and base.lv)
            refuse("%s has no field '%s'" % (t.key, n.name), n.line)
        if t.k == "struct":
            for fname, ft, _ in self.struct_fields(t.info):
                if fname == n.name:
                    code = "%s.f_%s" % (base.code, fname)
                    return Val(code, ft, lv=base.lv, pure=base.pure and base.lv,
                               stk="(%s).stk" % code if ft.k == "grow" else None)
            refuse("%s has no field '%s'" % (t.name, n.name), n.line)
        refuse("%s has no field '%s'" % (t.key, n.name), n.line)

    def e_Index(self, n, want):
        base, idx = self.operands([n.base, n.index], [None, I64])
        base = self.spill_unless_pure(base)
        t = base.ty
        if t is None or t.k not in SEQ:
            refuse("cannot index %s" % (t.key if t else "a constant"), n.line)
        idx = self.index_val(idx, n.line)
        if t.k == "arr" and not base.lv:
            base = self.spill(base)
        data, length = self.seq_parts(base)
        return Val("%s[gs_chk(%s, %s, %d)]" % (data, idx.code, length, n.line), t.elem,
                   lv=base.lv or t.k != "arr")

    def spill_unless_pure(self, v):
        if v.ty is not None and not v.pure and v.lit is None:
            return self.spill(v)
        return v

    def index_val(self, idx, line):
        if idx.ty is None:
            idx = self.coerce(idx, I64, line)
        if idx.ty.k != "int":
            refuse("an index must be an integer, found %s" % idx.ty.key, line)
        return Val("((int64_t)%s)" % idx.code, I64, pure=idx.pure)

    def e_Slice(self, n, want):
        base = self.spill_unless_pure(self.ex(n.base, None))
        t = base.ty
        if t is None or t.k not in SEQ:
            refuse("cannot slice %s" % (t.key if t else "a constant"), n.line)
        if t.k in ("arr", "lim") and not base.lv:
            base = self.spill(base)
        data, length = self.seq_parts(base)

        def bound(b, default):
            if b is None:
                return default
            if b.kind == "FromEnd":
                v = self.index_val(self.ex(b.expr, I64), n.line)
                return "(%s - %s)" % (length, v.code)
            return self.index_val(self.ex(b, I64), n.line).code
        lo = bound(n.lo, "0")
        hi = bound(n.hi, length)
        self.ctx.ntmp += 1
        k = self.ctx.ntmp
        self.emit("int64_t lo%d = %s, hi%d = %s;" % (k, lo, k, hi))
        self.emit("if (lo%d < 0) gs_abort(\"slice out of range\", %d);" % (k, n.line))
        self.emit("if (hi%d < lo%d) gs_abort(\"slice out of range\", %d);" % (k, k, n.line))
        self.emit("if (hi%d > %s) gs_abort(\"slice out of range\", %d);" % (k, length, n.line))
        st = slice_ty(t.elem)
        return Val("((%s){%s + lo%d, hi%d - lo%d})" % (self.ctype(st), data, k, k, k), st, pure=True)

    def e_ArrayLit(self, n, want):
        if n.fill is not None:
            if getattr(n, "cap", None) is not None:
                unsupported("a capacity literal", n.line)
            elem_want = want.elem if want is not None and want.k == "arr" else None
            val = self.ex(n.fill[0], elem_want)
            count = self.const_int(n.fill[1], {})
            ev = self.coerce(val, elem_want, n.line) if elem_want else self.default_lit(val)
            at = arr_ty(ev.ty, count)
            t = self.tmp(self.ctype(at))
            self.emit("{ int64_t i; for (i = 0; i < %d; i++) %s.a[i] = %s; }" % (count, t, ev.code))
            return Val(t, at, lv=True, pure=True)
        if getattr(n, "cap", None) is not None:
            unsupported("a capacity literal '[..n]'", n.line)
        items = n.items
        if want is not None and want.k == "slice":
            want = None
        frozen_elem = want.elem if want is not None and want.k == "frozen" else None
        if want is not None and want.k == "frozen":
            want = None
        if want is not None and want.k == "lim":
            if len(items) > want.n:
                refuse("%d elements do not fit %s" % (len(items), want.key), n.line)
            vals = self.operands(items, [want.elem] * len(items)) if items else []
            t = self.tmp(self.ctype(want))
            self.emit("%s.len = %d;" % (t, len(vals)))
            for i, v in enumerate(vals):
                self.emit("%s.a[%d] = %s;" % (t, i, self.coerce(v, want.elem, n.line).code))
            return Val(t, want, lv=True, pure=True)
        if not items:
            if want is not None and want.k == "arr" and want.n == 0:
                return Val("((%s){{0}})" % self.ctype(want), want, pure=True)
            if want is not None and want.k in ("grow", "lim"):
                t = self.tmp(self.ctype(want))
                self.emit("memset(&%s, 0, sizeof %s);" % (t, t))
                return Val(t, want, lv=True, pure=True)
            unsupported("an empty array literal (it needs a growable array type)", n.line)
        elem_want = want.elem if want is not None and want.k == "arr" else frozen_elem
        vals = self.operands(items, [elem_want] * len(items))
        if elem_want is None:
            typed = [v.ty for v in vals if v.ty is not None]
            if typed:
                elem_want = typed[0]
            else:
                elem_want = F64 if any(isinstance(v.lit, float) for v in vals) else I64
        vals = [self.coerce(v, elem_want, n.line) for v in vals]
        if want is not None and want.k == "arr" and want.n != len(vals):
            refuse("expected %d elements, found %d" % (want.n, len(vals)), n.line)
        at = arr_ty(elem_want, len(vals))
        return Val("((%s){{%s}})" % (self.ctype(at), ", ".join(v.code for v in vals)), at)

    def e_StructLit(self, n, want):
        ty = n.type
        if ty.kind == "Field" and ty.base.kind == "Name" and ty.base.ns is None \
                and ty.base.name in self.enums and self.lookup(ty.base.name) is None:
            info = self.enums[ty.base.name]
            names = [v.name for v in info.decl.variants]
            if ty.name not in names:
                refuse("%s has no variant '%s'" % (info.name, ty.name), n.line)
            idx = names.index(ty.name)
            fields = self.enum_variants(info)[idx][1]
            parts = self.lit_parts("%s.%s" % (info.name, ty.name), fields, n)
            return Val("%s(%s)" % (self.variant_ctor(info, idx), ", ".join(parts)), info.variant_ty(idx))
        if ty.kind != "Name" or ty.ns:
            unsupported("this literal (only struct and enum-variant literals are in)", n.line)
        tnode = N("Named", ty.line, name=ty, args=getattr(ty, "args", None))
        known = ty.name in self.structs or ty.name in self.gstructs or ty.name in self.aliases
        if not known:
            unsupported("this literal (only struct and enum-variant literals are in)", n.line)
        st = self.resolve(tnode, self.fn_env)
        if st.k != "struct":
            refuse("%s is not a struct" % st.key, n.line)
        info = st.info
        fields = self.struct_fields(info)
        parts = self.lit_parts(info.name, fields, n) or ["0"]
        return Val("((%s){%s})" % (self.ctype(info.ty), ", ".join(parts)), info.ty)

    def lit_parts(self, label, fields, n):
        """The C values of a literal's fields, in declaration order."""
        inits = n.inits
        if len(inits) > len(fields):
            refuse("%s has %d field(s), but this gives %d" % (label, len(fields), len(inits)), n.line)
        named = [i.name is not None for i in inits]
        if any(named) and not all(named):
            refuse("a literal is all named or all positional", n.line)
        fnames = [f[0] for f in fields]
        if all(named) and inits:
            pos = -1
            by_name = {}
            for i in inits:
                if i.name not in fnames:
                    refuse("%s has no field '%s'" % (label, i.name), i.line)
                idx = fnames.index(i.name)
                if idx <= pos:
                    refuse("fields must be given in declaration order ('%s')" % i.name, i.line)
                pos = idx
                by_name[i.name] = i
            given = [by_name.get(f[0]) for f in fields]
        else:
            given = [inits[k] if k < len(inits) else None for k in range(len(fields))]
        nodes = []
        for (fname, fty, fdef), init in zip(fields, given):
            nodes.append(init.value if init is not None else fdef)
        real = [(nd, f[1]) for nd, f in zip(nodes, fields) if nd is not None]
        vals = self.operands([nd for nd, _ in real], [w for _, w in real])
        it = iter(vals)
        parts = []
        for (fname, fty, fdef), nd in zip(fields, nodes):
            if nd is None:
                if not n.rest and fty.k != "opt":                  # an optional field starts out null
                    refuse("field '%s' of %s has no value and no default" % (fname, label), n.line)
                parts.append(self.zero_code(fty))
            else:
                parts.append(self.coerce(next(it), fty, n.line).code)
        return parts

    def rel_size(self, ty):
        """Goose's packed size of a type that holds relative references, whose width differs from the
        pointer this translation uses; None for a type without any (its C size stands)."""
        k = ty.k
        if k in ("ref", "opt"):
            return ty.n if ty.n else None
        if k == "arr":
            e = self.rel_size(ty.elem)
            return None if e is None else e * ty.n
        if k == "struct":
            tot, rel = 0, False
            for _, ft, _ in self.struct_fields(ty.info):
                e = self.rel_size(ft)
                rel = rel or e is not None
                tot += e if e is not None else self.packed_size(ft)
            return tot if rel else None
        return None

    def packed_size(self, ty):
        k = ty.k
        if k in ("int", "float"):
            return ty.bits // 8
        if k == "bool":
            return 1
        if k in ("ref", "opt"):
            return ty.n or 8
        if k == "arr":
            return ty.n * self.packed_size(ty.elem)
        if k == "struct":
            return sum(self.packed_size(ft) for _, ft, _ in self.struct_fields(ty))
        unsupported("the size of %s in 'bytes_of'" % ty.key, 0)

    def zero_code(self, ty):
        if ty.k in ("int", "float", "bool", "opt", "ref"):
            return "0"
        return "{0}"

    # -- calls --------------------------------------------------------------------------------
    def e_Call(self, n, want):
        fn = n.fn
        targs = None
        tnodes = n.targs or (getattr(fn, "args", None) if fn.kind == "Name" else None)
        if tnodes:
            targs = [self.resolve(t, self.fn_env) for t in tnodes]
            if fn.kind == "Name" and fn.name == "default" and fn.ns is None and "default" not in self.fns:
                return self.default_value(targs, n.line)
        self.cur_targs = targs
        if fn.kind == "Name" and fn.ns == "":
            unsupported("a call of a global-scope '::name'", n.line)
        if fn.kind == "Name":
            name = self.fn_key(fn)
            fv = self.lookup(name) if fn.ns is None else None
            if isinstance(fv, FnVal):
                if n.block is not None:
                    refuse("'%s' is a function value; it takes no block" % name, n.line)
                return self.call_fnval(fv, n.args, n.line, want)
            if name == "print" and name not in self.fns:
                return self.print_call(n)
            if name not in self.fns:
                r = self.builtin_call(name, n)
                if r is not False:
                    return r
            if name in self.fns and n.block is None and n.args and n.args[-1].kind == "Name" \
                    and n.args[-1].ns is None:
                last = self.lookup(n.args[-1].name)
                if isinstance(last, FnVal) and last.kind == "nested":
                    # `sort(xs, by_name)`: a named function stands for the block `{ a, b => by_name(a, b) }`
                    fd, ln = last.node, n.line
                    prm = [N("Param", ln, name=p.name, type=None, default=None, var=False, ref=False)
                           for p in fd.params]
                    call = N("Call", ln, fn=n.args[-1], targs=None,
                             args=[N("Name", ln, name=p.name, ns=None) for p in fd.params], block=None)
                    blk = N("FnBlock", ln, params=prm, body=N("Block", ln, stmts=[], value=call))
                    n = N("Call", ln, fn=n.fn, targs=n.targs, args=n.args[:-1], block=blk)
            if name in self.fns:
                r = self.user_call(name, n.args, n.line, want, n.block, targs)
                if r is NotImplemented:
                    r = self.builtin_call(name, n)
                    if r is False:
                        refuse("no '%s' accepts these arguments" % name, n.line)
                return r
            refuse("unknown function '%s'" % name, n.line)
        if fn.kind == "Field":
            m = fn.name
            if m in self.SEQ_METHODS:
                with self.capture() as pre:
                    recv = self.ex(fn.base, None)
                is_seq = recv.ty is not None and recv.ty.k in ("grow", "lim")
                if m not in self.fns or is_seq:
                    self.ctx.out.extend(pre)
                    if not is_seq:
                        refuse("'.%s()' needs a growable or limited array, found %s" %
                               (m, recv.ty.key if recv.ty else "a constant"), n.line)
                    return self.seq_method(m, recv, n.args, n.line)
            if m == "index_of" and m not in self.fns:
                if len(n.args) != 1:
                    refuse("index_of takes one argument", n.line)
                recv, x = self.operands([fn.base, n.args[0]], [None, None])
                if recv.ty is None or recv.ty.k not in SEQ:
                    refuse("index_of needs an array", n.line)
                if x.ty != recv.ty.elem or not x.lv:
                    refuse("index_of takes a reference to an element", n.line)
                recv = self.spill_unless_pure(recv)
                data, length = self.seq_parts(recv)
                i = self.tmp("int64_t", "(int64_t)(%s - %s)" % (self.addr(x.code), data))
                self.emit("if (%s < 0 || %s >= %s) %s = -1;" % (i, i, length, i))
                return Val(i, I64, lv=True, pure=True)
            if m in self.fns:
                return self.user_call(m, [fn.base] + n.args, n.line, want, n.block, targs)
            if m in ("bytes_of", "exit", "abort", "assert", "format"):         # builtins in method form
                r = self.builtin_call(m, N("Call", n.line, fn=N("Name", n.line, name=m, ns=None), targs=n.targs,
                                           args=[fn.base] + n.args, block=n.block))
                if r is not False:
                    return r
            unsupported("the method '.%s()'" % m, n.line)
        unsupported("this call", n.line)

    def fn_key(self, node):
        """The key in self.fns that a call's name means: `ns::f`, or `f` looked
        up inside the namespace of the function being compiled first."""
        if node.ns:
            return "%s::%s" % (node.ns, node.name)
        cur = getattr(self, "cur_ns", "")
        if cur and "%s::%s" % (cur, node.name) in self.fns:
            return "%s::%s" % (cur, node.name)
        return node.name

    def only_here(self, d, nm):
        """True if the generic `nm` is named by exactly one parameter type and nothing else."""
        r = getattr(d, "_uses", None)
        if r is None:
            r = {}

            def walk(x, tag):
                if isinstance(x, N):
                    for v in vars(x).values():
                        walk(v, tag)
                elif isinstance(x, (list, tuple)):
                    for v in x:
                        walk(v, tag)
                elif isinstance(x, str):
                    r[x] = r.get(x, 0) + 1

            for p in d.params:
                if p.type is not None:
                    walk(p.type, 0)
            for t in d.rets or []:
                walk(t, 1)
            walk(d.body, 1)
            d._uses = r
        return r.get(nm, 0) == 1

    def takes_block(self, d):
        """True when a generic parameter of `d` appears in no parameter type: it is
        a function value, filled by the trailing block."""
        r = getattr(d, "_takes_block", None)
        if r is None:
            seen = set()

            def walk(x):
                if isinstance(x, N):
                    for v in vars(x).values():
                        walk(v)
                elif isinstance(x, (list, tuple)):
                    for v in x:
                        walk(v)
                elif isinstance(x, str):
                    seen.add(x)

            for p in d.params:
                if p.type is not None:
                    walk(p.type)
            r = any(g[0] not in seen for g in d.generics)
            d._takes_block = r
        return r

    def user_call(self, name, argnodes, line, want, block=None, targs=None):
        cands = [d for d in self.fns[name]
                 if len(d.params) >= len(argnodes)
                 and all(p.default is not None for p in d.params[len(argnodes):])]
        if not cands:
            if name == "format":
                return NotImplemented
            refuse("no '%s' takes %d argument(s)" % (name, len(argnodes)), line)
        # `f(xs) { .. }` is the overload with a function parameter, `f(xs)` the one without
        byblock = [d for d in cands if self.takes_block(d) == (block is not None)]
        if byblock:
            cands = byblock
        wants = [None] * len(argnodes)
        if len(cands) == 1:
            d = cands[0]
            for i, p in enumerate(d.params[:len(argnodes)]):
                if p.type is not None and not d.generics:
                    wants[i] = self.resolve(p.type, {})
        vals = self.operands(argnodes, wants)
        best = None
        for d in cands:
            r = self.match_call(d, vals, targs)
            if r is not None and (best is None or r[0] < best[0]):
                best = (r[0], d, r[1])
        if best is None:
            r = self.case_dispatch(name, cands, vals, line)
            if r is not NotImplemented:
                return r
        if best is None and name == "format":
            return NotImplemented
        if best is None:
            have = ", ".join(v.ty.key if v.ty else ("float" if isinstance(v.lit, float) else "integer")
                             for v in vals)
            refuse("no '%s' accepts (%s)" % (name, have), line)
        _, d, ptys = best
        # defaults for the missing trailing parameters
        vals = list(vals)
        for p, pt in zip(d.params[len(vals):], ptys[len(vals):]):
            vals.append(self.ex(p.default, pt))
        if "extern" in d.quals:
            return self.extern_call(d, vals, ptys, line)
        if block is not None or self.is_fnval_generic(d, ptys, targs):
            return self.inline_call(d, vals, ptys, block, line, want, targs)
        inst = self.instantiate(d, ptys, targs or [], line)
        if inst.rets is None and self.gives_nothing(d):
            inst.rets = []                       # a recursive procedure: nothing to infer
        if inst.rets is None:
            refuse("'%s' is recursive: give it a result type" % name, line)
        args = []
        for v, t in zip(vals, ptys):
            if t.k == "lit":
                continue
            cv = self.coerce(v, t, line, "argument")
            args.append(cv.code)
            if t.k == "ref" and t.elem.k == "grow":
                if cv.stk is None:
                    unsupported("passing a growable array that is not a variable", line)
                args.append(str(cv.stk))
                if t.elem.elem.k == "frozen":
                    if cv.pay is None:
                        unsupported("passing this array of frozen arrays", line)
                    args.append(str(cv.pay))
        return self.finish_call(inst, args, d.name, line)

    def finish_call(self, inst, args, name, line):
        """Emit the call of an instance and wrap its results."""
        rets = inst.rets
        if not rets:
            self.emit("%s(%s);" % (inst.cname, ", ".join(args)))
            self.rf_check(name, line)
            return None
        extra = []
        for t in rets[1:]:
            tv = self.tmp(self.ctype(t))
            extra.append(Val(tv, t, lv=True, pure=True, origin="call" if t.k == "frozen" else None))
            args.append("&" + tv)
        call = "%s(%s)" % (inst.cname, ", ".join(args))
        if rets[0].k == "ref" and not extra:
            r = self.tmp(self.ctype(rets[0]), call)
            return Val("(*%s)" % r, rets[0].elem, lv=True, pure=True)
        org = "call" if rets[0].k == "frozen" else None
        if self.rf_throws and self.may_throw(name):
            t0 = self.tmp(self.ctype(rets[0]), call)
            self.rf_check(name, line)
            return Val(t0, rets[0], lv=True, pure=True, extra=extra or None, origin=org)
        if not extra:
            return Val(call, rets[0], origin=org)
        t0 = self.tmp(self.ctype(rets[0]), call)
        return Val(t0, rets[0], lv=True, pure=True, extra=extra, origin=org)

    MATH = {"sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "exp", "log", "log2", "log10",
            "pow", "hypot", "cbrt", "exp2", "expm1", "log1p", "sinh", "cosh", "tanh", "floor", "ceil",
            "round", "trunc", "copysign", "fabs", "fmod", "frexp", "ldexp"}
    BYTE_HELPERS = {
        "gs_os_write_stdout": "static void gs_os_write_stdout(%(sl)s s) { fwrite(s.data, 1, (size_t)s.len, stdout); }",
        "gs_os_flush_stdout": "static void gs_os_flush_stdout(void) { fflush(stdout); }",
        "gs_os_read_line": """static uint8_t gs_os_read_line(%(gr)s *out) {
    int c, any = 0;
    for (;;) {
        c = fgetc(stdin);
        if (c == -1) return (uint8_t)any;
        any = 1;
        if (c == '\\n') return 1;
        if (gs_top[out->stk] + 1 > gs_lim[out->stk]) gs_abort("data stack overflow", 0);
        out->base[out->len++] = (uint8_t)c;
        gs_top[out->stk] += 1;
    }
}""",
        "gs_os_read_stdin": """static void gs_os_read_stdin(%(gr)s *out) {
    int c;
    while ((c = fgetc(stdin)) != -1) {
        if (gs_top[out->stk] + 1 > gs_lim[out->stk]) gs_abort("data stack overflow", 0);
        out->base[out->len++] = (uint8_t)c;
        gs_top[out->stk] += 1;
    }
}""",
        "gs_os_read_stdin_max": """static uint8_t gs_os_read_stdin_max(%(gr)s *out, int64_t max) {
    int c;
    int64_t n = 0, got = 0;
    while (n < max && (c = fgetc(stdin)) != -1) {
        if (gs_top[out->stk] + 1 > gs_lim[out->stk]) gs_abort("data stack overflow", 0);
        out->base[out->len++] = (uint8_t)c;
        gs_top[out->stk] += 1;
        n++; got = 1;
    }
    return (uint8_t)got;
}""",
        "gs_os_read_file": """static uint8_t gs_os_read_file(%(sl)s path, %(gr)s *out) {
    char *name = (char *)malloc((size_t)path.len + 1);
    FILE *f;
    size_t room, n;
    memcpy(name, path.data, (size_t)path.len);
    name[path.len] = 0;
    f = fopen(name, "rb");
    free(name);
    if (!f) return 0;
    room = (size_t)(gs_lim[out->stk] - gs_top[out->stk]);
    n = fread(out->base + out->len, 1, room, f);
    if (n == room && fgetc(f) != -1) gs_abort("data stack overflow", 0);
    fclose(f);
    gs_top[out->stk] += n;
    out->len += (int64_t)n;
    return 1;
}""",
        "gs_os_arg_count": "static int64_t gs_os_arg_count(void) { return gs_argc; }",
        "gs_os_arg": """static void gs_os_arg(int64_t i, %(gr)s *out) {
    size_t n;
    if (i < 0 || i >= gs_argc) gs_abort("argument index out of range", 0);
    n = strlen(gs_argv[i]);
    if (gs_top[out->stk] + n > gs_lim[out->stk]) gs_abort("data stack overflow", 0);
    memcpy(out->base + out->len, gs_argv[i], n);
    gs_top[out->stk] += n;
    out->len += (int64_t)n;
}""",
        "gs_os_write_file": """static uint8_t gs_os_write_file(%(sl)s path, %(sl)s data) {
    char *name = (char *)malloc((size_t)path.len + 1);
    FILE *f;
    size_t n;
    memcpy(name, path.data, (size_t)path.len);
    name[path.len] = 0;
    f = fopen(name, "wb");
    free(name);
    if (!f) return 0;
    n = fwrite(data.data, 1, (size_t)data.len, f);
    return (uint8_t)((fclose(f) == 0) && n == (size_t)data.len);
}""",
        "gs_f64_digits": """static int64_t gs_f64_digits(double x, %(sl)s dig, int64_t *point) {
    char buf[48];
    int p, i = 0, ex = 0, neg = 0;
    int64_t n = 0;
    double ax = x < 0 ? -x : x;
    for (p = 0; p < 17; p++) {
        snprintf(buf, sizeof buf, "%%.*e", p, ax);
        if (strtod(buf, 0) == ax) break;
    }
    for (i = 0; buf[i] && buf[i] != 'e'; i++) if (buf[i] != '.') dig.data[n++] = (uint8_t)buf[i];
    while (n > 1 && dig.data[n - 1] == '0') n--;
    if (buf[i] == 'e') { i++; if (buf[i] == '-') { neg = 1; i++; } else if (buf[i] == '+') i++;
        for (; buf[i]; i++) ex = ex * 10 + (buf[i] - '0'); }
    *point = (neg ? -ex : ex) + 1;
    return n;
}""",
        "gs_os_write_stderr": "static void gs_os_write_stderr(%(sl)s s) { fflush(stdout); fwrite(s.data, 1, (size_t)s.len, stderr); }",
    }
    BIT_HELPERS = {
        "gs_rotl32": "static uint32_t gs_rotl32(uint32_t x, int64_t n) { unsigned k = (unsigned)(n & 31); return k ? (uint32_t)((x << k) | (x >> (32 - k))) : x; }",
        "gs_rotr32": "static uint32_t gs_rotr32(uint32_t x, int64_t n) { unsigned k = (unsigned)(n & 31); return k ? (uint32_t)((x >> k) | (x << (32 - k))) : x; }",
        "gs_rotl64": "static uint64_t gs_rotl64(uint64_t x, int64_t n) { unsigned k = (unsigned)(n & 63); return k ? (x << k) | (x >> (64 - k)) : x; }",
        "gs_rotr64": "static uint64_t gs_rotr64(uint64_t x, int64_t n) { unsigned k = (unsigned)(n & 63); return k ? (x >> k) | (x << (64 - k)) : x; }",
        "gs_byte_swap16": "static uint16_t gs_byte_swap16(uint16_t x) { return (uint16_t)((x << 8) | (x >> 8)); }",
        "gs_byte_swap32": "static uint32_t gs_byte_swap32(uint32_t x) { return ((x & 0xFFu) << 24) | ((x & 0xFF00u) << 8) | ((x >> 8) & 0xFF00u) | (x >> 24); }",
        "gs_byte_swap64": "static uint64_t gs_byte_swap64(uint64_t x) { uint64_t r = 0; int i; for (i = 0; i < 8; i++) { r = (r << 8) | (x & 0xFF); x >>= 8; } return r; }",
        "gs_popcount": "static int64_t gs_popcount(uint64_t x) { int64_t n = 0; while (x) { n += (int64_t)(x & 1); x >>= 1; } return n; }",
        "gs_clz": "static int64_t gs_clz(uint64_t x) { int64_t n = 0; if (x == 0) return 64; while (!(x >> 63)) { x <<= 1; n++; } return n; }",
        "gs_ctz": "static int64_t gs_ctz(uint64_t x) { int64_t n = 0; if (x == 0) return 64; while (!(x & 1)) { x >>= 1; n++; } return n; }",
    }
    OS_BODIES = {
        "gs_os_clock_ns": ("long clock(void);", "static int64_t gs_os_clock_ns(void) { return (int64_t)clock() * 1000; }"),
        "gs_os_time_ns": ("long time(long *);", "static int64_t gs_os_time_ns(void) { return (int64_t)time(0) * 1000000000; }"),
    }

    def extern_call(self, d, vals, ptys, line):
        """A library function implemented in C: scalars in, a scalar (or nothing) out."""
        rets = [self.resolve(r, {}) for r in d.rets] if d.rets else []
        sym0 = d.sym or d.name.rpartition("::")[2]
        for t in list(ptys) + rets:
            scalar = t.k in ("int", "float", "bool") or (t.k == "ref" and t.elem.k in ("int", "float", "bool"))
            if not scalar and not ((t.k == "slice" or t.k == "ref" and t.elem.k == "grow") and sym0 in self.BYTE_HELPERS):
                unsupported("the library function '%s' (not a scalar signature)" % d.name, line)
        if len(rets) > 1:
            unsupported("the library function '%s'" % d.name, line)
        sym = sym0
        name = sym
        if sym in self.BYTE_HELPERS:
            if sym not in self.extern_done:
                self.extern_done.add(sym)
                self.helper_defs.append(self.BYTE_HELPERS[sym] % {"sl": self.ctype(slice_ty(U8)),
                                                                  "gr": self.ctype(grow_ty(U8))})
            name = sym
        elif sym in self.BIT_HELPERS:
            if sym not in self.extern_done:
                self.extern_done.add(sym)
                self.helper_defs.append(self.BIT_HELPERS[sym])
            name = sym
        elif sym in self.OS_BODIES:
            proto, body = self.OS_BODIES[sym]
            if sym not in self.extern_done:
                self.extern_done.add(sym)
                self.helper_defs.append(proto)
                self.helper_defs.append(body)
        else:
            if sym.startswith("gs_"):
                base = sym[3:]
                if base == "mul_add":
                    base = "fma"
                elif base == "mul_addf":
                    base = "fmaf"
                if base.rstrip("f") in self.MATH or base in self.MATH or base in ("fma", "fmaf"):
                    name = base
                else:
                    unsupported("the library function '%s' (needs C helper %s)" % (d.name, sym), line)
            if name not in self.extern_done:
                self.extern_done.add(name)
                self.helper_defs.append("%s %s(%s);" % (self.ctype(rets[0]) if rets else "void", name,
                                                         ", ".join(self.ctype(t) for t in ptys) or "void"))
        args = [self.coerce(v, t, line, "argument").code for v, t in zip(vals, ptys)]
        call = "%s(%s)" % (name, ", ".join(args))
        if not rets:
            self.emit("%s;" % call)
            return None
        return Val(call, rets[0])

    # -- function values: expanded inline ---------------------------------------------------
    def unbound_generics(self, d, ptys, targs=None):
        env = {}
        names = [g[0] for g in d.generics]
        if targs:
            env.update(zip(names, targs))
        for p, at in zip(d.params, ptys):
            if p.type is not None:
                self.unify(p.type, at, env, names)
        return env, [nm for nm in names if nm not in env]

    def is_fnval_generic(self, d, ptys, targs=None):
        return bool(d.generics) and bool(self.unbound_generics(d, ptys, targs)[1])

    def inline_call(self, d, vals, ptys, block, line, want, targs=None):
        env, unbound = self.unbound_generics(d, ptys, targs)
        if block is None:
            unsupported("a call that needs a function argument", line)
        if len(unbound) != 1:
            unsupported("'%s' with %d function parameters" % (d.name, len(unbound)), line)
        if d.body is None:
            refuse("function '%s' has no body" % d.name, line)
        c = self.ctx
        if any(f is d for f in getattr(self, "inline_active", [])):
            unsupported("a recursive function taking a block", line)
        rets = None
        if d.rets:
            # a growable result is handed back as a slice of its (released) data, which the
            # receiving variable copies onto its own stack, as for a call of a real function
            rets = [slice_ty(t.elem) if t.k == "grow" else t for t in (self.resolve(r, env) for r in d.rets)]
        self.ntmp_inline = getattr(self, "ntmp_inline", 0) + 1
        body_env = Env(None, c.env)
        frame = Frame("inline", rets, "ret%d" % (c.ntmp + 1), body_env)
        frame.name = d.name
        c.ntmp += 1
        frame.exit_env = c.env
        nres = len(rets) if rets else 1
        wants = rets if rets else [want] * nres
        frame.res = [Branch(self, wants[i]) for i in range(nres)]
        fv = FnVal("block", c.env, c.frame, self.fn_env, node=block)
        fv.ns = getattr(self, "cur_ns", "")
        fv.active = getattr(self, "inline_active", [])       # what was being expanded where the block is written
        body_env.vars[unbound[0]] = fv
        # parameters
        saved = (c.env, c.frame, c.breakables, self.fn_env)
        c.env, c.frame, c.breakables, self.fn_env = body_env, frame, [], env
        saved_ns = getattr(self, "cur_ns", "")
        self.cur_ns = d.name.rpartition("::")[0]
        self.emit("{")
        c.indent += 1
        try:
            for p, v, pt in zip(d.params, vals, ptys):
                if pt.k == "lit":
                    self.declare(p.name, I64, False).lit = pt.info
                    continue
                cv = self.coerce(v, pt, line, "argument")
                if pt.k == "ref":
                    stk = None
                    if pt.elem.k == "grow":
                        if cv.stk is None:
                            unsupported("passing a growable array that is not a variable", line)
                        stk = str(cv.stk)
                    nv = self.declare(p.name, pt.elem, mutable=p.var, ref=True, stk=stk)
                    if pt.elem.k == "grow" and pt.elem.elem.k == "frozen":
                        nv.pay = str(cv.pay)
                    self.emit("%s %s = %s;" % (self.ctype(pt), nv.code, cv.code))
                else:
                    nv = self.declare(p.name, pt, mutable=p.var)
                    self.emit("%s %s = %s;" % (self.ctype(pt), nv.code, cv.code))
            self.inline_active = getattr(self, "inline_active", []) + [d]
            try:
                val = self.block(d.body, want=(rets[0] if rets and len(rets) == 1 else None), tail=True)
            except GooseError as e:
                self.annotate(e, d)
                raise
            finally:
                self.inline_active = self.inline_active[:-1]
            if val is not None:
                if len(frame.res) != 1:
                    refuse("function '%s' returns %d values; its last expression is one" % (d.name, nres), line)
                frame.res[0].give(val, d.body.line)
        finally:
            c.indent -= 1
            c.env, c.frame, c.breakables, self.fn_env = saved
            self.cur_ns = saved_ns
        self.emit("}")
        if frame.used:
            self.emit("%s:;" % frame.label)
        outs = [r.finish(line) for r in frame.res]
        if rets is not None and len(rets) != len([o for o in outs if o is not None]) and rets:
            if any(o is None for o in outs):
                outs = [o for o in outs]
        if not rets and outs[0] is None:
            return None
        if len(outs) == 1:
            return outs[0]
        return Val(outs[0].code, outs[0].ty, lv=True, pure=True, extra=outs[1:])

    def s_FnDecl(self, s):
        if s.generics:
            unsupported("a generic nested function", s.line)
        if s.body is None:
            refuse("function '%s' has no body" % s.name, s.line)
        c = self.ctx
        fv = FnVal("nested", c.env, c.frame, self.fn_env, node=s, name=s.name)
        fv.ns = getattr(self, "cur_ns", "")
        fv.inst = None
        c.env.vars[s.name] = fv

    def build_nested(self, fv, line):
        """Lift a nested function to a C function: the enclosing locals it can
        see become pointer parameters after its own."""
        d = fv.node
        self.ninst += 1
        fn = Fn(d, "%s_n%d" % (d.name.replace("::", "_"), self.ninst))
        for p in d.params:
            if p.type is None:
                refuse("parameter '%s' of the nested function '%s' needs a type" % (p.name, d.name), d.line)
        fv.ptys = [self.resolve(p.type, fv.tenv) for p in d.params]
        fn.params = fv.ptys
        if d.rets:
            fn.rets = [self.resolve(r, fv.tenv) for r in d.rets]
        fv.inst = fn
        caps, seen, shared = [], set(), {}
        e = fv.env
        while e is not None:
            for name, v in e.vars.items():
                if name in seen:
                    continue
                seen.add(name)
                if isinstance(v, FnVal):
                    if v.kind != "block":
                        shared[name] = v
                elif v.lit is not None:
                    shared[name] = v
                else:
                    caps.append((name, v))
            e = e.parent
        fv.caps = caps
        saved = (self.ctx, self.fn_env, getattr(self, "cur_ns", ""))
        saved_active, self.inline_active = getattr(self, "inline_active", []), []
        self.ctx = Ctx(fn)
        self.fn_env, self.cur_ns = fv.tenv, fv.ns
        try:
            c = self.ctx
            c.env.vars.update(shared)
            params = []
            for name, v in caps:
                nv = self.declare(name, v.ty, mutable=v.mutable, ref=True, stk=("%s_stk" % name) if v.stk is not None else None)
                params.append("%s *%s" % (self.ctype(v.ty), nv.code))
                if v.stk is not None:
                    params.append("int %s" % nv.stk)
                    if v.pay is not None:
                        nv.pay = "%s_pay" % nv.code
                        params.append("int %s" % nv.pay)
            self.push()
            own = []
            for p, pt in zip(d.params, fv.ptys):
                if pt.k == "lit":
                    refuse("a nested function cannot take a constant-specialised parameter", d.line)
                if pt.k == "ref":
                    v = self.declare(p.name, pt.elem, mutable=p.var, ref=True,
                                     stk=("%s_stk" % ("%s_g" % p.name)) if pt.elem.k == "grow" else None)
                    own.append("%s %s" % (self.ctype(pt), v.code))
                    if pt.elem.k == "grow":
                        own.append("int %s" % v.stk)
                        if pt.elem.elem.k == "frozen":
                            v.pay = "%s_pay" % v.code
                            own.append("int %s" % v.pay)
                    continue
                v = self.declare(p.name, pt, mutable=p.var)
                own.append("%s %s" % (self.ctype(pt), v.code))
            self.gen_body(fn, d.body, fv.tenv)
            self.pop()
            rets = fn.rets or []
            params = own + params
            for i in range(1, len(rets)):
                params.append("%s *gs_o%d" % (self.ctype(rets[i]), i))
            sig = "static %s %s(%s)" % (self.ctype(rets[0]) if rets else "void", fn.cname,
                                        ", ".join(params) or "void")
            self.protos.append(sig + ";")
            self.bodies.append(sig + " {")
            self.bodies += c.out
            self.bodies.append("}")
        except GooseError as e:
            self.annotate(e, d)
            raise
        finally:
            self.ctx, self.fn_env, self.cur_ns = saved
            self.inline_active = saved_active

    def call_nested(self, fv, argnodes, line, want):
        if fv.inst is None:
            self.build_nested(fv, line)
        inst = fv.inst
        d = fv.node
        if len(argnodes) != len(fv.ptys):
            refuse("'%s' takes %d argument(s), but is called with %d" % (d.name, len(fv.ptys), len(argnodes)), line)
        vals = self.operands(argnodes, fv.ptys)
        args = []
        for v, t in zip(vals, fv.ptys):
            cv = self.coerce(v, t, line, "argument")
            args.append(cv.code)
            if t.k == "ref" and t.elem.k == "grow":
                if cv.stk is None:
                    unsupported("passing a growable array that is not a variable", line)
                args.append(str(cv.stk))
                if t.elem.elem.k == "frozen":
                    args.append(str(cv.pay))
        for name, v in fv.caps:
            cur = self.lookup(name)
            if cur is None or isinstance(cur, FnVal) or cur.lit is not None:
                refuse("'%s' uses '%s', which is not visible where it is called" % (d.name, name), line)
            args.append(cur.code if cur.ref else "&" + cur.code)
            if v.stk is not None:
                args.append(str(cur.stk))
                if v.pay is not None:
                    args.append(str(cur.pay))
        if inst.rets is None and self.gives_nothing(d):
            inst.rets = []
        if inst.rets is None and inst.decl is not None and self.in_progress(inst):
            refuse("'%s' is recursive: give it a result type" % d.name, line)
        return self.finish_call(inst, args, d.name, line)

    @staticmethod
    def gives_nothing(d):
        """A function without a declared result whose body never returns a value."""
        if d.rets or d.body is None:
            return False
        tail = d.body.value
        if tail is not None and not (tail.kind in ("For", "While", "Loop") or
                                     tail.kind == "If" and tail.els is None):
            return False

        def has_value_return(x):
            if isinstance(x, N):
                if x.kind == "Return" and x.values:
                    return True
                if x.kind == "FnDecl":
                    return False
                return any(has_value_return(v) for v in vars(x).values())
            if isinstance(x, (list, tuple)):
                return any(has_value_return(v) for v in x)
            return False
        return not has_value_return(d.body)

    def in_progress(self, inst):
        return inst.rets is None and self.ctx.fn is inst

    def call_fnval(self, fv, argnodes, line, want):
        node = fv.node
        if fv.kind == "nested":
            return self.call_nested(fv, argnodes, line, want)
        if fv.kind != "block":
            unsupported("this function value", line)
        c = self.ctx
        vals = self.operands(argnodes, [None] * len(argnodes))
        params = node.params
        if params is None:
            if len(vals) < 1:
                if True:
                    params = []
            else:
                params = [N("Param", line, name="it", type=None, default=None, var=False, ref=False)]
                vals = vals[:1]
        elif len(params) != len(vals):
            refuse("this block takes %d parameter(s), but is called with %d" % (len(params), len(vals)), line)
        env = Env(fv.env, c.env)
        saved = (c.env, c.frame, c.breakables, self.fn_env)
        c.env, c.frame, c.breakables, self.fn_env = env, fv.frame, [], fv.tenv
        saved_ns, self.cur_ns = getattr(self, "cur_ns", ""), fv.ns
        saved_active = getattr(self, "inline_active", [])
        self.inline_active = getattr(fv, "active", saved_active)
        try:
            for p, v in zip(params, vals):
                if v.ty is None:
                    v = self.default_lit(v)
                if v.ty.k == "ref":                       # `F(&x)`: the block's parameter is that reference
                    ptr = self.tmp(self.ctype(v.ty), v.code)
                    nv = self.declare(p.name, v.ty.elem, mutable=True, ref=True, stk=v.stk)
                    nv.code = ptr
                    nv.pay = v.pay
                elif v.lv and (p.ref or not p.var):
                    ptr = self.tmp("%s *" % self.ctype(v.ty), "&" + v.code) if v.ty.k not in ("ref", "opt") or True else None
                    nv = self.declare(p.name, v.ty, mutable=bool(p.ref and True), ref=True, stk=v.stk)
                    nv.code = ptr
                else:
                    nv = self.declare(p.name, v.ty, mutable=True)
                    self.emit("%s %s = %s;" % (self.ctype(v.ty), nv.code, v.code))
            val = self.block(node.body, want)
            if val is not None and val.ty is not None and not val.pure and val.lit is None:
                val = self.spill(val)
        finally:
            c.env, c.frame, c.breakables, self.fn_env = saved
            self.cur_ns = saved_ns
            self.inline_active = saved_active
        return val

    def case_dispatch(self, name, cands, vals, line):
        """`area(s)` with `s: Shape` where the overloads are `area(s: Shape.Circle)`,
        `area(s: Shape.Rect)`, ...: one call per variant, chosen by the tag."""
        idx = None
        for i, v in enumerate(vals):
            if v.ty is not None and v.ty.k == "enum":
                for d in cands:
                    if i < len(d.params) and d.params[i].type is not None and d.params[i].type.kind == "VariantType":
                        idx = i
        if idx is None:
            return NotImplemented
        info = vals[idx].ty.info
        sv = self.spill(vals[idx])
        picks = []
        for vi, v in enumerate(info.decl.variants):
            vv = list(vals)
            vv[idx] = Val(sv.code, info.variant_ty(vi), lv=sv.lv, pure=True)
            best = None
            for d in cands:
                r = self.match_call(d, vv)
                if r is not None and (best is None or r[0] < best[0]):
                    best = (r[0], d, r[1])
            if best is None:
                refuse("'%s' has no case for %s.%s" % (name, info.name, v.name), line)
            _, d, ptys = best
            picks.append((vi, d, ptys, vv))
        insts = [(vi, self.instantiate(d, ptys, [], line), ptys, vv) for vi, d, ptys, vv in picks]
        rets0 = insts[0][1].rets
        if rets0 is None or any(i[1].rets != rets0 for i in insts):
            refuse("the cases of '%s' must all return the same type" % name, line)
        if len(rets0) > 1:
            unsupported("a case function with several results", line)
        res = self.tmp(self.ctype(rets0[0])) if rets0 else None
        self.emit("switch (%s.tag) {" % sv.code)
        for vi, inst, ptys, vv in insts:
            args = [self.coerce(v, t, line, "argument").code for v, t in zip(vv, ptys)]
            call = "%s(%s)" % (inst.cname, ", ".join(args))
            self.emit("    case %d: %s; break;" % (vi, ("%s = %s" % (res, call)) if res else call))
        self.emit("}")
        return Val(res, rets0[0], lv=True, pure=True) if res else None

    def match_call(self, d, vals, targs=None):
        """(score, parameter types) if `d` accepts `vals`, else None."""
        env, ptys, score = {}, [], 0
        names = [g[0] for g in d.generics]
        if targs:
            if len(targs) > len(names):
                return None
            env.update(zip(names, targs))
        deferred = []
        for p, v in zip(d.params, vals):
            if p.type is None:
                t = self.default_lit(v).ty if v.ty is None else v.ty
                ptys.append(t)
                continue
            if names:
                if v.ty is not None:
                    self.unify(p.type, v.ty, env, names)
                elif p.type.kind == "Named" and p.type.name.name in names and p.type.args is None \
                        and v.lit is not None:
                    deferred.append(len(ptys))        # a constant for a bare T: see below
                    ptys.append(None)
                    continue
            try:
                pt = self.resolve(p.type, env)
            except GooseError:
                return None
            s = self.accepts(v, pt)
            if s is None:
                return None
            score += s
            ptys.append(pt)
        for i in deferred:
            nm = d.params[i].type.name.name
            v = vals[i]
            if nm in env:                              # another argument fixed T
                s = self.accepts(v, env[nm])
                if s is None:
                    return None
                score += s
                ptys[i] = env[nm]
            elif self.only_here(d, nm):                # T is only a constant: specialise on its value
                ptys[i] = Ty("lit", "lit:%r" % (v.lit,), info=v.lit)
            else:
                t = self.default_lit(v).ty
                env[nm] = t
                ptys[i] = t
        for p in d.params[len(vals):]:
            if p.type is None:
                return None
            try:
                ptys.append(self.resolve(p.type, env))
            except GooseError:
                return None
        return score, ptys

    def accepts(self, v, pt):
        if pt.k in ("ref", "opt"):
            if v.ty is NULLTY:
                return 0 if pt.k == "opt" else None
            if v.ty == pt:
                return 0
            if v.ty is not None and v.ty == pt.elem and v.lv:
                return 0 if pt.k == "ref" else 1
            return None
        if v.ty is NULLTY:
            return None
        if v.ty is None:
            if pt.k == "float":
                return 1
            if pt.k == "int" and isinstance(v.lit, int) and self.fits(v.lit, pt):
                return 0 if pt == I64 else 1
            return None
        if v.ty == pt:
            return 0
        if pt.k == "enum" and v.ty.k == "variant" and v.ty.info is pt.info:
            return 2
        if self.widen_ok(v.ty, pt):
            return 3 if v.ty.k == "int" and pt.k == "float" else 2
        if pt.k == "slice" and v.ty.k in ("arr", "grow", "lim", "frozen") and v.ty.elem == pt.elem:
            return 1
        if pt.k == "frozen" and v.ty.k in ("arr", "slice", "grow", "lim") and v.ty.elem == pt.elem:
            return 2
        if pt.k == "lim" and v.ty.k in ("slice", "arr", "grow", "lim") and v.ty.elem == pt.elem:
            return 3
        return None

    # -- print ----------------------------------------------------------------------------------
    def print_call(self, n):
        vals = self.operands(n.args, [None] * len(n.args))
        for v in vals:
            v = self.default_lit(v) if v.ty is None else v
            self.print_val(v, True)
        self.emit("gs_p_lit(\"\\n\");")
        return None

    def format_overload(self, t):
        """A user `fn format(out: u8[>..]&, v: T)` that renders values of type `t`."""
        for d in self.fns.get("format", []):
            if d.generics or len(d.params) != 2 or d.params[0].type is None or d.params[1].type is None:
                continue
            try:
                p0 = self.resolve(d.params[0].type, {})
                p1 = self.resolve(d.params[1].type, {})
            except GooseError:
                continue
            if p0.k == "ref" and p0.elem == grow_ty(U8) and (p1 == t or (p1.k == "ref" and p1.elem == t)):
                return d, p0, p1
        return None

    def print_via_overload(self, found, v, line=0):
        d, p0, p1 = found
        v = self.spill(v) if not v.lv else v
        k = self.new_stack()
        gt = grow_ty(U8)
        t = self.tmp(self.ctype(gt))
        self.emit("%s.base = (uint8_t *)gs_top[%d]; %s.len = 0;" % (t, k, t))
        inst = self.instantiate(d, [p0, p1], [], line)
        arg = self.coerce(v, p1, line, "argument")
        extra = ""
        if p1.k == "ref" and p1.elem.k == "grow":
            if arg_stk(v) is None:
                unsupported("formatting a growable array that is not a variable", line)
            extra = ", %s" % arg_stk(v)
            if p1.elem.elem.k == "frozen":
                extra += ", %s" % v.pay
        # the overload may print or format by itself: it must leave the sink we write to as it was
        sv = self.ctx.ntmp = self.ctx.ntmp + 1
        self.emit("uint8_t *sb%d = gs_sbuf, **st%d = gs_stop; int64_t *sl%d = gs_slen, sc%d = gs_scap;" % (sv, sv, sv, sv))
        self.emit("%s(&%s, %d, %s%s);" % (inst.cname, t, k, arg.code, extra))
        self.emit("gs_sbuf = sb%d; gs_stop = st%d; gs_slen = sl%d; gs_scap = sc%d;" % (sv, sv, sv, sv))
        self.emit("gs_p_raw((const uint8_t *)%s.base, %s.len);" % (t, t))
        self.emit("gs_top[%d] = (uint8_t *)%s.base;" % (k, t))

    def print_val(self, v, top):
        t = v.ty
        if t.k in ("struct", "enum", "variant", "grow", "lim", "arr", "slice") and "format" in self.fns:
            found = self.format_overload(t.info.ty if t.k == "variant" else t)
            if found:
                self.print_via_overload(found, v)
                return
        if t.k == "int":
            self.emit("gs_p_%s((%s)%s);" % ("i64" if t.signed else "u64",
                                           "int64_t" if t.signed else "uint64_t", v.code))
        elif t.k == "float":
            self.emit("gs_p_float((double)%s, %d);" % (v.code, 1 if t.bits == 32 else 0))
        elif t.k == "bool":
            self.emit("gs_p_bool(%s);" % v.code)
        elif t.k == "opt":
            self.emit("if (!%s) gs_p_lit(\"null\"); else {" % v.code)
            self.ctx.indent += 1
            self.print_val(Val("(*%s)" % v.code, t.elem, lv=True, pure=True), top)
            self.ctx.indent -= 1
            self.emit("}")
        else:
            v = self.spill(v) if not v.lv else v
            self.emit("%s(&%s, %d);" % (self.printer(t), v.code, 1 if top else 0))

    def eq_expr(self, a, b, t):
        if t.k in ("int", "float", "bool"):
            return "(%s == %s)" % (a, b)
        if t.k in ("struct", "enum", "arr", "variant"):
            return "%s(&%s, &%s)" % (self.eqfn(t), a, b)
        unsupported("'==' on %s" % t.key, 0)

    def seq_eqfn(self, elem):
        name = "gs_eqs_%s" % mangle(elem.key)
        if name in self.printers:
            return name
        self.printers[name] = name
        c = self.ctype(elem)
        sig = "static int %s(const %s *a, int64_t na, const %s *b, int64_t nb)" % (name, c, c)
        self.printer_protos.append(sig + ";")
        self.printer_defs.append(sig + " {")
        self.printer_defs.append("    int64_t i;")
        self.printer_defs.append("    if (na != nb) return 0;")
        self.printer_defs.append("    for (i = 0; i < na; i++) if (!%s) return 0;" %
                                 self.eq_expr("a[i]", "b[i]", elem))
        self.printer_defs.append("    return 1;")
        self.printer_defs.append("}")
        return name

    def eqfn(self, t):
        if t.k == "variant":
            t = t.info.ty
        name = "gs_eq_%s" % mangle(t.key)
        if name in self.printers:
            return name
        self.printers[name] = name
        c = self.ctype(t)
        self.printer_protos.append("static int %s(const %s *a, const %s *b);" % (name, c, c))
        body = []
        if t.k == "struct":
            terms = [self.eq_expr("a->f_%s" % fn, "b->f_%s" % fn, ft) for fn, ft, _ in self.struct_fields(t.info)]
            body.append("    return %s;" % (" && ".join(terms) or "1"))
        elif t.k == "arr":
            body.append("    int64_t i;")
            body.append("    for (i = 0; i < %d; i++) if (!%s) return 0;" %
                        (t.n, self.eq_expr("a->a[i]", "b->a[i]", t.elem)))
            body.append("    return 1;")
        else:
            body.append("    if (a->tag != b->tag) return 0;")
            body.append("    switch (a->tag) {")
            for vi, (vname, fields) in enumerate(self.enum_variants(t.info)):
                terms = [self.eq_expr("a->u.%s.f_%s" % (vname, fn), "b->u.%s.f_%s" % (vname, fn), ft)
                         for fn, ft, _ in fields]
                body.append("    case %d: return %s;" % (vi, " && ".join(terms) or "1"))
            body.append("    }")
            body.append("    return 1;")
        self.printer_defs.append("static int %s(const %s *a, const %s *b) {" % (name, c, c))
        self.printer_defs += body
        self.printer_defs.append("}")
        return name

    def printer(self, t):
        if t.k == "variant":
            t = t.info.ty
        if t.key in self.printers:
            return self.printers[t.key]
        name = "gs_pr_%s" % mangle(t.key)
        self.printers[t.key] = name
        self.printer_protos.append("static void %s(const %s *v, int top);" % (name, self.ctype(t)))
        saved = self.ctx
        self.ctx = Ctx(Fn(None, name))
        self.ctx.fn.rets = []
        self.ctx.indent = 1
        body = self.ctx.out
        if t.k == "variant":
            t = t.info.ty
        if t.k == "enum":
            body.append("    switch (v->tag) {")
            for vi, (vname, fields) in enumerate(self.enum_variants(t.info)):
                body.append("    case %d:" % vi)
                if not fields:
                    body.append("        gs_p_lit(\"%s.%s\");" % (t.name, vname))
                else:
                    body.append("        gs_p_lit(\"%s { \");" % vname)
                    self.ctx.indent = 2
                    for j, (fname, ft, _) in enumerate(fields):
                        if j:
                            body.append("        gs_p_lit(\", \");")
                        self.print_val(Val("v->u.%s.f_%s" % (vname, fname), ft, lv=True, pure=True), False)
                    self.ctx.indent = 1
                    body.append("        gs_p_lit(\" }\");")
                body.append("        break;")
            body.append("    }")
        elif t.k in SEQ:
            data, n = self.seq_parts(Val("(*v)", t))
            if t.elem == U8:
                body.append("    if (top) gs_p_raw((const uint8_t *)%s, %s); else gs_p_quoted((const uint8_t *)%s, %s);"
                            % (data, n, data, n))
            else:
                body.append("    int64_t i;")
                body.append("    gs_p_lit(\"[\");")
                body.append("    for (i = 0; i < %s; i++) {" % n)
                body.append("        if (i) gs_p_lit(\", \");")
                ev = Val("%s[i]" % data, t.elem, lv=True, pure=True)
                self.ctx.indent = 2
                self.print_val(ev, False)
                body.append("    }")
                self.ctx.indent = 1
                body.append("    gs_p_lit(\"]\");")
        elif t.k == "struct":
            fields = self.struct_fields(t.info)
            body.append("    gs_p_lit(\"%s { \");" % t.info.display)
            for i, (fname, ft, _) in enumerate(fields):
                if i:
                    body.append("    gs_p_lit(\", \");")
                self.print_val(Val("v->f_%s" % fname, ft, lv=True, pure=True), False)
            body.append("    gs_p_lit(\" }\");")
        else:
            raise AssertionError(t.key)
        self.printer_defs.append("static void %s(const %s *v, int top) {" % (name, self.ctype(t)))
        self.printer_defs.append("    (void)top;")
        self.printer_defs += body
        self.printer_defs.append("}")
        self.ctx = saved
        return name


def arg_stk(v):
    return v.stk


class Branch:
    """The arms of an if/match/block/loop that yields a value assign into one
    temporary, declared before the construct once the arms' type is known."""

    def __init__(self, gen, want):
        self.gen, self.want = gen, want
        c = gen.ctx
        c.ntmp += 1
        self.tmp = "t%d" % c.ntmp
        self.tmp_used = False
        self.ty = None
        self.out, self.at, self.indent = c.out, len(c.out), c.indent
        self.pending = []
        self.void_arm = False

    def give(self, v, line):
        g = self.gen
        if v is None or (v.ty is None and v.lit is None):
            self.void_arm = True
            return
        self.tmp_used = True
        if v.ty is None:                                   # an untyped constant
            ty = self.ty or self.want
            if ty is None or ty.k not in ("int", "float"):
                g.emit("")
                self.pending.append((g.ctx.out, len(g.ctx.out) - 1, v.lit, g.ctx.indent, line))
                return
            v = g.coerce(v, ty, line)
        if self.ty is None:
            self.ty = v.ty
            if self.want is not None and g.accepts(v, self.want) is not None:
                self.ty = self.want
        v = g.coerce(v, self.ty, line)
        g.emit("%s = %s;" % (self.tmp, v.code))

    def finish(self, line):
        g = self.gen
        if not self.tmp_used:
            return None
        if self.void_arm:
            refuse("the arms of this construct do not all give a value", line)
        ty = self.ty
        if ty is None:
            lits = [p[2] for p in self.pending]
            ty = self.want if self.want is not None and self.want.k in ("int", "float") else \
                (F64 if any(isinstance(x, float) for x in lits) else I64)
            self.ty = ty
        for lst, idx, lit, ind, ln in self.pending:
            v = g.coerce(Val("", None, lit=lit), ty, ln)
            lst[idx] = "    " * ind + "%s = %s;" % (self.tmp, v.code)
        self.out.insert(self.at, "    " * self.indent + "%s %s;" % (g.ctype(ty), self.tmp))
        return Val(self.tmp, ty, lv=True, pure=True)


def translate(text, path="<goose>"):
    decls = parse(text, path)
    try:
        return Gen(path).module(decls)
    except GooseError as e:
        e.path = path
        raise


def main(argv):
    args = argv[1:]
    out = None
    if "-o" in args:
        i = args.index("-o")
        out = args[i + 1]
        del args[i:i + 2]
    if len(args) != 1:
        print(__doc__)
        return 2
    path = args[0]
    try:
        with open(path, encoding="utf-8") as f:
            c = translate(f.read(), path)
    except GooseError as e:
        msg = str(e)
        if out:
            with open(out, "w") as f:
                f.write(msg + "\n")
        print(msg, file=sys.stderr)
        return 1
    if out:
        with open(out, "w") as f:
            f.write(c)
    else:
        sys.stdout.write(c)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
