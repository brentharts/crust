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
# what an engine accessor's C type is, in the manifest
ACCESSOR_FFI = {"unsigned": "uint", "int": "int", "float": "float", "double": "double"}
# the fields managed code can have as properties: C# type -> (getter cast, setter cast)
FIELD_TYPES = {"int", "uint", "short", "ushort", "sbyte", "byte", "bool", "float"}


class HybridError(Exception):
    pass


# ---- the toolchain ---------------------------------------------------------------------------------------------------------------------

def dna_home():
    h = os.environ.get("DNA_HOME") or os.path.join(os.path.dirname(REPO), "DotNetAnywhere")
    return h if os.path.exists(os.path.join(h, "build.py")) else None


def build_dir():
    return os.environ.get("UNITY_PACK_DNA_BUILD") or os.path.join(REPO, "build", "dna")


def available():
    """(True, '') or (False, why): DotNetAnywhere beside this repository (or $DNA_HOME) and mono's mcs."""
    if shutil.which("mcs") is None:
        return False, "mcs (mono-mcs) is not installed"
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
    p = subprocess.run(["mcs", "-nostdlib", "-unsafe", "-target:library", "-nowarn:0169,0414,0219,0649", "-r:" + corlib, "-out:" + out] + list(sources),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.returncode == 0, p.stdout.decode("utf-8", "replace")


# ---- what the packer tells us about a method -------------------------------------------------------------------------------------------

def signature_ok(params, ret):
    """May a method with these parameters (cs2cpp.Param) and C# return type be called across the boundary?  Numbers and bools only, for now."""
    for prm in params:
        if prm.type not in SCALAR or getattr(prm, "modifier", None):
            return False
    return (ret or "void").strip() in SCALAR or (ret or "void").strip() == "void"


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
        self.natives = {}          # export name -> (ret FFI type, [arg FFI types], C text of the wrapper, managed extern declaration)
        self.time = "Time_deltaTime" in engine and re.search(r"^extern float Time_deltaTime;", engine, re.M) is not None
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

    def _transform(self):
        """position through the SoA position accessors, when the class has them"""
        need = ("pos_x", "pos_y", "pos_z")
        if not all(n in self.acc and self.acc[n][0] == "float" and self.acc[n][1] == "float" for n in need):
            return None
        for n in need:
            self.natives["ccs_x_%s_get_%s" % (self.name, n)] = ("float", ["uint"], "float ccs_x_%s_get_%s(unsigned i) { return %s_get_%s(i); }" % (self.name, n, self.name, n))
            self.natives["ccs_x_%s_set_%s" % (self.name, n)] = ("void", ["uint", "float"], "void ccs_x_%s_set_%s(unsigned i, float v) { %s_set_%s(i, v); }" % (self.name, n, self.name, n))
        g = lambda n: "HybridNative.ccs_x_%s_get_%s(i)" % (self.name, n)
        s = lambda n, v: "HybridNative.ccs_x_%s_set_%s(i, %s);" % (self.name, n, v)
        return ("public class %s_Transform : UnityEngine.Transform {\n    readonly uint i;\n    public %s_Transform(uint i) { this.i = i; }\n"
                "    public override UnityEngine.Vector3 position {\n        get { return new UnityEngine.Vector3(%s, %s, %s); }\n"
                "        set { %s %s %s }\n    }\n}\n") % (self.name, self.name, g("pos_x"), g("pos_y"), g("pos_z"), s("pos_x", "value.x"), s("pos_y", "value.y"), s("pos_z", "value.z"))

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
        lines = ["public class %s : UnityEngine.MonoBehaviour\n{" % name]
        lines.append("    static %s[] __objs = new %s[8];" % (name, name))
        # (an int index: DotNetAnywhere's array opcodes take a 32-bit index, and `a[uint]` is a conv.u it does not narrow)
        lines.append("    public static %s __Get(uint i)\n    {\n        int n = (int)i;\n        if (n >= __objs.Length) {\n"
                     "            %s[] bigger = new %s[n * 2 + 8];\n            for (int k = 0; k < __objs.Length; k++) bigger[k] = __objs[k];\n            __objs = bigger;\n        }\n"
                     "        %s o = __objs[n];\n        if (o == null) {\n            o = new %s();\n            o.__i = i;\n%s            __objs[n] = o;\n        }\n        return o;\n    }"
                     % (name, name, name, name, name, ("            o.transform = new %s_Transform(i);\n" % name) if transform else ""))
        lines += props
        # the lowered methods of the class, for the managed ones to call (only those a managed body names)
        managed = {c["name"] for c in self.cands}
        for s in self.siblings:
            if s["name"] in managed or s["name"] not in used or not s["scalar"]:
                continue
            ex = "ccs_x_%s" % s["sym"]
            cparams = "".join(", %s a%d" % (SCALAR[t][3], k) for k, (t, _n) in enumerate(s["params"]))
            ffi = ["uint"] + [SCALAR[t][1] for t, _n in s["params"]]
            rc = SCALAR[s["ret"]][3] if s["ret"] in SCALAR else "void"
            call = "%s(i%s)" % (s["sym"], "".join(", a%d" % k for k in range(len(s["params"]))))
            self.natives[ex] = ("void" if rc == "void" else SCALAR[s["ret"]][1], ffi,
                                "%s %s(unsigned i%s) { %s%s; }" % (rc, ex, cparams, "" if rc == "void" else "return ", call))
            mparams = ", ".join("%s %s" % (SCALAR[t][0], n) for t, n in s["params"])
            margs = "".join(", " + n for _t, n in s["params"])
            mret = SCALAR[s["ret"]][0] if s["ret"] in SCALAR else "void"
            conv = ""
            lines.append("    public %s %s(%s) { %sHybridNative.%s(__i%s); }" % (mret, s["name"], mparams, "" if mret == "void" else "return ", ex, margs))
        for c in self.cands:
            mparams = ", ".join("%s %s" % (SCALAR[t][0], n) for t, n in c["params"])
            ret = (c["ret"] or "void").strip()
            mret = SCALAR[ret][0] if ret in SCALAR else "void"
            lines.append("    public %s %s(%s)\n    {\n%s\n    }" % (mret, c["name"], mparams, c["body"]))
            eparams = "".join(", %s %s" % (SCALAR[t][0], n) for t, n in c["params"])
            eargs = "".join(", " + n for _t, n in c["params"])
            lines.append("    public static %s __%s(uint i%s) { %s__Get(i).%s(%s); }" % (
                mret, c["name"], eparams, "" if mret == "void" else "return ", c["name"], ", ".join(n for _t, n in c["params"])))
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


def _time_class(natives):
    natives["ccs_x_Time_deltaTime"] = ("float", [], "float ccs_x_Time_deltaTime(void) { return Time_deltaTime; }")
    return ("namespace UnityEngine\n{\n    public static class Time\n    {\n"
            "        public static float deltaTime { get { return HybridNative.ccs_x_Time_deltaTime(); } }\n    }\n}\n")


def managed_source(gens, with_time):
    natives = {}
    parts = ["using System;\nusing UnityEngine;\n"]
    for g in gens:
        natives.update(g.natives)
    body = []
    if with_time:
        body.append(_time_class(natives))
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
           "static DNA_Assembly *hy_asm;", "",
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
            n = len(c["params"])
            rc = SCALAR[ret][3] if ret in SCALAR else "void"
            sig = "i" + "".join(SCALAR[t][2] for t, _n in c["params"]) + ">" + (SCALAR[ret][2] if ret in SCALAR else "v")
            cparams = "unsigned i" + "".join(", %s a%d" % (SCALAR[t][3], k) for k, (t, _n) in enumerate(c["params"]))
            out.append("%s ccs_b_%s(%s) {" % (rc, c["sym"], cparams))
            out.append("\tstatic DNA_Method *m;")
            out.append("\tDNA_Value a[%d], r;" % (n + 1))
            out.append("\tif (m == NULL) m = hy_find(\"%s\", \"__%s\", \"%s\");" % (cname, c["name"], sig))
            out.append("\ta[0] = DNA_Int(i);")
            for k, (t, _n) in enumerate(c["params"]):
                out.append("\ta[%d] = %s(a%d);" % (k + 1, SCALAR[t][4], k))
            out.append("\tif (DNA_Call(m, a, %d, &r) != 0) { fprintf(stderr, \"unity_pack: %%s\\n\", DNA_Error()); exit(70); }" % (n + 1))
            if ret in SCALAR:
                field = {"i": "i", "l": "l", "f": "f", "d": "d"}[SCALAR[ret][2]]
                out.append("\treturn (%s)r.u.%s;" % (rc, field))
            else:
                out.append("\t(void)r;")
            out.append("}")
            out.append("")
    return "\n".join(out)


def _proto(c):
    ret = (c["ret"] or "void").strip()
    rc = SCALAR[ret][3] if ret in SCALAR else "void"
    cparams = "unsigned i" + "".join(", %s a%d" % (SCALAR[t][3], k) for k, (t, _n) in enumerate(c["params"]))
    return "%s ccs_b_%s(%s);" % (rc, c["sym"], cparams)


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

    def finish(resolved):
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
    with_time = any(g.time for g in gens.values())
    good = []
    for cname, g in sorted(gens.items()):
        if g.problem:
            notes[cname] = g.problem
            continue
        text, _n = managed_source([g], with_time and g.time)
        src = os.path.join(work, "check_%s.cs" % cname)
        with open(src, "w") as f:
            f.write(text)
        ok, msg = mcs([SHIM, src], os.path.join(work, "check_%s.dll" % cname), corlib)
        if ok:
            good.append(cname)
        else:
            errs = [l for l in msg.splitlines() if "error" in l]
            notes[cname] = "the managed code does not compile: " + (re.sub(r"^.*?\):\s*", "", errs[0]) if errs else msg.strip()[:160])
    if not good:
        shutil.rmtree(work, ignore_errors=True)
        return finish(set())

    text, natives = managed_source([gens[c] for c in good], any(gens[c].time for c in good))
    gen_cs = os.path.join(outdir, "hybrid_generated.cs")
    with open(gen_cs, "w") as f:
        f.write(text)
    ok, msg = mcs([SHIM, gen_cs], os.path.join(outdir, ASSEMBLY), corlib)
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
            args = "i" + "".join(", " + n for _t, n in c["params"])
            ret = (c["ret"] or "void").strip()
            call = ("return " if ret in SCALAR else "") + "ccs_b_%s(%s);" % (c["sym"], args)
            if marker not in engine:
                raise HybridError("internal: the marker of %s.%s is not in engine.c" % (cname, c["name"]))
            engine = engine.replace(marker, call, 1)
            resolved.add((cname, c["name"]))
    protos = "".join(_proto(c) + "\n" for cname in good for c in cands_by_class[cname])
    anchor = "#include <stdint.h>\n"
    k = engine.index(anchor) + len(anchor)
    engine = engine[:k] + "/* unity_pack --hybrid: managed methods, defined in hybrid_glue.c */\n" + protos + engine[k:]
    wrappers = "\n/* unity_pack --hybrid: what managed code may call (hybrid.ffi.json) */\n" + "\n".join(natives[ex][2] for ex in sorted(natives)) + "\n"
    engine = engine.rstrip("\n") + "\n" + wrappers
    with open(os.path.join(outdir, "hybrid_glue.c"), "w") as f:
        f.write(glue_c(cands_by_class, good))
    with open(os.path.join(outdir, "hybrid.ffi.json"), "w") as f:
        json.dump(ffi_manifest(natives), f, indent=1)
    progress("hybrid: %d managed method(s) in %d class(es): %s" % (len(resolved), len(good), ", ".join(sorted("%s.%s" % r for r in resolved))))
    return finish(resolved)


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
