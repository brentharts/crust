# SPDX-License-Identifier: MIT
"""unity_pack --hybrid: a script method the packer cannot lower to C runs as managed code on DotNetAnywhere.

Without --hybrid a method with C# left in it after lowering becomes an empty stub and a `CS8000` warning: the method does nothing at run time.
With it, such a method keeps its own C# source, compiled (mcs) against a small managed UnityEngine (tools/unity_pack_managed/UnityShim.cs)
and run by DotNetAnywhere, which is linked into the player.  The packed engine is not changed:

    native  engine.c    Spark_Tally(i)  ->  ccs_b_Spark_Tally(i)   (hybrid_glue.c: DNA_Find / DNA_Call)   ->  managed Spark.__Tally(i)
    managed Spark.hp  ->  ccs_x_Spark_get_hp(i)   (engine.c: a wrapper of the accessor the lowered code uses)   ->  the packed array

An object is its index.  The managed class is a handle to it: its fields are PROPERTIES that read and write the packed arrays through the
accessors engine.c already has (`Spark_get_hp(i)`), so state is held once, natively, and a managed method and a lowered one see the same.

What this does not do, by design: change what a pack that worked did.  A class whose managed code does not compile (a Unity member the shim
lacks, a field the packed engine stores in a form managed code cannot reach) keeps its stubs and its CS8000 warnings, with the reason added.
A field is never copied into the managed object: that would be a second copy that silently disagrees.

Pieces written beside engine.c:  hybrid_generated.cs  hybrid.managed.dll  hybrid_glue.c  hybrid.ffi.json
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SHIM = os.path.join(HERE, "unity_pack_managed", "UnityShim.cs")
ASSEMBLY = "hybrid.managed.dll"

# C# type -> (managed type, FFI manifest type, DotNetAnywhere value letter, C type in the glue, constructor of the value)
SCALAR = {
    "int": ("int", "int", "i", "int", "DNA_Int"), "uint": ("uint", "uint", "i", "unsigned", "DNA_Int"),
    "short": ("short", "short", "i", "short", "DNA_Int"), "ushort": ("ushort", "ushort", "i", "unsigned short", "DNA_Int"),
    "sbyte": ("sbyte", "sbyte", "i", "signed char", "DNA_Int"), "byte": ("byte", "byte", "i", "unsigned char", "DNA_Int"),
    "bool": ("bool", "int", "i", "int", "DNA_Int"),
    "long": ("long", "long", "l", "long long", "DNA_Long"),
    "float": ("float", "float", "f", "float", "DNA_Float"), "double": ("double", "double", "d", "double", "DNA_Double"),
}
# A Vector2 crosses the boundary as its two floats; a Vector2 result comes back through a two-float slot (ccs_ret2, in hybrid_glue.c) that the
# callee sets and the caller reads, since neither the FFI nor DNA_Call returns a struct.
VEC2 = "Vector2"


def _carrier(t):
    """may a value of C# type *t* cross the boundary?"""
    return t in SCALAR or t == VEC2


def _ctype(t):
    return VEC2 if t == VEC2 else SCALAR[t][3]


def _mtype(t):
    return VEC2 if t == VEC2 else SCALAR[t][0]


def _ffi_args(params):
    out = []
    for t, _n in params:
        out += ["float", "float"] if t == VEC2 else [SCALAR[t][1]]
    return out


def _dna_letters(params):
    return "".join("ff" if t == VEC2 else SCALAR[t][2] for t, _n in params)


# what an engine accessor's C type is, in the manifest
ACCESSOR_FFI = {"unsigned": "uint", "int": "int", "float": "float", "double": "double"}
# the fields managed code can have as properties: C# type -> (getter cast, setter cast)
FIELD_TYPES = {"int", "uint", "short", "ushort", "sbyte", "byte", "bool", "float"}


# the names of the managed Transform's rotation API: a class whose managed code uses one needs the engine's rotation storage (ClassGen._rotation_storage)
ROTATION_WORDS = frozenset(("rotation", "localRotation", "eulerAngles", "localEulerAngles", "Rotate", "LookAt", "TransformDirection", "InverseTransformDirection"))


class HybridError(Exception):
    pass


# ---- the toolchain ---------------------------------------------------------------------------------------------------------------------

def dna_home():
    h = os.environ.get("DNA_HOME") or os.path.join(os.path.dirname(REPO), "DotNetAnywhere")
    return h if os.path.exists(os.path.join(h, "build.py")) else None


def build_dir():
    return os.environ.get("UNITY_PACK_DNA_BUILD") or os.path.join(REPO, "build", "dna")


def ccs_dll():
    """CC#'s compiler (CCSharp, built: `python3 build.py compiler`), which compiles the managed side with Roslyn; None when it or `dotnet` is absent.
    $CCS_DLL names it, $CCS_HOME the checkout; otherwise a CCSharp checkout beside this repository."""
    if shutil.which("dotnet") is None:
        return None
    cands = []
    if os.environ.get("CCS_DLL"):
        cands.append(os.environ["CCS_DLL"])
    home = os.environ.get("CCS_HOME") or os.path.join(os.path.dirname(REPO), "CCSharp")
    cands.append(os.path.join(home, "build", "compiler", "ccs.dll"))
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def compiler_name():
    """'roslyn' (CC#'s compiler) or 'mcs' (mono): what compiles the managed C#.  Roslyn when it is there; $UNITY_PACK_MANAGED_COMPILER forces one."""
    want = os.environ.get("UNITY_PACK_MANAGED_COMPILER", "").strip().lower()
    if want == "mcs":
        return "mcs" if shutil.which("mcs") else None
    if want == "roslyn":
        return "roslyn" if ccs_dll() else None
    if ccs_dll():
        return "roslyn"
    return "mcs" if shutil.which("mcs") else None


def available():
    """(True, '') or (False, why): DotNetAnywhere beside this repository (or $DNA_HOME) and a compiler for the managed C#
    (CC#'s Roslyn one beside this repository, or mono's mcs)."""
    if compiler_name() is None:
        return False, "no managed C# compiler: build CCSharp beside this repository (python3 build.py compiler; needs dotnet), or install mono-mcs"
    if dna_home() is None:
        return False, "DotNetAnywhere not found: clone it beside this repository, or set DNA_HOME"
    return True, ""


def _dna_run(args):
    home, bdir = dna_home(), build_dir()
    os.makedirs(bdir, exist_ok=True)
    p = subprocess.run([sys.executable, os.path.join(home, "build.py")] + list(args) + ["--build-dir", bdir],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if p.returncode != 0:
        raise HybridError("building DotNetAnywhere failed:\n" + p.stdout.decode("utf-8", "replace")[-800:])


def prepare_corlib():
    """DotNetAnywhere's corlib.dll (the managed code is compiled against it and runs on it), built once."""
    corlib = os.path.join(build_dir(), "corlib.dll")
    if not os.path.exists(corlib):
        _dna_run(["--lib-only"])
    if not os.path.exists(corlib):
        raise HybridError("DotNetAnywhere's corlib.dll was not built (it needs mcs)")
    return corlib


def mcs(sources, out, corlib):
    p = subprocess.run(["mcs", "-nostdlib", "-unsafe", "-target:" + ("exe" if out.endswith(".exe") else "library"), "-nowarn:0169,0414,0219,0649",
                        "-r:" + corlib, "-out:" + out] + list(sources),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.returncode == 0, p.stdout.decode("utf-8", "replace")


def roslyn(sources, out, corlib):
    """The same, with CC#'s compiler: Roslyn against DotNetAnywhere's corlib (`ccs --managed-compile`), so current C# (what mcs, which stops at
    C# 7, cannot parse) compiles."""
    p = subprocess.run(["dotnet", ccs_dll(), "--managed-compile", out, corlib] + list(sources), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.returncode == 0, p.stdout.decode("utf-8", "replace")


def compile_managed(sources, out, corlib):
    """(ok, messages): compile the managed C# *sources* to *out* (a console program when it ends in .exe, else a library) against *corlib*
    with whichever compiler compiler_name() picks."""
    return (roslyn if compiler_name() == "roslyn" else mcs)(sources, out, corlib)


# ---- what the packer tells us about a method -------------------------------------------------------------------------------------------

def signature_ok(params, ret):
    """May a method with these parameters (cs2cpp.Param) and C# return type be called across the boundary?  Numbers and bools only, for now."""
    for prm in params:
        if not _carrier(prm.type) or getattr(prm, "modifier", None):
            return False
    r = (ret or "void").strip()
    return _carrier(r) or r == "void"


def accessors(engine, cname):
    """{field: (get C type, set C type or None)} for the scalar accessors engine.c has for *cname*: `static unsigned Spark_get_hp(unsigned i)`."""
    out = {}
    for m in re.finditer(r"^static (unsigned|int|float|double) %s_get_(\w+)\(unsigned i\)" % re.escape(cname), engine, re.M):
        out[m.group(2)] = [m.group(1), None]
    for m in re.finditer(r"^static void %s_set_(\w+)\(unsigned i, (unsigned|int|float|double) v\)" % re.escape(cname), engine, re.M):
        if m.group(1) in out:
            out[m.group(1)][1] = m.group(2)
    return {k: tuple(v) for k, v in out.items()}


def _words(text):
    return set(re.findall(r"[A-Za-z_]\w*", text))


# ---- the managed class of a script ----------------------------------------------------------------------------------------------------

class ClassGen(object):
    """The managed C# of one script class, and what it needs from the engine."""

    def __init__(self, plan, engine, cl, cands, siblings):
        self.name = cl["name"]
        self.cl = cl
        self.cands = cands
        self.siblings = [s for s in siblings if not s.get("stub")]
        self.acc = accessors(engine, self.name)
        self.engine = engine
        self.natives = {}          # export name -> (ret FFI type, [arg FFI types], C text of the wrapper, managed extern declaration)
        self.time = time_members(engine)          # the Time members the managed Time class gets (all the engine declares)
        self.problem = None
        self.src = self._build()

    # a managed property for each field the engine has accessors for
    def _field(self, f):
        t = f["ty"]
        a = self.acc.get(f["name"])
        if t not in FIELD_TYPES or a is None or a[1] is None:
            return None
        gt, st = a
        n = f["name"]
        get_ex, set_ex = "ccs_x_%s_get_%s" % (self.name, n), "ccs_x_%s_set_%s" % (self.name, n)
        self.natives[get_ex] = (ACCESSOR_FFI[gt], ["uint"], "%s ccs_x_%s_get_%s(unsigned i) { return %s_get_%s(i); }" % (gt, self.name, n, self.name, n))
        self.natives[set_ex] = ("void", ["uint", ACCESSOR_FFI[st]], "void ccs_x_%s_set_%s(unsigned i, %s v) { %s_set_%s(i, v); }" % (self.name, n, st, self.name, n))
        if t == "float":
            return "    public float %s { get { return HybridNative.%s(__i); } set { HybridNative.%s(__i, value); } }" % (n, get_ex, set_ex)
        if t == "bool":
            return "    public bool %s { get { return HybridNative.%s(__i) != 0; } set { HybridNative.%s(__i, value ? %s : %s); } }" % (
                n, get_ex, set_ex, "1" if ACCESSOR_FFI[st] != "uint" else "1u", "0" if ACCESSOR_FFI[st] != "uint" else "0u")
        mt = SCALAR[t][0]
        sc = "(%s)" % ("uint" if ACCESSOR_FFI[st] == "uint" else "int" if ACCESSOR_FFI[st] == "int" else "float")
        return "    public %s %s { get { return (%s)HybridNative.%s(__i); } set { HybridNative.%s(__i, %svalue); } }" % (mt, n, mt, get_ex, set_ex, sc)

    def _rotation_storage(self):
        """Does the engine keep a rotation for this class (`_Class_rot_x/y/z/w[i]` and the basis `_Class_rot_m00..m11[i]`), and the helper that sets
        the quaternion and the basis together?  Then managed code can read and write transform.rotation through them."""
        e, n = self.engine, re.escape(self.name)
        return (all(re.search(r"^extern float _%s_rot_%s\[" % (n, a), e, re.M) for a in ("x", "y", "z", "w", "m00", "m01", "m10", "m11"))
                and re.search(r"^static void _engine_transform_set_quat\(", e, re.M) is not None)

    def _transform(self):
        """position through the SoA position accessors, when the class has them.  A 2D class has no pos_z: z reads 0 and a write of it is dropped.
        rotation through the engine's rotation arrays, when the class has them (_rotation_storage)."""
        def has(n):
            return n in self.acc and self.acc[n][0] == "float" and self.acc[n][1] == "float"
        if not (has("pos_x") and has("pos_y")):
            return None
        axes = ("pos_x", "pos_y") + (("pos_z",) if has("pos_z") else ())
        for n in axes:
            self.natives["ccs_x_%s_get_%s" % (self.name, n)] = ("float", ["uint"], "float ccs_x_%s_get_%s(unsigned i) { return %s_get_%s(i); }" % (self.name, n, self.name, n))
            self.natives["ccs_x_%s_set_%s" % (self.name, n)] = ("void", ["uint", "float"], "void ccs_x_%s_set_%s(unsigned i, float v) { %s_set_%s(i, v); }" % (self.name, n, self.name, n))
        g = lambda n: "HybridNative.ccs_x_%s_get_%s(i)" % (self.name, n)
        s = lambda n, v: "HybridNative.ccs_x_%s_set_%s(i, %s);" % (self.name, n, v)
        z_get = g("pos_z") if "pos_z" in axes else "0f"
        z_set = s("pos_z", "value.z") if "pos_z" in axes else ""
        if self._rotation_storage():
            for a in "xyzw":
                self.natives["ccs_x_%s_get_rot_%s" % (self.name, a)] = (
                    "float", ["uint"], "float ccs_x_%s_get_rot_%s(unsigned i) { return _%s_rot_%s[i]; }" % (self.name, a, self.name, a))
            self.natives["ccs_x_%s_set_rot" % self.name] = (
                "void", ["uint", "float", "float", "float", "float"],
                "void ccs_x_%s_set_rot(unsigned i, float x, float y, float z, float w) { _engine_transform_set_quat("
                "&_%s_rot_x[i], &_%s_rot_y[i], &_%s_rot_z[i], &_%s_rot_w[i], &_%s_rot_m00[i], &_%s_rot_m01[i], &_%s_rot_m10[i], &_%s_rot_m11[i], x, y, z, w); }"
                % ((self.name,) + (self.name,) * 8))
            rot = ("    public override UnityEngine.Quaternion rotation {\n        get { return new UnityEngine.Quaternion(%s, %s, %s, %s); }\n"
                   "        set { HybridNative.ccs_x_%s_set_rot(i, value.x, value.y, value.z, value.w); }\n    }\n"
                   % (g("rot_x"), g("rot_y"), g("rot_z"), g("rot_w"), self.name))
        else:
            # (_build declines a class whose managed code names rotation, so this is only reached by code that does not)
            rot = ("    public override UnityEngine.Quaternion rotation {\n"
                   "        get { throw new System.NotSupportedException(\"the packed engine keeps no rotation for %s\"); }\n"
                   "        set { throw new System.NotSupportedException(\"the packed engine keeps no rotation for %s\"); }\n    }\n" % (self.name, self.name))
        return ("public class %s_Transform : UnityEngine.Transform {\n    readonly uint i;\n    public %s_Transform(uint i) { this.i = i; }\n"
                "    public override UnityEngine.Vector3 position {\n        get { return new UnityEngine.Vector3(%s, %s, %s); }\n"
                "        set { %s %s %s }\n    }\n%s}\n") % (self.name, self.name, g("pos_x"), g("pos_y"), z_get, s("pos_x", "value.x"), s("pos_y", "value.y"), z_set, rot)

    def _ret2(self):
        """the natives of the two-float result slot a Vector2 result travels through"""
        self.natives["ccs_x_ret2_set"] = ("void", ["float", "float"], "void ccs_x_ret2_set(float x, float y) { ccs_ret2[0] = x; ccs_ret2[1] = y; }")
        self.natives["ccs_x_ret2_x"] = ("float", [], "float ccs_x_ret2_x(void) { return ccs_ret2[0]; }")
        self.natives["ccs_x_ret2_y"] = ("float", [], "float ccs_x_ret2_y(void) { return ccs_ret2[1]; }")

    def _build(self):
        cl, name = self.cl, self.name
        bodies = "\n".join(c["body"] for c in self.cands)
        used = _words(bodies)
        props, unreachable = [], []
        for f in cl.get("fields") or []:
            if f.get("static") or f.get("const"):
                continue
            line = self._field(f)
            if line is not None:
                props.append(line)
            elif f["name"] in used:
                unreachable.append("%s %s" % (f["ty"], f["name"]))
        if unreachable:
            self.problem = ("the method uses %s, which the packed engine stores in a form managed code cannot reach yet"
                            % ", ".join("`%s`" % u for u in unreachable))
            return ""
        transform = self._transform()
        if transform is None and "transform" in used:
            # (it would build, and then fail at run time on a null transform: say so now, and keep the lowered C)
            self.problem = "the method uses `transform`, and the packed engine keeps no position accessors for this class"
            return ""
        if ROTATION_WORDS & used or re.search(r"\btransform\s*\.\s*(?:right|up|forward)\b", bodies):
            if not self._rotation_storage():
                # (it would build, and then fail at run time with no rotation to read: say so now, and keep the lowered C)
                self.problem = "the method uses the transform's rotation, which the packed engine keeps no storage for in this class"
                return ""
        lines = ["public class %s : UnityEngine.MonoBehaviour\n{" % name]
        lines.append("    static %s[] __objs = new %s[8];" % (name, name))
        # (an int index: DotNetAnywhere's array opcodes take a 32-bit index, and `a[uint]` is a conv.u it does not narrow)
        lines.append("    public static %s __Get(uint i)\n    {\n        int n = (int)i;\n        if (n >= __objs.Length) {\n"
                     "            %s[] bigger = new %s[n * 2 + 8];\n            for (int k = 0; k < __objs.Length; k++) bigger[k] = __objs[k];\n            __objs = bigger;\n        }\n"
                     "        %s o = __objs[n];\n        if (o == null) {\n            o = new %s();\n            o.__i = i;\n%s            __objs[n] = o;\n        }\n        return o;\n    }"
                     % (name, name, name, name, name, ("            o.transform = new %s_Transform(i);\n" % name) if transform else ""))
        lines += props
        # the lowered methods of the class, for the managed ones to call (only those a managed body names)
        managed = {c["sym"] for c in self.cands}
        for s in self.siblings:
            if s["sym"] in managed or s["name"] not in used or not s["scalar"]:
                continue
            ex = "ccs_x_%s" % s["sym"]
            st = bool(s.get("static"))
            ps = s["params"]
            cps, pre, cargs = [], [], []
            for k, (t_, _n) in enumerate(ps):
                if t_ == VEC2:
                    cps.append("float a%dx, float a%dy" % (k, k))
                    pre.append("%s a%d = {a%dx, a%dy};" % (VEC2, k, k, k))
                else:
                    cps.append("%s a%d" % (SCALAR[t_][3], k))
                cargs.append("a%d" % k)
            ffi = ([] if st else ["uint"]) + _ffi_args(ps)
            vret = s["ret"] == VEC2
            rc = "void" if vret else (SCALAR[s["ret"]][3] if s["ret"] in SCALAR else "void")
            call = "%s(%s)" % (s["sym"], ", ".join(([] if st else ["i"]) + cargs))
            head = "%s %s(%s)" % (rc, ex, ", ".join(([] if st else ["unsigned i"]) + cps) or "void")
            if vret:
                stmt = "%s r = %s; ccs_ret2[0] = r.x; ccs_ret2[1] = r.y;" % (VEC2, call)
                self._ret2()
            else:
                stmt = "%s%s;" % ("" if rc == "void" else "return ", call)
            self.natives[ex] = ("void" if rc == "void" else SCALAR[s["ret"]][1], ffi, "%s { %s%s }" % (head, " ".join(pre) + " " if pre else "", stmt))
            mparams = ", ".join("%s %s" % (_mtype(t_), n) for t_, n in ps)
            margs = ", ".join(([] if st else ["__i"]) + [("%s.x, %s.y" % (n, n)) if t_ == VEC2 else n for t_, n in ps])
            mret = VEC2 if vret else (SCALAR[s["ret"]][0] if s["ret"] in SCALAR else "void")
            if vret:
                body = ("HybridNative.%s(%s); return new %s(HybridNative.ccs_x_ret2_x(), HybridNative.ccs_x_ret2_y());" % (ex, margs, VEC2))
            else:
                body = "%sHybridNative.%s(%s);" % ("" if mret == "void" else "return ", ex, margs)
            lines.append("    public %s%s %s(%s) { %s }" % ("static " if st else "", mret, s["name"], mparams, body))
        for c in self.cands:
            st = bool(c.get("static"))
            mparams = ", ".join("%s %s" % (_mtype(t_), n) for t_, n in c["params"])
            ret = (c["ret"] or "void").strip()
            mret = _mtype(ret) if _carrier(ret) else "void"
            lines.append("    public %s%s %s(%s)\n    {\n%s\n    }" % ("static " if st else "", mret, c["name"], mparams, c["body"]))
            # the entry native code calls: named by the C symbol, which tells overloads apart (a Vector2 comes in as its two floats, goes out in ccs_ret2)
            eps = ([] if st else ["uint i"])
            for t_, n in c["params"]:
                eps += ["float %s_x, float %s_y" % (n, n)] if t_ == VEC2 else ["%s %s" % (SCALAR[t_][0], n)]
            eargs = ", ".join(("new %s(%s_x, %s_y)" % (VEC2, n, n)) if t_ == VEC2 else n for t_, n in c["params"])
            target = "%s(%s)" % (c["name"], eargs) if st else "__Get(i).%s(%s)" % (c["name"], eargs)
            if ret == VEC2:
                self._ret2()
                lines.append("    public static void __%s(%s) { %s r = %s; HybridNative.ccs_x_ret2_set(r.x, r.y); }" % (c["sym"], ", ".join(eps), VEC2, target))
            else:
                lines.append("    public static %s __%s(%s) { %s%s; }" % (mret, c["sym"], ", ".join(eps), "" if mret == "void" else "return ", target))
        lines.append("}\n")
        return (transform or "") + "\n".join(lines)


def _native_decls(natives):
    out = ["public static class HybridNative\n{"]
    for ex in sorted(natives):
        ret, args, _c = natives[ex]
        mret = {"uint": "uint", "int": "int", "float": "float", "double": "double", "long": "long", "short": "short", "ushort": "ushort",
                "sbyte": "sbyte", "byte": "byte", "void": "void"}[ret]
        params = ", ".join("%s a%d" % (a, k) for k, a in enumerate(args))
        out.append("    [System.Runtime.InteropServices.DllImport(\"ccs_native\")] public static extern %s %s(%s);" % (mret, ex, params))
    out.append("}\n")
    return "\n".join(out)


# UnityEngine.Time members that are floats the engine keeps as `extern float Time_<name>;` (each only when the engine has it)
TIME_FIELDS = ("deltaTime", "unscaledDeltaTime", "timeScale", "time", "fixedDeltaTime")


def time_members(engine):
    """The Time members (TIME_FIELDS) this engine.c declares, for the managed Time class to read."""
    return [n for n in TIME_FIELDS if re.search(r"^extern float Time_%s;" % n, engine, re.M)]


def _time_class(natives, names):
    props = []
    for n in names:
        natives["ccs_x_Time_%s" % n] = ("float", [], "float ccs_x_Time_%s(void) { return Time_%s; }" % (n, n))
        props.append("        public static float %s { get { return HybridNative.ccs_x_Time_%s(); } }" % (n, n))
    return "namespace UnityEngine\n{\n    public static class Time\n    {\n" + "\n".join(props) + "\n    }\n}\n"


def managed_source(gens, time_names):
    natives = {}
    parts = ["using System;\nusing UnityEngine;\n"]
    for g in gens:
        natives.update(g.natives)
    body = []
    if time_names:
        body.append(_time_class(natives, time_names))
    body += [g.src for g in gens]
    parts.append(_native_decls(natives))
    parts += body
    return "\n".join(parts), natives


# ---- native side ---------------------------------------------------------------------------------------------------------------------------

def _kind_conv(t):
    return SCALAR[t]


def glue_c(cands_by_class, ok_names):
    """hybrid_glue.c: native -> managed.  One function for each managed method, with the C signature the engine's stub has."""
    out = ["/* unity_pack --hybrid: the C side of the native/managed bridge.  Generated. */",
           "#include <stdio.h>", "#include <stdlib.h>", "#include <string.h>", "#include <unistd.h>", '#include "Host.h"', "",
           "static DNA_Assembly *hy_asm;", "", "typedef struct Vector2 { float x; float y; } Vector2;", "float ccs_ret2[2];", "",
           "static const char *hy_dll_path(char *buf, size_t n) {",
           "\tconst char *e = getenv(\"UNITY_PACK_MANAGED_DLL\");", "\tssize_t k;", "\tif (e != NULL && *e) return e;",
           "\tk = readlink(\"/proc/self/exe\", buf, n - 1);", "\tif (k > 0) {", "\t\tchar *s;", "\t\tbuf[k] = 0;", "\t\ts = strrchr(buf, '/');",
           "\t\tif (s != NULL) {", "\t\t\tsnprintf(s + 1, n - (size_t)(s + 1 - buf), \"%s\", \"" + ASSEMBLY + "\");",
           "\t\t\tif (access(buf, R_OK) == 0) return buf;", "\t\t}", "\t}", "\treturn \"" + ASSEMBLY + "\";", "}", "",
           "static void hy_ensure(void) {", "\tchar buf[4096];", "\tconst char *path;", "\tif (hy_asm != NULL) return;", "\tpath = hy_dll_path(buf, sizeof buf);",
           "\tif (access(path, R_OK) != 0) { fprintf(stderr, \"unity_pack: cannot load the managed assembly %s (it belongs beside the player; "
           "UNITY_PACK_MANAGED_DLL names another)\\n\", path); exit(70); }",
           "\tDNA_SetCrashMode(1);", "\tDNA_SetAssemblyDirFromFile(path);", "\tDNA_Init();", "\thy_asm = DNA_Load(path);",
           "\tif (hy_asm == NULL) { fprintf(stderr, \"unity_pack: cannot load the managed assembly: %s\\n\", DNA_Error()); exit(70); }", "}", "",
           "static DNA_Method *hy_find(const char *cls, const char *name, const char *sig) {", "\tDNA_Method *m;", "\thy_ensure();",
           "\tm = DNA_Find(hy_asm, \"\", cls, name, sig);",
           "\tif (m == NULL) { fprintf(stderr, \"unity_pack: %s\\n\", DNA_Error()); exit(70); }", "\treturn m;", "}", ""]
    for cname in sorted(ok_names):
        for c in cands_by_class[cname]:
            ret = (c["ret"] or "void").strip()
            st = bool(c.get("static"))
            lead = 0 if st else 1
            n = len(c["params"]) + lead
            vret = ret == VEC2
            rc = VEC2 if vret else (SCALAR[ret][3] if ret in SCALAR else "void")
            n = lead + sum(2 if t_ == VEC2 else 1 for t_, _n in c["params"])
            sig = ("" if st else "i") + _dna_letters(c["params"]) + ">" + ("v" if vret else (SCALAR[ret][2] if ret in SCALAR else "v"))
            out.append("%s ccs_b_%s(%s) {" % (rc, c["sym"], _cparams(c)))
            out.append("\tstatic DNA_Method *m;")
            out.append("\tDNA_Value a[%d], r;" % max(n, 1))
            out.append("\tif (m == NULL) m = hy_find(\"%s\", \"__%s\", \"%s\");" % (cname, c["sym"], sig))
            if not st:
                out.append("\ta[0] = DNA_Int(i);")
            slot = lead
            for k, (t, _n) in enumerate(c["params"]):
                if t == VEC2:
                    out.append("\ta[%d] = DNA_Float(a%d.x);" % (slot, k))
                    out.append("\ta[%d] = DNA_Float(a%d.y);" % (slot + 1, k))
                    slot += 2
                else:
                    out.append("\ta[%d] = %s(a%d);" % (slot, SCALAR[t][4], k))
                    slot += 1
            out.append("\tif (DNA_Call(m, a, %d, &r) != 0) { fprintf(stderr, \"unity_pack: %%s\\n\", DNA_Error()); exit(70); }" % n)
            if vret:
                out.append("\t{ %s v; v.x = ccs_ret2[0]; v.y = ccs_ret2[1]; return v; }" % VEC2)
            elif ret in SCALAR:
                field = {"i": "i", "l": "l", "f": "f", "d": "d"}[SCALAR[ret][2]]
                out.append("\treturn (%s)r.u.%s;" % (rc, field))
            else:
                out.append("\t(void)r;")
            out.append("}")
            out.append("")
    return "\n".join(out)


def _cparams(c):
    """the C parameter list of a managed method's engine-side function: `unsigned i, int a0, ..`, or without `i` for a static (`void` when empty)"""
    ps = ([] if c.get("static") else ["unsigned i"]) + ["%s a%d" % (_ctype(t), k) for k, (t, _n) in enumerate(c["params"])]
    return ", ".join(ps) if ps else "void"


def _proto(c):
    ret = (c["ret"] or "void").strip()
    rc = _ctype(ret) if _carrier(ret) else "void"
    return "%s ccs_b_%s(%s);" % (rc, c["sym"], _cparams(c))


def ffi_manifest(natives):
    return {"c_files": [], "cflags": [], "functions": [
        {"library": "ccs_native", "entry": ex, "ret": natives[ex][0], "args": list(natives[ex][1])} for ex in sorted(natives)]}


# ---- the step in pack() --------------------------------------------------------------------------------------------------------------------

def apply(plan, engine, outdir, report_stub, progress=lambda m: None):
    """Replace the stubs that managed code can stand in for.  Returns the engine text.

    *plan["_hybrid_deferred"]* holds the stubs emit_engine did not report; the ones this does not resolve are reported here, as they would have been."""
    deferred = plan.pop("_hybrid_deferred", []) or []
    cands_by_class = plan.pop("_hybrid_cands", {}) or {}
    methods = plan.pop("_hybrid_methods", {}) or {}
    notes = {}

    # --managed: a selected class runs as managed C# whole.  Its methods the packer lowered fine (and the boundary can carry) join the ones it
    # could not lower, as candidates; they are swapped for the managed call only once the managed assembly has built, so a class that cannot
    # be built managed is exactly what it was without --managed.
    selected = plan.get("managed") or set()
    forced = set()
    if selected:
        for cname, sibs in methods.items():
            if selected != "*" and cname not in selected:
                continue
            fs = [dict(s, forced=True) for s in sibs if s.get("scalar") and not s.get("stub")]
            if fs:
                cands_by_class.setdefault(cname, []).extend(fs)
                forced.add(cname)
        if selected != "*":
            for cname in sorted(selected - set(methods) - set(cands_by_class)):
                progress("managed: no script class %s with a method to run managed" % cname)
    declined = plan.setdefault("managed_declined", {})

    def finish(resolved):
        for cname in sorted(forced):
            if not any(r[0] == cname for r in resolved):
                declined[cname] = notes.get(cname) or "no managed method could be built"
                progress("managed: %s stays lowered: %s" % (cname, declined[cname]))
        for site, cl, m, why in deferred:
            if (cl["name"], m["name"]) in resolved:
                plan.setdefault("hybrid_methods", []).append({"class": cl["name"], "method": m["name"]})
                continue
            extra = notes.get(cl["name"])
            report_stub(plan, site, cl, m, (why[0] + ("; hybrid: " + extra if extra else ""), why[1]))
        return engine

    if not cands_by_class:
        return finish(set())
    ok, why_not = available()
    if not ok:
        for c in cands_by_class:
            notes[c] = why_not
        return finish(set())
    try:
        corlib = prepare_corlib()
    except HybridError as e:
        for c in cands_by_class:
            notes[c] = str(e).splitlines()[0]
        return finish(set())

    os.makedirs(outdir, exist_ok=True)
    work = os.path.join(outdir, "hybrid_work")
    os.makedirs(work, exist_ok=True)
    gens = {}
    for cname, cands in sorted(cands_by_class.items()):
        cl = next(c for c in plan["classes"] if c["name"] == cname) if isinstance(plan["classes"], list) else plan["classes"][cname]
        gens[cname] = ClassGen(plan, engine, cl, cands, methods.get(cname, []))
    good = []
    for cname, g in sorted(gens.items()):
        if g.problem:
            notes[cname] = g.problem
            continue
        text, _n = managed_source([g], g.time)
        src = os.path.join(work, "check_%s.cs" % cname)
        with open(src, "w") as f:
            f.write(text)
        ok, msg = compile_managed([SHIM, src], os.path.join(work, "check_%s.dll" % cname), corlib)
        if ok:
            good.append(cname)
        else:
            errs = [l for l in msg.splitlines() if "error" in l]
            notes[cname] = "the managed code does not compile: " + (re.sub(r"^.*?\):\s*", "", errs[0]) if errs else msg.strip()[:160])
    if not good:
        shutil.rmtree(work, ignore_errors=True)
        return finish(set())

    text, natives = managed_source([gens[c] for c in good], time_members(engine))
    gen_cs = os.path.join(outdir, "hybrid_generated.cs")
    with open(gen_cs, "w") as f:
        f.write(text)
    ok, msg = compile_managed([SHIM, gen_cs], os.path.join(outdir, ASSEMBLY), corlib)
    if not ok:                                   # (each class compiled alone: this is the union; it should too)
        for c in good:
            notes[c] = "the managed code does not compile together: " + msg.strip()[:160]
        return finish(set())
    shutil.rmtree(work, ignore_errors=True)

    # the engine: calls instead of the markers, prototypes, and the wrappers managed code calls
    resolved = set()
    for cname in good:
        for c in cands_by_class[cname]:
            marker = "/* unity_pack:hybrid %s */" % c["sym"]
            args = ", ".join(([] if c.get("static") else ["i"]) + [n for _t, n in c["params"]])
            ret = (c["ret"] or "void").strip()
            call = ("return " if _carrier(ret) else "") + "ccs_b_%s(%s);" % (c["sym"], args)
            if c.get("forced"):
                swapped = _swap_body(engine, c["sym"], call)
                if swapped is None:
                    raise HybridError("internal: the function %s of %s.%s is not in engine.c" % (c["sym"], cname, c["name"]))
                engine = swapped
                plan.setdefault("managed_methods", []).append({"class": cname, "method": c["name"]})
            elif marker not in engine:
                raise HybridError("internal: the marker of %s.%s is not in engine.c" % (cname, c["name"]))
            else:
                engine = engine.replace(marker, call, 1)
            resolved.add((cname, c["name"]))
    protos = "".join(_proto(c) + "\n" for cname in good for c in cands_by_class[cname])
    anchor = "#include <stdint.h>\n"
    if "Vector2" in protos and "} Vector2;\n" in engine:
        anchor = "} Vector2;\n"             # (the prototypes name the type)
    k = engine.index(anchor) + len(anchor)
    engine = engine[:k] + "/* unity_pack --hybrid: managed methods, defined in hybrid_glue.c */\n" + (
        "extern float ccs_ret2[2];\n" if any("ccs_ret2" in natives[ex][2] for ex in natives) or "Vector2 ccs_b_" in protos else "") + protos + engine[k:]
    wrappers = "\n/* unity_pack --hybrid: what managed code may call (hybrid.ffi.json) */\n" + "\n".join(natives[ex][2] for ex in sorted(natives)) + "\n"
    engine = engine.rstrip("\n") + "\n" + wrappers
    with open(os.path.join(outdir, "hybrid_glue.c"), "w") as f:
        f.write(glue_c(cands_by_class, good))
    with open(os.path.join(outdir, "hybrid.ffi.json"), "w") as f:
        json.dump(ffi_manifest(natives), f, indent=1)
    counts = {}
    for cname in good:
        for c in cands_by_class[cname]:
            counts[(cname, c["name"])] = counts.get((cname, c["name"]), 0) + 1
    progress("hybrid: %d managed method(s) in %d class(es): %s" % (
        sum(counts.values()), len(good), ", ".join("%s.%s%s" % (k[0], k[1], " x%d" % n if n > 1 else "") for k, n in sorted(counts.items()))))
    return finish(resolved)


def _swap_body(engine, sym, call):
    """engine with the body of the lowered function *sym* replaced by *call* (the C that calls its managed twin); None when it is not there.
    The packer writes a function as `static T sym(...) {` .. a line that is only `}`, its body indented."""
    m = re.search(r"^static [^\n]*?\b%s\([^\n]*\) \{\n" % re.escape(sym), engine, re.M)
    if m is None:
        return None
    end = engine.find("\n}\n", m.end() - 1)
    if end < 0:
        return None
    return engine[:m.end()] + "    " + call + engine[end:]


def clean(outdir):
    """No hybrid pieces are left behind by a pack that does not use them."""
    for name in ("hybrid_generated.cs", ASSEMBLY, "hybrid_glue.c", "hybrid.ffi.json", "hybrid_glue.o", "corlib.dll"):
        p = os.path.join(outdir, name)
        if os.path.exists(p):
            os.remove(p)


def link_inputs(outdir, cc, run, progress):
    """For build_player_executable: (objects, libraries) that put DotNetAnywhere in the player, and the managed assembly beside it."""
    glue = os.path.join(outdir, "hybrid_glue.c")
    if not os.path.isfile(glue):
        return [], []
    home, bdir = dna_home(), build_dir()
    manifest = os.path.join(outdir, "hybrid.ffi.json")
    corlib = prepare_corlib()
    progress("building DotNetAnywhere with the managed-to-native functions")
    _dna_run(["--ffi", manifest, "--lib-only", "--no-corlib"])
    obj = os.path.join(outdir, "hybrid_glue.o")
    run([cc, "-O2", "-I", os.path.join(home, "native", "src"), "-c", "-o", obj, glue])
    shutil.copy(corlib, os.path.join(outdir, "corlib.dll"))
    return [obj], [os.path.join(bdir, "libdna_ffi.a"), "-lpthread"]
