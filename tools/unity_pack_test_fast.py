#!/usr/bin/env python3
"""Fast tests for unity_pack's newer fixes.

tests/test_unity_pack.py packs every project through cpprust + crust to
re-validate the emitted C, which costs seconds a pack; its full run takes
about ten minutes. Here the module patches that re-validation out (as
godot_pack_test_fast.py does): a test that needs the engine's behaviour
compiles engine.c with the host compiler and runs it -- which also fails on
anything the validation would, an undeclared helper or a type error. A pack
that holds a C# string (a coost fastring) is still lowered for real.

New fixes add their tests here. Run:

    python3 tools/unity_pack_test_fast.py            # everything
    python3 tools/unity_pack_test_fast.py TestLineRenderer
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import tools.unity_pack as unity_pack  # noqa: E402

_CC = shutil.which("gcc") or shutil.which("cc")
needs_cc = unittest.skipIf(_CC is None, "no C compiler")
# A C# string is a coost fastring: coost cloned beside this repository, or
# COOST_ROOT (as tests/test_unity_pack.py's string tests need).
needs_coost = unittest.skipUnless(
    os.environ.get("COOST_ROOT")
    or os.path.isdir(os.path.join(os.path.dirname(ROOT), "coost")),
    "coost not found: clone https://github.com/crustos/coost beside crust")

_saved_validate = []


def setUpModule():
    saved = unity_pack.validate_emitted_c
    _saved_validate.append(saved)

    def validate(text, *a, **k):
        # a C# string is a coost fastring, a List a std::vector: C++ only
        # cpprust lowers to C
        if "fastring" in text or "std::vector" in text:
            return saved(text, *a, **k)
        return None
    unity_pack.validate_emitted_c = validate


def tearDownModule():
    unity_pack.validate_emitted_c = _saved_validate.pop()


# ---- shared helpers for new tests -------------------------------------------

def project(test, scripts, scene_objects):
    """A Unity project: *scripts* {name: C# source}, and a scene of
    *scene_objects*, each (script name, extra component YAML with `{go}`
    and `{fid}` placeholders, extra MonoBehaviour field YAML, rotation
    quaternion or None). Every object gets a GameObject, a Transform and
    its script; returns the project root."""
    root = tempfile.mkdtemp(prefix="upf-")
    test.addCleanup(shutil.rmtree, root, True)
    sd = os.path.join(root, "Assets", "Scripts")
    os.makedirs(sd)
    guids = {}
    for k, (name, src) in enumerate(sorted(scripts.items())):
        guids[name] = "%032x" % (k + 1)
        with open(os.path.join(sd, name + ".cs"), "w") as f:
            f.write(src)
        with open(os.path.join(sd, name + ".cs.meta"), "w") as f:
            f.write("guid: %s\n" % guids[name])
    scene, fid = "%YAML 1.1\n", 100
    for k, obj in enumerate(scene_objects):
        name, extra, fields, rot = (tuple(obj) + (None, None, None))[:4]
        extra = (extra or "").format(go=fid, fid=fid + 3)
        comps = [fid + 1, fid + 2] + ([fid + 3] if extra else [])
        q = rot or (0, 0, 0, 1)
        scene += ("--- !u!1 &%d\nGameObject:\n  m_Name: %s%d\n  m_Component:\n"
                  % (fid, name, k)
                  + "".join("  - component: {fileID: %d}\n" % c for c in comps)
                  + "--- !u!4 &%d\nTransform:\n  m_GameObject: {fileID: %d}\n"
                    "  m_LocalRotation: {x: %r, y: %r, z: %r, w: %r}\n"
                    "  m_LocalPosition: {x: %d, y: 0, z: 0}\n"
                  % (fid + 1, fid, q[0], q[1], q[2], q[3], k)
                  + "--- !u!114 &%d\nMonoBehaviour:\n  m_GameObject: {fileID: %d}\n"
                    "  m_Script: {fileID: 11500000, guid: %s}\n%s"
                  % (fid + 2, fid, guids[name], fields or "")
                  + extra)
        fid += 10
    os.makedirs(os.path.join(root, "Assets", "Scenes"))
    with open(os.path.join(root, "Assets", "Scenes", "S.unity"), "w") as f:
        f.write(scene)
    return root


def pack(test, root, strict=True):
    """Pack quietly (strict: a stub is an error); the output directory."""
    out = tempfile.mkdtemp(prefix="upf-out-")
    test.addCleanup(shutil.rmtree, out, True)
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        unity_pack.pack(root, out, force=True, strict=strict)
    return out


def run_frames(test, out, frames=1, body="", pre=""):
    """Run the packed engine *frames* ticks at 60 fps (then *body*, C over
    engine_draw.h; *pre*: C before main) and return its stdout lines
    (Debug.Log included)."""
    src = os.path.join(out, "h.c")
    with open(src, "w") as f:
        f.write('#include <stdio.h>\n#include "engine_draw.h"\n'
                "extern float Time_deltaTime;\n" + pre +
                "int main(int c, char **v) { int f;\n"
                "  engine_apply_argv(c, v); Time_deltaTime = 1.f / 60.f;\n"
                "  for (f = 0; f < %d; f++) engine_tick();\n  %s\n  return 0; }\n"
                % (frames, body))
    exe = os.path.join(out, "h")
    r = subprocess.run([_CC, "-O1", "-w", "-I", out, "-o", exe, src,
                        os.path.join(out, "engine.c"), os.path.join(out, "data.c"),
                        "-lm"], capture_output=True, text=True)
    test.assertEqual(r.returncode, 0, r.stderr[-2000:])
    run = subprocess.run([exe, "-logFile", "-"], capture_output=True, text=True,
                         timeout=60)
    test.assertEqual(run.returncode, 0, run.stderr[-2000:])
    return run.stdout.splitlines()


def script(name, body, members=""):
    """A MonoBehaviour *name* whose first Update runs *body* once."""
    return ("using UnityEngine;\npublic class %s : MonoBehaviour {\n%s\n"
            "    int _f;\n    void Update() {\n        _f++; if (_f > 1) return;\n"
            "        %s\n    }\n}\n" % (name, members, body))


# ---- tests --------------------------------------------------------------------

class TestParticleSystems(unittest.TestCase):
    """A scene with a ParticleSystem, and the script forms that reach it.

    Every such project failed to pack -- the simulation calls `cosf` and
    `sinf`, and `<math.h>` was included by a feature list particles were not
    on -- and the static-call refusal had been switched off, so an invented
    `ParticleSystem.X(..)` emptied its method with only a warning."""

    SCENE = (
        "%%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: Spark\n  m_Component:\n"
        "  - component: {fileID: 2}\n  - component: {fileID: 3}\n"
        "  - component: {fileID: 4}\n--- !u!4 &2\nTransform:\n"
        "  m_GameObject: {fileID: 1}\n  m_LocalPosition: {x: 0, y: 0, z: 0}\n"
        "--- !u!114 &3\nMonoBehaviour:\n  m_GameObject: {fileID: 1}\n"
        "  m_Script: {fileID: 11500000, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa}\n"
        "%s--- !u!198 &4\nParticleSystem:\n  m_GameObject: {fileID: 1}\n")

    def _project(self, script, field=None):
        root = tempfile.mkdtemp(prefix="upack-ps-")
        self.addCleanup(shutil.rmtree, root, True)
        s = os.path.join(root, "Assets", "Scripts")
        os.makedirs(s)
        with open(os.path.join(s, "Spark.cs"), "w") as f:
            f.write(script)
        with open(os.path.join(s, "Spark.cs.meta"), "w") as f:
            f.write("guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
        sc = os.path.join(root, "Assets", "Scenes")
        os.makedirs(sc)
        with open(os.path.join(sc, "S.unity"), "w") as f:
            f.write(self.SCENE % (("  %s: {fileID: 4}\n" % field) if field else ""))
        return root

    def _pack(self, root):
        out = tempfile.mkdtemp(prefix="upack-ps-out-")
        self.addCleanup(shutil.rmtree, out, True)
        unity_pack.pack(root, out, force=True, strict=True)  # a stub fails
        return out

    def _script(self, members):
        return ("using UnityEngine;\npublic class Spark : MonoBehaviour {\n"
                + members + "\n}\n")

    def test_instance_forms_lower(self):
        for members, field in (
                ("    public ParticleSystem ps;\n"
                 "    public void Update() { ps.Emit(3); }", "ps"),
                # "Color Color": the name means the field, not the type
                ("    public ParticleSystem ParticleSystem;\n"
                 "    public void Update() { ParticleSystem.Emit(3); }",
                 "ParticleSystem"),
                ("    public void Update() { GetComponent<ParticleSystem>().Emit(2); }",
                 None)):
            out = self._pack(self._project(self._script(members), field))
            with open(os.path.join(out, "engine.c")) as f:
                self.assertIn("ParticleSystem_Emit(", f.read(), members)

    def test_a_unity_stub_is_still_a_warning_by_default(self):
        # Godot packs strict by default; Unity keeps its documented warning
        # until the move to cs2cpp is done (UNITY_PACK.md).
        root = self._project(self._script(
            "    public void Update() { Foo.Bar(); }"))
        out = tempfile.mkdtemp(prefix="upack-ps-out-")
        self.addCleanup(shutil.rmtree, out, True)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            unity_pack.pack(root, out, force=True)
        self.assertIn("warning CS8000", err.getvalue())

    def test_static_calls_are_refused_as_csc_would(self):
        for call, code in (("ParticleSystem.Emit(0f, 0f)", "CS0120"),
                           ("ParticleSystem.Explode()", "CS0117")):
            root = self._project(self._script(
                "    public void Update() { %s; }" % call))
            with self.assertRaises(unity_pack.PackError) as cm:
                self._pack(root)
            self.assertIn("error %s" % code, cm.exception.message)
            self.assertIn("Spark.cs(3,", cm.exception.message)

    @needs_cc
    def test_emit_stop_play_counts(self):
        root = self._project(self._script("""
    public ParticleSystem ps;
    int f;
    void Start() { ps.Stop(); ps.Clear(); }
    void Update() {
        f++;
        if (f == 2) ps.Emit(3);
        if (f == 5) ps.Play();
        if (f == 1 || f == 4 || f == 65) Debug.Log("n=" + ps.particleCount);
    }"""), "ps")
        out = self._pack(root)
        src = os.path.join(out, "harness.c")
        with open(src, "w") as f:
            f.write('#include "engine_draw.h"\nextern float Time_deltaTime;\n'
                    "int main(int argc, char **argv) { int f;\n"
                    "  engine_apply_argv(argc, argv); Time_deltaTime = 1.f / 60.f;\n"
                    "  for (f = 1; f <= 66; f++) engine_tick(); return 0; }\n")
        exe = os.path.join(out, "h")
        r = subprocess.run([_CC, "-O0", "-w", "-I", out, "-o", exe, src,
                            os.path.join(out, "engine.c"),
                            os.path.join(out, "data.c"), "-lm"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        run = subprocess.run([exe, "-logFile", "-"], capture_output=True,
                             text=True, timeout=60)
        # stopped and cleared; three emitted while stopped (5 s lifetime);
        # then one second of the default 10/s after Play
        self.assertEqual(run.stdout.split(), ["n=0", "n=3", "n=13"])


class TestAnimationCurves(unittest.TestCase):
    """A script's AnimationCurve field: read from the scene and evaluated
    as Unity evaluates it. It used to be packed as a reference to a class
    named `AnimationCurve` -- always null, its keys dropped."""

    TS = [-0.6, 0.0, 0.25, 0.5, 0.9, 1.0, 1.5, 2.25, 3.7]
    CURVES = {
        1: ([(0, 0, 0, 0), (1, 1, 0, 0)], 8, 8),                  # EaseInOut, clamp
        2: ([(0, 0, 0, 3), (1, 2, 1, "Infinity"), (2, 5, -1, 0)], 2, 4),  # Hermite, step; loop / pingpong
    }

    @staticmethod
    def _key(t, v, i, o, weighted=0):
        return ("    - serializedVersion: 3\n      time: %s\n      value: %s\n"
                "      inSlope: %s\n      outSlope: %s\n      tangentMode: 0\n"
                "      weightedMode: %d\n      inWeight: 0.33333334\n"
                "      outWeight: 0.33333334\n" % (t, v, i, o, weighted))

    def _project(self, curves, weighted=0):
        script = ("using UnityEngine;\npublic class Mover : MonoBehaviour {\n"
                  "    public AnimationCurve speed;\n    public int tag;\n    int f;\n"
                  "    void Update() {\n        f++;\n        if (f != 1) return;\n"
                  "        Debug.Log(\"c\" + tag + \" n=\" + speed.length);\n"
                  + "".join("        Debug.Log(\"c\" + tag + \" \" + speed.Evaluate(%rf));\n" % t
                            for t in self.TS)
                  + "    }\n}\n")
        scene = "%YAML 1.1\n"
        for n, (keys, pre, post) in sorted(curves.items()):
            fid = 10 * n
            scene += (
                "--- !u!1 &%d\nGameObject:\n  m_Name: M%d\n  m_Component:\n"
                "  - component: {fileID: %d}\n  - component: {fileID: %d}\n"
                "--- !u!4 &%d\nTransform:\n  m_GameObject: {fileID: %d}\n"
                "  m_LocalPosition: {x: 0, y: 0, z: 0}\n"
                "--- !u!114 &%d\nMonoBehaviour:\n  m_GameObject: {fileID: %d}\n"
                "  m_Script: {fileID: 11500000, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa}\n"
                "  speed:\n    serializedVersion: 2\n    m_Curve:\n"
                % (fid, n, fid + 1, fid + 2, fid + 1, fid, fid + 2, fid)
                + "".join(self._key(*k, weighted=weighted) for k in keys)
                + "    m_PreInfinity: %d\n    m_PostInfinity: %d\n"
                  "    m_RotationOrder: 4\n  tag: %d\n" % (pre, post, n))
        root = tempfile.mkdtemp(prefix="upack-curve-")
        self.addCleanup(shutil.rmtree, root, True)
        s = os.path.join(root, "Assets", "Scripts")
        os.makedirs(s)
        with open(os.path.join(s, "Mover.cs"), "w") as f:
            f.write(script)
        with open(os.path.join(s, "Mover.cs.meta"), "w") as f:
            f.write("guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
        os.makedirs(os.path.join(root, "Assets", "Scenes"))
        with open(os.path.join(root, "Assets", "Scenes", "S.unity"), "w") as f:
            f.write(scene)
        return root

    def test_reference_matches_unity_known_values(self):
        import tools.unity_pack_curves as uc
        ease = {"keys": [(0, 0, 0, 0), (1, 1, 0, 0)], "pre": 8, "post": 8}
        lin = {"keys": [(0, 0, 1, 1), (1, 1, 1, 1)], "pre": 8, "post": 8}
        # AnimationCurve.EaseInOut is smoothstep; .Linear is the line
        self.assertAlmostEqual(uc.evaluate(ease, 0.25), 0.15625)
        self.assertAlmostEqual(uc.evaluate(lin, 0.5), 0.5)
        self.assertAlmostEqual(uc.evaluate(dict(lin, post=2), 1.25), 0.25)
        self.assertAlmostEqual(uc.evaluate(dict(lin, post=4), 1.25), 0.75)

    @needs_cc
    def test_engine_evaluates_as_the_reference(self):
        import tools.unity_pack_curves as uc
        out = tempfile.mkdtemp(prefix="upack-curve-out-")
        self.addCleanup(shutil.rmtree, out, True)
        unity_pack.pack(self._project(self.CURVES), out, force=True, strict=True)
        src = os.path.join(out, "h.c")
        with open(src, "w") as f:
            f.write('#include "engine_draw.h"\nextern float Time_deltaTime;\n'
                    "int main(int c, char **v) { engine_apply_argv(c, v);\n"
                    "  Time_deltaTime = 1.f / 60.f; engine_tick(); return 0; }\n")
        exe = os.path.join(out, "h")
        r = subprocess.run([_CC, "-O2", "-w", "-I", out, "-o", exe, src,
                            os.path.join(out, "engine.c"),
                            os.path.join(out, "data.c"), "-lm"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        lines = subprocess.run([exe, "-logFile", "-"], capture_output=True,
                               text=True, timeout=60).stdout.split("\n")
        for n, (keys, pre, post) in self.CURVES.items():
            rows = [l.split(" ", 1)[1] for l in lines if l.startswith("c%d " % n)]
            self.assertEqual(rows[0], "n=%d" % len(keys))
            ref = {"keys": [(float(a), float(b), uc._num(str(c)), uc._num(str(d)))
                            for a, b, c, d in keys], "pre": pre, "post": post}
            for t, got in zip(self.TS, rows[1:]):
                self.assertAlmostEqual(float(got), uc.evaluate(ref, t), places=4,
                                       msg="curve %d at t=%g" % (n, t))

    def test_weighted_tangents_are_refused(self):
        with self.assertRaises(unity_pack.PackError) as cm:
            unity_pack.pack(self._project({1: self.CURVES[1]}, weighted=3),
                            tempfile.mkdtemp(prefix="upack-curve-out-"), force=True)
        self.assertIn("weighted", cm.exception.message)


class TestLineRenderer(unittest.TestCase):
    """A LineRenderer: read from the scene, scripted, and drawn as one quad
    a segment. It was not read at all: the scene reader's kind whitelist
    did not name it, so the component vanished."""

    SCRIPT = """using UnityEngine;
public class Drawer : MonoBehaviour {
    public LineRenderer lr;
    void Start() {
        lr.positionCount = 4;
        lr.SetPosition(3, new Vector3(0f, 2f, 0f));
        lr.loop = true;
        lr.startColor = Color.blue;
        Debug.Log("n=" + lr.positionCount + " y3=" + lr.GetPosition(3).y + " w0=" + lr.startWidth);
    }
}
"""

    @staticmethod
    def _key(t, v, i, o):
        return ("      - serializedVersion: 3\n        time: %s\n        value: %s\n"
                "        inSlope: %s\n        outSlope: %s\n        tangentMode: 0\n"
                "        weightedMode: 0\n        inWeight: 0.33333334\n"
                "        outWeight: 0.33333334\n" % (t, v, i, o))

    def _project(self, script, world=1, rot_z=0.0, with_line=True):
        lr = ("--- !u!120 &5\nLineRenderer:\n  m_GameObject: {fileID: 1}\n  m_Enabled: 1\n"
              "  m_SortingOrder: 3\n  m_Positions:\n  - {x: 0, y: 0, z: 0}\n"
              "  - {x: 2, y: 0, z: 0}\n  - {x: 2, y: 2, z: 0}\n  m_Parameters:\n"
              "    serializedVersion: 3\n    widthMultiplier: 0.5\n    widthCurve:\n"
              "      serializedVersion: 2\n      m_Curve:\n"
              + self._key(0, 1, 0, -0.5) + self._key(1, 0.5, -0.5, 0) +
              "      m_PreInfinity: 2\n      m_PostInfinity: 2\n      m_RotationOrder: 4\n"
              "    colorGradient:\n      serializedVersion: 2\n"
              "      key0: {r: 1, g: 1, b: 1, a: 1}\n      key1: {r: 1, g: 0, b: 0, a: 1}\n"
              "      ctime0: 0\n      ctime1: 65535\n      atime0: 0\n      atime1: 65535\n"
              "      m_Mode: 0\n      m_NumColorKeys: 2\n      m_NumAlphaKeys: 2\n"
              "  m_UseWorldSpace: %d\n  m_Loop: 0\n" % world)
        scene = ("%%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: Drawer\n  m_Component:\n"
                 "  - component: {fileID: 2}\n  - component: {fileID: 3}\n"
                 "  - component: {fileID: 5}\n--- !u!4 &2\nTransform:\n"
                 "  m_GameObject: {fileID: 1}\n  m_LocalRotation: {x: 0, y: 0, z: %s, w: %s}\n"
                 "  m_LocalPosition: {x: 0, y: 0, z: 0}\n"
                 "--- !u!114 &3\nMonoBehaviour:\n  m_GameObject: {fileID: 1}\n"
                 "  m_Script: {fileID: 11500000, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa}\n"
                 "  lr: {fileID: 5}\n" % (rot_z, (1 - rot_z * rot_z) ** 0.5)) + lr
        if not with_line:
            scene = scene.split("--- !u!120 ")[0].replace(
                "  - component: {fileID: 5}\n", "").replace("  lr: {fileID: 5}\n", "")
        root = tempfile.mkdtemp(prefix="upack-lr-")
        self.addCleanup(shutil.rmtree, root, True)
        d = os.path.join(root, "Assets", "Scripts")
        os.makedirs(d)
        with open(os.path.join(d, "Drawer.cs"), "w") as f:
            f.write(script)
        with open(os.path.join(d, "Drawer.cs.meta"), "w") as f:
            f.write("guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
        os.makedirs(os.path.join(root, "Assets", "Scenes"))
        with open(os.path.join(root, "Assets", "Scenes", "S.unity"), "w") as f:
            f.write(scene)
        return root

    def _run_lines(self, root):
        """Pack, tick once; the log and the untextured quads' lines."""
        out = tempfile.mkdtemp(prefix="upack-lr-out-")
        self.addCleanup(shutil.rmtree, out, True)
        unity_pack.pack(root, out, force=True, strict=True)
        src = os.path.join(out, "h.c")
        with open(src, "w") as f:
            f.write('#include <stdio.h>\n#include "engine_draw.h"\nextern float Time_deltaTime;\n'
                    "int main(int c, char **v) { EngineDraw b[32]; int n, k;\n"
                    "  engine_apply_argv(c, v); Time_deltaTime = 1.f / 60.f;\n"
                    "  engine_tick(); n = engine_collect_draws(b, 32);\n"
                    "  for (k = 0; k < n; k++) if (b[k].tex == -2)\n"
                    "    printf(\"Q %g %g %g %g %g %g %g %g %g %d\\n\", b[k].x, b[k].y,\n"
                    "           b[k].half_w, b[k].half_h, b[k].m00, b[k].m10,\n"
                    "           b[k].r, b[k].g, b[k].b, b[k].sorting_order);\n"
                    "  return 0; }\n")
        exe = os.path.join(out, "h")
        r = subprocess.run([_CC, "-O2", "-w", "-I", out, "-o", exe, src,
                            os.path.join(out, "engine.c"), os.path.join(out, "data.c"),
                            "-lm"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        run = subprocess.run([exe, "-logFile", "-"], capture_output=True,
                             text=True, timeout=60)
        return run.stdout.splitlines()

    @needs_cc
    def test_scripted_line_draws_as_unity_evaluates_it(self):
        import math
        import tools.unity_pack_curves as uc
        import tools.unity_pack_lines as ul
        lines = self._run_lines(self._project(self.SCRIPT))
        self.assertIn("n=4 y3=2 w0=0.5", lines)
        quads = [list(map(float, l.split()[1:])) for l in lines if l.startswith("Q ")]
        # the reference: Unity's rules, independently of the engine
        pts = [(0, 0), (2, 0), (2, 2), (0, 2)]          # 4th set by the script
        width = {"keys": [(0, 1, 0, -0.5), (1, 0.5, -0.5, 0)], "pre": 2, "post": 2}
        grad = {"mode": 0, "colors": [(0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 0.0, 0.0)],
                "alphas": [(0.0, 1.0), (1.0, 1.0)]}     # startColor = blue
        segs = [(pts[k], pts[(k + 1) % 4]) for k in range(4)]   # loop = true
        lens = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in segs]
        self.assertEqual(len(quads), 4)
        run_ = 0.0
        for (a, b), L, q in zip(segs, lens, quads):
            u = (run_ + L / 2) / sum(lens)
            run_ += L
            col = ul.gradient_eval(grad, u)
            want = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, L / 2,
                    0.5 * uc.evaluate(width, u) / 2, (b[0] - a[0]) / L,
                    (b[1] - a[1]) / L, col[0], col[1], col[2], 3]
            for got, exp in zip(q, want):
                self.assertAlmostEqual(got, exp, places=4)

    @needs_cc
    def test_added_line_takes_a_spare_row(self):
        lines = self._run_lines(self._project("""using UnityEngine;
public class Drawer : MonoBehaviour {
    void Start() {
        LineRenderer a = gameObject.AddComponent<LineRenderer>();
        a.positionCount = 3;
        a.SetPosition(2, new Vector3(1f, 2f, 0f));
        a.useWorldSpace = false;
        Debug.Log("n=" + a.positionCount + " y2=" + a.GetPosition(2).y + " ws=" + a.useWorldSpace
                  + " again=" + (gameObject.AddComponent<LineRenderer>() == null)
                  + " same=" + (GetComponent<LineRenderer>() == a));
    }
}
""", with_line=False))
        self.assertIn("n=3 y2=2 ws=False again=True same=True", lines)
        # Unity's new line: two points at the origin (no quad), then the one set
        self.assertEqual(len([l for l in lines if l.startswith("Q ")]), 1)

    def test_gradient_reference(self):
        import tools.unity_pack_lines as ul
        g = {"mode": 0, "colors": [(0.0, 1.0, 1.0, 1.0), (1.0, 1.0, 0.0, 0.0)],
             "alphas": [(0.0, 1.0), (1.0, 0.0)]}
        self.assertEqual(ul.gradient_eval(g, 0.25), (1.0, 0.75, 0.75, 0.75))
        fixed = dict(g, mode=1)                # the first key at or after t
        self.assertEqual(ul.gradient_eval(fixed, 0.25), (1.0, 0.0, 0.0, 0.0))

    @needs_cc
    def test_local_space_on_a_rotated_object_turns_with_it(self):
        import math
        lines = self._run_lines(self._project(
            self.SCRIPT, world=0, rot_z=math.sqrt(0.5)))
        q = [list(map(float, l.split()[1:])) for l in lines if l.startswith("Q ")][0]
        # 90 degrees: the first segment (0,0)-(2,0) is drawn (0,0)-(0,2)
        for got, want in zip(q[:6], (0, 1, 1, q[3], 0, 1)):
            self.assertAlmostEqual(got, want, places=4)


class TestTransformTranslate(unittest.TestCase):
    """`transform.Translate`, and the diagnostics for Transform members.

    Translate was refused with CS1061 -- "'Transform' does not contain a
    definition for 'Translate'" -- which is false: every real Transform
    member the pack does not lower got that. A real one is now "not packed
    yet" (CS8000), an invented one CS1061. Translate itself moves by the
    delta in the object's own axes (Space.Self, the default) or the
    world's; it is the first use of a live-rotation read in an engine with
    no string concatenation, which lost its runtime helpers (the helper
    marker was emitted only for string concatenation)."""

    def _project(self, scripts, rots):
        root = tempfile.mkdtemp(prefix="upack-tr-")
        self.addCleanup(shutil.rmtree, root, True)
        sd = os.path.join(root, "Assets", "Scripts")
        os.makedirs(sd)
        scene, fid = "%YAML 1.1\n", 100
        for k, (name, body) in enumerate(scripts.items()):
            guid = "%032x" % (k + 1)
            with open(os.path.join(sd, name + ".cs"), "w") as f:
                f.write("using UnityEngine;\npublic class %s : MonoBehaviour {\n"
                        "    int f;\n    void Update() {\n        f++; if (f > 1) return;\n"
                        "        %s\n        Debug.Log(\"%s \" + transform.position.x + \" \""
                        " + transform.position.y);\n    }\n}\n" % (name, body, name))
            with open(os.path.join(sd, name + ".cs.meta"), "w") as f:
                f.write("guid: %s\n" % guid)
            q = rots.get(name, (0, 0, 0, 1))
            scene += (
                "--- !u!1 &%d\nGameObject:\n  m_Name: %s\n  m_Component:\n"
                "  - component: {fileID: %d}\n  - component: {fileID: %d}\n"
                "--- !u!4 &%d\nTransform:\n  m_GameObject: {fileID: %d}\n"
                "  m_LocalRotation: {x: %r, y: %r, z: %r, w: %r}\n"
                "  m_LocalPosition: {x: 0, y: 0, z: 0}\n"
                "--- !u!114 &%d\nMonoBehaviour:\n  m_GameObject: {fileID: %d}\n"
                "  m_Script: {fileID: 11500000, guid: %s}\n"
                % (fid, name, fid + 1, fid + 2, fid + 1, fid, q[0], q[1], q[2], q[3],
                   fid + 2, fid, guid))
            fid += 10
        os.makedirs(os.path.join(root, "Assets", "Scenes"))
        with open(os.path.join(root, "Assets", "Scenes", "S.unity"), "w") as f:
            f.write(scene)
        return root

    @needs_cc
    def test_self_and_world_space(self):
        import math
        q90 = (0.0, 0.0, math.sin(math.pi / 4), math.cos(math.pi / 4))
        root = self._project({
            "A": "transform.Translate(1f, 0f);",                       # Self
            "B": "transform.Translate(new Vector3(1f, 0f, 0f), Space.World);",
            "C": "transform.Rotate(0f, 0f, 90f); transform.Translate(Vector2.right * 1f);",
        }, {"A": q90, "B": q90})
        out = tempfile.mkdtemp(prefix="upack-tr-out-")
        self.addCleanup(shutil.rmtree, out, True)
        unity_pack.pack(root, out, force=True, strict=True)
        src = os.path.join(out, "h.c")
        with open(src, "w") as f:
            f.write('#include "engine_draw.h"\nextern float Time_deltaTime;\n'
                    "int main(int c, char **v) { engine_apply_argv(c, v);\n"
                    "  Time_deltaTime = 1.f / 60.f; engine_tick(); return 0; }\n")
        exe = os.path.join(out, "h")
        r = subprocess.run([_CC, "-O2", "-w", "-I", out, "-o", exe, src,
                            os.path.join(out, "engine.c"), os.path.join(out, "data.c"),
                            "-lm"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        got = {}
        for l in subprocess.run([exe, "-logFile", "-"], capture_output=True,
                                text=True, timeout=60).stdout.splitlines():
            p = l.split()
            if len(p) == 3:
                got[p[0]] = (float(p[1]), float(p[2]))
        # Unity: a 90-degree object's right is world up; World ignores it;
        # after Rotate the heading is the new one
        for name, want in (("A", (0, 1)), ("B", (1, 0)), ("C", (0, 1))):
            self.assertAlmostEqual(got[name][0], want[0], places=4, msg=name)
            self.assertAlmostEqual(got[name][1], want[1], places=4, msg=name)

    def test_real_member_is_not_packed_yet_invented_is_cs1061(self):
        for body, code, member in (("float z = transform.right.x;", "CS8000", "right"),
                                   ("transform.Fly();", "CS1061", "Fly")):
            with self.assertRaises(unity_pack.PackError) as cm:
                unity_pack.pack(self._project({"P": body}, {}),
                                tempfile.mkdtemp(prefix="upack-tr-out-"), force=True)
            self.assertIn("error %s" % code, cm.exception.message)
            self.assertIn(member, cm.exception.message)




class TestPositionVectors(unittest.TestCase):
    """`transform.position` as a vector value, and Vector2's MoveTowards /
    Lerp / LerpUnclamped / Min / Max, in a 2D pack. Only `.x` / `+=` /
    `= new Vector3` were lowered before: `Vector2.MoveTowards(
    transform.position, ..)` or `transform.position = Vector2.Lerp(..)`
    emptied the method."""

    @needs_cc
    def test_values_follow_unity(self):
        body = (
            # MoveTowards: a step short of the target, then reaching it
            "Vector2 a = Vector2.MoveTowards(new Vector2(0f, 0f), new Vector2(3f, 4f), 2f);"
            " Debug.Log(\"mt \" + a.x + \" \" + a.y);"
            " Vector2 b = Vector2.MoveTowards(new Vector2(0f, 0f), new Vector2(3f, 4f), 9f);"
            " Debug.Log(\"mt2 \" + b.x + \" \" + b.y);"
            # Lerp clamps t, LerpUnclamped does not
            " Vector2 c = Vector2.Lerp(new Vector2(0f, 0f), new Vector2(2f, 4f), 1.5f);"
            " Debug.Log(\"lerp \" + c.x + \" \" + c.y);"
            " Vector2 d = Vector2.LerpUnclamped(new Vector2(0f, 0f), new Vector2(2f, 4f), 1.5f);"
            " Debug.Log(\"lu \" + d.x + \" \" + d.y);"
            " Vector2 e = Vector2.Min(new Vector2(1f, 5f), new Vector2(3f, 2f));"
            " Vector2 g = Vector2.Max(new Vector2(1f, 5f), new Vector2(3f, 2f));"
            " Debug.Log(\"mm \" + e.x + \" \" + e.y + \" \" + g.x + \" \" + g.y);"
            # the position as a value: read, step towards a point, write
            " transform.position = Vector2.MoveTowards(transform.position, new Vector2(10f, 0f), 4f);"
            " Vector2 p = transform.position;"
            " Debug.Log(\"pos \" + p.x + \" \" + p.y);")
        root = project(self, {"P": script("P", body)}, [("P",)])
        lines = run_frames(self, pack(self, root))
        got = dict((l.split(" ", 1)[0], [float(x) for x in l.split()[1:]])
                   for l in lines if " " in l)
        self.assertEqual(got["mt"], [1.2, 1.6])        # 2 along (3, 4) / 5
        self.assertEqual(got["mt2"], [3.0, 4.0])       # within reach: the target
        self.assertEqual(got["lerp"], [2.0, 4.0])      # t clamped to 1
        self.assertEqual(got["lu"], [3.0, 6.0])
        self.assertEqual(got["mm"], [1.0, 2.0, 3.0, 5.0])
        self.assertEqual(got["pos"], [4.0, 0.0])       # from x = 0 (object 0)



class TestInputButtons(unittest.TestCase):
    """Input.GetButton / GetButtonDown / GetButtonUp by name. Only "Jump"
    existed -- any other name was silently never pressed -- and Down / Up
    emptied the method."""

    @needs_cc
    def test_down_held_up_per_frame(self):
        src = ("using UnityEngine;\npublic class P : MonoBehaviour {\n"
               "    int f;\n    void Update() {\n        f++;\n"
               # `? 1 : 0`: "x" + a bool prints 1 / 0 here, not True /
               # False (a separate gap: scalar_kind has no bool)
               "        Debug.Log(\"f\" + f + \" \" + (Input.GetButtonDown(\"Jump\") ? 1 : 0)"
               " + \" \" + (Input.GetButton(\"Jump\") ? 1 : 0) + \" \""
               " + (Input.GetButtonUp(\"Jump\") ? 1 : 0) + \" \""
               " + (Input.GetButton(\"Fire1\") ? 1 : 0));\n    }\n}\n")
        out = pack(self, project(self, {"P": src}, [("P",)]))
        # frames 1..5: Jump up, down, held, released, up; Fire1 held
        lines = run_frames(self, out, 0, body=(
            "extern int engine_input_button_Jump, engine_input_button_Fire1;\n"
            "  { int jump[5] = {0, 1, 1, 0, 0}; for (f = 0; f < 5; f++) {\n"
            "    engine_input_button_Jump = jump[f]; engine_input_button_Fire1 = 1;\n"
            "    engine_tick(); } }"))
        self.assertEqual([l for l in lines if l.startswith("f")], [
            "f1 0 0 0 1",
            "f2 1 1 0 1",       # pressed this frame
            "f3 0 1 0 1",      # held
            "f4 0 0 1 1",      # released this frame
            "f5 0 0 0 1"])



class TestOwnName(unittest.TestCase):
    """`name` / `gameObject.name`: the GameObject's name. It reached C as an
    undeclared `name`, and `name + " hp"` (a literal second) had no string
    concatenation helper."""

    @needs_cc
    def test_each_object_its_own_name(self):
        src = script("P", 'Debug.Log(name + " hp"); Debug.Log("go " + gameObject.name);')
        lines = run_frames(self, pack(self, project(self, {"P": src}, [("P",), ("P",)])))
        self.assertEqual(sorted(l for l in lines if l.endswith(" hp")), ["P0 hp", "P1 hp"])
        self.assertEqual(sorted(l for l in lines if l.startswith("go ")), ["go P0", "go P1"])

    @needs_cc
    @needs_coost
    def test_own_field_named_name_is_the_field(self):
        src = script("P", 'Debug.Log("n " + name);', members='    public string name = "mine";')
        lines = run_frames(self, pack(self, project(self, {"P": src}, [("P",)])))
        self.assertIn("n mine", lines)



class TestSingletonField(unittest.TestCase):
    """`public static GameManager Instance; .. Instance = this;` -- the
    everyday singleton. Its own `Instance = this` reached C as
    `Type_Instance() = i`, and a class used only through its own bare
    `Instance` got no cache at all. A static *field* is null until
    assigned (no FindObjectOfType fallback), so only the first Awake sees
    `Instance == null`, and a duplicate destroys itself."""

    GM = """using UnityEngine;
public class GM : MonoBehaviour {
    public static GM Instance;
    public int score;
    void Awake() {
        if (Instance == null) Debug.Log("first " + name);
        if (Instance != null && Instance != this) { Debug.Log("dup " + name); Destroy(gameObject); return; }
        Instance = this;
    }
    public void Add(int v) { score += v; }
}
"""
    USER = """using UnityEngine;
public class User : MonoBehaviour {
    int f;
    void Update() { f++; if (f > 1) return; GM.Instance.Add(2); Debug.Log("score " + GM.Instance.score); }
}
"""

    @needs_cc
    def test_first_wins_duplicate_destroyed(self):
        root = project(self, {"GM": self.GM, "User": self.USER},
                       [("GM",), ("GM",), ("User",)])
        lines = run_frames(self, pack(self, root))
        self.assertEqual([l for l in lines if l.startswith("first")], ["first GM0"])
        self.assertEqual([l for l in lines if l.startswith("dup")], ["dup GM1"])
        self.assertIn("score 2", lines)



class TestStaticStateAndForeach(unittest.TestCase):
    """Slime Jump forms: a static property over a static backing field
    (`P += x`, `P++`), a `static Bag b = new Bag();` handle made at the
    first tick, and `foreach` over a List<int>. Each emptied its method."""

    BAG = "public class Bag { public int n; }\n"
    P = """using UnityEngine;
using System.Collections.Generic;
public class P : MonoBehaviour {
    static int _pts;
    public static int Points { get { return _pts; } set { _pts = value; } }
    static Bag bag = new Bag();
    List<int> xs = new List<int>();
    int _f;
    void Update() {
        _f++; if (_f > 1) return;
        Points += 3; Points++;
        bag.n = 5;
        xs.Add(2); xs.Add(4);
        int s = 0;
        foreach (int x in xs) s += x;
        Debug.Log("pts " + Points + " bag " + bag.n + " sum " + s);
    }
}
"""

    @needs_cc
    def test_values_follow_csharp(self):
        root = project(self, {"P": self.P, "Bag": self.BAG}, [("P",)])
        self.assertIn("pts 4 bag 5 sum 6", run_frames(self, pack(self, root)))


class TestStaticReference(unittest.TestCase):
    """`static Ach current;` -- a static reference to a packed object, set
    bare in its class and read as `Ach.current` elsewhere (Slime Jump's
    `SpeedAchievement.current` emptied `GameManager.Update`)."""

    ACH = """using UnityEngine;
public class Ach : MonoBehaviour {
    public static Ach current;
    public float left = 3f;
    public float TimeLeft { get { return left; } set { left = value; } }
    void Start() { current = this; }
}
"""
    MGR = """using UnityEngine;
public class Mgr : MonoBehaviour {
    int _f;
    void Update() {
        _f++;
        if (Ach.current == null) { Debug.Log("none " + _f); return; }
        Ach.current.TimeLeft -= 1f;
        if (Ach.current.TimeLeft <= 0) { Ach.current = null; return; }
        Debug.Log("left " + (int)Ach.current.TimeLeft);
    }
}
"""

    @needs_cc
    def test_set_read_and_cleared(self):
        root = project(self, {"Ach": self.ACH, "Mgr": self.MGR},
                       [("Ach",), ("Mgr",)])
        out = run_frames(self, pack(self, root), 5)
        self.assertIn("left 2", out)
        self.assertIn("left 1", out)
        self.assertIn("none 4", out)


class TestSingletonFieldCompare(unittest.TestCase):
    """`Lasso.instance.changeLengthInput == 0` is a read: Slime Jump's
    `Player.DoUpdate` had it lowered as a setter of `= 0) { ... }`."""

    LASSO = """using UnityEngine;
public class Lasso : MonoBehaviour {
    public static Lasso instance;
    public int changeLengthInput;
    public bool isAttached = true;
    void Awake() { instance = this; }
}
"""
    CAM = """using UnityEngine;
public class Cam : MonoBehaviour {
    public static Cam instance;
    public bool followPlayer = true;
    void Awake() { instance = this; }
}
"""
    MGR = """using UnityEngine;
public class Mgr : MonoBehaviour {
    void Update() {
        if (Lasso.instance.isAttached && Lasso.instance.changeLengthInput == 0)
        {
            Cam.instance.followPlayer = false;
        }
        if (!Cam.instance.followPlayer) Debug.Log("follow False");
    }
}
"""

    @needs_cc
    def test_compare_is_a_read(self):
        root = project(self, {"Lasso": self.LASSO, "Cam": self.CAM,
                              "Mgr": self.MGR},
                       [("Lasso",), ("Cam",), ("Mgr",)])
        out = run_frames(self, pack(self, root), 2)
        self.assertIn("follow False", out)


class TestPlayerUpdateForms(unittest.TestCase):
    """Slime Jump's `Player.DoUpdate` forms: a compound write keeps its
    value whole (`x -= 3 - 1` is 8, not 6), a Vector2 field's `*=`, `.x` of
    a Vector2 property, per-frame shader-parameter statements dropped, and
    a TMP text write that stops the player only if it runs."""

    MGR = """using UnityEngine;
using TMPro;
public class Mgr : MonoBehaviour {
    public TMP_Text label;
    public SpriteRenderer sr;
    public float x = 10f;
    public Vector2 v = new Vector2(1f, 2f);
    public static Vector2 Stick { get { return new Vector2(0.5f, 0f); } }
    void Update() {
        x -= 3f - 1f;
        v *= 2f;
        if (x < 0f) label.text = "never";
        Material mat = new Material(sr.sharedMaterial);
        mat.SetInt("_g", 1);
        sr.sharedMaterial = mat;
        Debug.Log("x " + (int)x + " v " + (int)v.y + " s " + (int)(Stick.x * 10f));
    }
}
"""

    @needs_cc
    def test_forms(self):
        root = project(self, {"Mgr": self.MGR}, [("Mgr",)])
        # not strict: strict refuses the shader and TMP statements instead
        out = pack(self, root, strict=False)
        self.assertIn("x 8 v 4 s 5", run_frames(self, out, 1))
        # frame 6: x < 0 runs the TMP write
        with self.assertRaises(AssertionError) as cm:
            run_frames(self, out, 6)
        self.assertIn("Mgr.cs:12: `TMP_Text.text` is not lowered",
                      str(cm.exception))


class TestEmbeddedStruct(unittest.TestCase):
    """A [Serializable] struct a component embeds (Slime Jump's
    `AnimationEntry jumpAnimationEntry`) is authored as a nested mapping:
    each value is a row of the struct's class, the field its index. The
    rows are shared by copies, so a write to one is refused."""

    ENTRY = """using System;
[Serializable]
public struct Entry {
    public string stateName;
    public int layer;
    public float length;
    public bool Is(string s) { return stateName == s; }
    public float Twice() { return length * 2f; }
}
"""
    MGR = """using UnityEngine;
public class Mgr : MonoBehaviour {
    public Entry jump;
    public Entry land;
    void Update() {
        if (jump.Is("Jump")) Debug.Log("jump " + jump.layer + " " + (int)land.Twice());
        else Debug.Log("other " + land.layer);
    }
}
"""
    FIELDS = ("  jump:\n    stateName: %s\n    layer: %d\n    length: 0.5\n"
              "  land:\n    stateName: Land\n    layer: %d\n    length: %s\n")

    @needs_cc
    def test_values_per_object(self):
        root = project(self, {"Entry": self.ENTRY, "Mgr": self.MGR}, [
            ("Mgr", None, self.FIELDS % ("Jump", 1, 2, "3.5")),
            ("Mgr", None, self.FIELDS % ("Idle", 4, 5, "1"))])
        out = run_frames(self, pack(self, root), 1)
        self.assertIn("jump 1 7", out)
        self.assertIn("other 5", out)

    def test_prefab_override_reaches_nested_value(self):
        doc = ("MonoBehaviour:\n  jump:\n    stateName: Jump\n    layer: 1\n"
               "  land:\n    layer: 2\n")
        got = unity_pack._set_yaml_property(doc, "land.layer", "7")
        self.assertIn("  land:\n    layer: 7\n", got)
        self.assertIn("    layer: 1\n", got)
        self.assertEqual(unity_pack._embedded_values(got)["land"]["fields"],
                         {"layer": 7})

    def test_write_is_refused(self):
        mgr = self.MGR.replace("void Update() {",
                               "void Update() {\n        jump.layer = 3;")
        root = project(self, {"Entry": self.ENTRY, "Mgr": mgr}, [
            ("Mgr", None, self.FIELDS % ("Jump", 1, 2, "3.5"))])
        with self.assertRaises(unity_pack.PackError) as cm:
            pack(self, root)
        self.assertIn("embedded struct", str(cm.exception))


class TestAnimatorStateMachine(unittest.TestCase):
    """An Animator field runs its controller: `Play` switches state at the
    animator update (after Update), `IsName` reads the current one, the
    clip's float curve writes the script's field, and the exit-time
    transition returns to the default state."""

    MGR = """using UnityEngine;
public class Mgr : MonoBehaviour {
    public Animator anim;
    public Vector2 multiplySize = Vector2.one;
    int f;
    void Update() {
        f++;
        if (f == 2) anim.Play("Squash");
        if (f == 2 || f == 3 || f == 5 || f == 20)
            Debug.Log("f" + f + " " + (anim.GetCurrentAnimatorStateInfo(0).IsName("Squash") ? 1 : 0) + " " + (int)(multiplySize.x * 100f + 0.5f));
    }
}
"""
    CTRL = """%YAML 1.1
--- !u!91 &9100000
AnimatorController:
  m_Name: C
  m_AnimatorParameters: []
  m_AnimatorLayers:
  - serializedVersion: 5
    m_Name: Base Layer
    m_StateMachine: {fileID: 1107000}
    m_Mask: {fileID: 0}
    m_BlendingMode: 0
    m_SyncedLayerIndex: -1
    m_DefaultWeight: 0
--- !u!1107 &1107000
AnimatorStateMachine:
  m_Name: Base Layer
  m_ChildStates:
  - serializedVersion: 1
    m_State: {fileID: 1102001}
  - serializedVersion: 1
    m_State: {fileID: 1102002}
  m_ChildStateMachines: []
  m_AnyStateTransitions: []
  m_EntryTransitions: []
  m_DefaultState: {fileID: 1102001}
--- !u!1102 &1102001
AnimatorState:
  m_Name: Idle
  m_Speed: 1
  m_CycleOffset: 0
  m_Transitions: []
  m_Motion: {fileID: 0}
--- !u!1102 &1102002
AnimatorState:
  m_Name: Squash
  m_Speed: 1
  m_CycleOffset: 0
  m_Transitions:
  - {fileID: 1101003}
  m_Motion: {fileID: 7400000, guid: CLIPGUID, type: 2}
--- !u!1101 &1101003
AnimatorStateTransition:
  m_Conditions: []
  m_DstState: {fileID: 1102001}
  m_Mute: 0
  m_IsExit: 0
  m_TransitionDuration: 0
  m_TransitionOffset: 0
  m_ExitTime: 1
  m_HasExitTime: 1
"""
    CLIP = """%YAML 1.1
--- !u!74 &7400000
AnimationClip:
  m_Name: Squash
  m_PositionCurves: []
  m_FloatCurves:
  - serializedVersion: 2
    curve:
      serializedVersion: 2
      m_Curve:
      - serializedVersion: 3
        time: 0
        value: 1
        inSlope: -5
        outSlope: -5
        weightedMode: 0
      - serializedVersion: 3
        time: 0.1
        value: 0.5
        inSlope: -5
        outSlope: -5
        weightedMode: 0
    attribute: multiplySize.x
    path: 
    classID: 114
  m_PPtrCurves: []
  m_AnimationClipSettings:
    m_StopTime: 0.1
    m_LoopTime: 0
"""
    ANIMATOR = ("--- !u!95 &{fid}\nAnimator:\n  m_GameObject: {{fileID: {go}}}\n"
                "  m_Enabled: 1\n  m_Controller: {{fileID: 9100000, guid: "
                "c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0, type: 2}}\n")

    def _project(self, ctrl=None):
        root = project(self, {"Mgr": self.MGR}, [
            ("Mgr", self.ANIMATOR,
             "  anim: {fileID: 103}\n  multiplySize: {x: 1, y: 1}\n")])
        d = os.path.join(root, "Assets", "Animations")
        os.makedirs(d)
        for name, guid, text in (
                ("C.controller", "c0" * 16,
                 (ctrl or self.CTRL).replace("CLIPGUID", "d0" * 16)),
                ("Squash.anim", "d0" * 16, self.CLIP)):
            with open(os.path.join(d, name), "w") as f:
                f.write(text)
            with open(os.path.join(d, name + ".meta"), "w") as f:
                f.write("guid: %s\n" % guid)
        return root

    @needs_cc
    def test_play_isname_curve_and_exit(self):
        out = run_frames(self, pack(self, self._project()), 20)
        # f2: Play is pending; f3/f5: Squash after 1 and 3 animator ticks
        # (value 1 - 5t); f20: back in Idle through the exit transition
        self.assertIn("f2 0 100", out)
        self.assertIn("f3 1 92", out)
        self.assertIn("f5 1 75", out)
        self.assertTrue(any(l.startswith("f20 0 ") for l in out), out)

    def test_parameters_are_refused(self):
        ctrl = self.CTRL.replace(
            "  m_AnimatorParameters: []\n",
            "  m_AnimatorParameters:\n  - m_Name: Speed\n    m_Type: 1\n")
        with self.assertRaises(unity_pack.PackError) as cm:
            pack(self, self._project(ctrl))
        self.assertIn("parameters", str(cm.exception))


class TestColliderBounds(unittest.TestCase):
    """`Collider2D.bounds` of a field (Slime Jump's wall probe): the world
    AABB of a rotated, offset box and of a capsule."""

    MGR = """using UnityEngine;
public class Mgr : MonoBehaviour {
    public BoxCollider2D box;
    public Collider2D cap;
    int f;
    void Update() {
        f++; if (f > 1) return;
        Vector2 c = (Vector2) box.bounds.center + Vector2.down * box.bounds.extents.y;
        Debug.Log("box " + R(box.bounds.center.x) + " " + R(box.bounds.center.y) + " " + R(box.bounds.extents.x) + " " + R(box.bounds.max.y) + " " + R(box.bounds.size.x) + " " + R(c.y));
        Debug.Log("cap " + R(cap.bounds.min.x) + " " + R(cap.bounds.extents.y));
    }
    static int R(float v) { return Mathf.RoundToInt(v * 100f); }
}
"""
    BOX = ("--- !u!61 &{fid}\nBoxCollider2D:\n  m_GameObject: {{fileID: {go}}}\n"
           "  m_Enabled: 1\n  m_Offset: {{x: 0.5, y: 0}}\n  m_Size: {{x: 2, y: 1}}\n")
    CAP = ("--- !u!70 &{fid}\nCapsuleCollider2D:\n  m_GameObject: {{fileID: {go}}}\n"
           "  m_Enabled: 1\n  m_Offset: {{x: 0, y: 0}}\n  m_Size: {{x: 1, y: 3}}\n"
           "  m_Direction: 0\n")

    @needs_cc
    def test_box_and_capsule(self):
        log = "using UnityEngine;\npublic class Log : MonoBehaviour { }\n"
        root = project(self, {"Mgr": self.MGR, "Log": log}, [
            ("Mgr", self.BOX, "  box: {fileID: 103}\n  cap: {fileID: 113}\n",
             (0.0, 0.0, 0.70710678, 0.70710678)),
            ("Log", self.CAP)])
        # no Box2D in this harness: nothing moves
        out = run_frames(self, pack(self, root), 1,
                         pre="void engine_box2d_step(float dt) { (void)dt; }\n")
        # turned 90 degrees: the 2 x 1 box offset (0.5, 0) is 1 x 2 at (0, 0.5)
        self.assertIn("box 0 50 50 150 100 -50", out)
        # the capsule at x = 1: 1 wide, 3 tall
        self.assertIn("cap 50 150", out)


class TestPrefabInstantiateAt(unittest.TestCase):
    """`Instantiate(prefabField, pos, Quaternion.identity)` of an unplaced
    prefab (Slime Jump's dust clouds): clones at *pos*; a destroyed clone's
    slot is reused."""

    @needs_cc
    def test_clone_at_position(self):
        mgr = ("using UnityEngine;\npublic class Mgr : MonoBehaviour {\n"
               "    public Cloud prefab;\n    Vector2 at;\n    int f;\n"
               "    void Update() {\n        f++;\n"
               "        if (f <= 2) { at = new Vector2(f * 3, 2f); "
               "Instantiate(prefab, at, Quaternion.identity); }\n    }\n}\n")
        cloud = ("using UnityEngine;\npublic class Cloud : MonoBehaviour {\n"
                 "    public float life;\n    float t;\n"
                 "    void Start() { Debug.Log(\"cloud \" + Mathf.RoundToInt("
                 "transform.position.x) + \" \" + Mathf.RoundToInt("
                 "transform.position.y) + \" \" + Mathf.RoundToInt(life)); "
                 "Destroy(gameObject, 0.04f); }\n"
                 "    void Update() { t += Time.deltaTime; }\n"
                 "    void OnDestroy() { Debug.Log(\"gone \" + "
                 "Mathf.RoundToInt(t * 60f)); }\n}\n")
        root = project(self, {"Cloud": cloud, "Mgr": mgr}, [
            ("Mgr", None, "  prefab: {fileID: 12, guid: %032x, type: 3}\n"
             % 99)])
        with open(os.path.join(root, "Assets", "Cloud.prefab"), "w") as f:
            f.write("%%YAML 1.1\n--- !u!1 &10\nGameObject:\n  m_Name: Cloud\n"
                    "  m_Component:\n  - component: {fileID: 11}\n"
                    "  - component: {fileID: 12}\n"
                    "--- !u!4 &11\nTransform:\n  m_GameObject: {fileID: 10}\n"
                    "  m_LocalPosition: {x: 7, y: 7, z: 0}\n"
                    "--- !u!114 &12\nMonoBehaviour:\n  m_GameObject: {fileID: 10}\n"
                    "  m_Script: {fileID: 11500000, guid: %032x}\n"
                    "  life: 5\n" % 1)
        with open(os.path.join(root, "Assets", "Cloud.prefab.meta"), "w") as f:
            f.write("guid: %032x\n" % 99)
        out = run_frames(self, pack(self, root), 6)
        self.assertEqual([l for l in out if l.startswith("cloud")],
                         ["cloud 3 2 5", "cloud 6 2 5"])
        # `Destroy(gameObject, 0.04f)`: gone after its 2nd or 3rd Update
        gone = [l for l in out if l.startswith("gone")]
        self.assertEqual(len(gone), 2, out)
        self.assertTrue(all(l in ("gone 2", "gone 3") for l in gone), out)


class TestSpriteRendererColor(unittest.TestCase):
    """`sr.color = sr.color.SetAlpha(a)` through a SpriteRenderer field
    (Slime Jump's dust cloud fade): the authored tint, then the set one."""

    @needs_cc
    def test_fade(self):
        ext = ("using UnityEngine;\npublic static class ColorExtensions {\n"
               "    public static Color SetAlpha(this Color c, float a) {\n"
               "        return new Color(c.r, c.g, c.b, a);\n    }\n}\n")
        mgr = ("using UnityEngine;\npublic class Mgr : MonoBehaviour {\n"
               "    public SpriteRenderer sr;\n    int f;\n"
               "    void Update() {\n        f++;\n"
               "        Debug.Log(\"a\" + f + \" \" + Mathf.RoundToInt("
               "sr.color.a * 100f) + \" \" + Mathf.RoundToInt(sr.color.g * 100f));\n"
               "        sr.color = sr.color.SetAlpha(0.25f);\n    }\n}\n")
        spr = ("--- !u!212 &{fid}\nSpriteRenderer:\n  m_GameObject: {{fileID: {go}}}\n"
               "  m_Enabled: 1\n  m_Color: {{r: 1, g: 0.5, b: 1, a: 0.5}}\n"
               "  m_Sprite: {{fileID: 0}}\n")
        root = project(self, {"ColorExtensions": ext, "Mgr": mgr}, [
            ("Mgr", spr, "  sr: {fileID: 103}\n")])
        out = run_frames(self, pack(self, root), 2)
        self.assertIn("a1 50 50", out)
        self.assertIn("a2 25 50", out)


class TestHandleEulerZ(unittest.TestCase):
    """`t.eulerAngles += Vector3.forward * d` and `t.eulerAngles =
    Vector3.zero` through a Transform field (Slime Jump's lasso swing)."""

    @needs_cc
    def test_step_and_reset(self):
        mgr = ("using UnityEngine;\npublic class Mgr : MonoBehaviour {\n"
               "    public Transform other;\n    int f;\n    void Update() {\n"
               "        f++;\n        if (f <= 2) other.eulerAngles += Vector3.forward * 30f / 2f * 3f;\n"
               "        if (f == 3) other.eulerAngles = Vector3.zero;\n    }\n}\n")
        log = ("using UnityEngine;\npublic class Log : MonoBehaviour {\n"
               "    int f;\n    void LateUpdate() {\n        f++;\n"
               "        Debug.Log(\"z\" + f + \" \" + Mathf.RoundToInt(transform.eulerAngles.z));\n"
               "    }\n}\n")
        root = project(self, {"Mgr": mgr, "Log": log}, [
            ("Mgr", None, "  other: {fileID: 111}\n"), ("Log",)])
        out = run_frames(self, pack(self, root), 3)
        for want in ("z1 45", "z2 90", "z3 0"):
            self.assertIn(want, out)

    @needs_cc
    def test_through_singleton(self):
        """`Hook.instance.trs.eulerAngles += ..` (`Lasso.instance.hookTrs`)."""
        hook = ("using UnityEngine;\npublic class Hook : MonoBehaviour {\n"
                "    public static Hook instance;\n    public Transform trs;\n"
                "    void Awake() { instance = this; }\n}\n")
        mgr = ("using UnityEngine;\npublic class Mgr : MonoBehaviour {\n"
               "    int f;\n    void Update() {\n        f++;\n"
               "        if (f <= 2) Hook.instance.trs.eulerAngles += "
               "Vector3.forward * 30f;\n    }\n}\n")
        log = ("using UnityEngine;\npublic class Log : MonoBehaviour {\n"
               "    int f;\n    void LateUpdate() {\n        f++;\n"
               "        Debug.Log(\"z\" + f + \" \" + Mathf.RoundToInt(transform.eulerAngles.z));\n"
               "    }\n}\n")
        root = project(self, {"Hook": hook, "Mgr": mgr, "Log": log}, [
            ("Mgr",), ("Hook", None, "  trs: {fileID: 121}\n"), ("Log",)])
        out = run_frames(self, pack(self, root), 2)
        for want in ("z1 30", "z2 60"):
            self.assertIn(want, out)


class TestDropAchievementsSounds(unittest.TestCase):
    """Slime Jump's StartJump: achievement and sound statements become `;`
    (lines kept); a HandleAchieve whose result is read stays."""

    def test_drop(self):
        src = ("void StartJump () {\n"
               "\tif (a) JumpAchievement.Instance.HandleAchieve ();\n"
               "\tJumpAchievement.JumpCount ++;\n"
               "\tAudioClip jumpSound = jumpSounds[Random.Range(0, 3)];\n"
               "\tSoundEffect s = AudioManager.instance.MakeSoundEffect("
               "jumpSound, Vector3.zero, v);\n"
               "\ts.audioSource.pitch = r.Get(Random.value);\n"
               "\ts.audioSource.spatialBlend = 0;\n"
               "\tbool b = WinAchievement.Instance.HandleAchieve();\n}\n")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            out = unity_pack._desugar_drop_achievements_sounds(src, "P.cs")
        self.assertEqual(out.count("\n"), src.count("\n"))
        self.assertIn("if (a) ;", out)
        for gone in ("JumpCount", "jumpSound", "audioSource", "SoundEffect"):
            self.assertNotIn(gone, out)
        self.assertIn("WinAchievement.Instance.HandleAchieve()", out)
        self.assertIn("lines 2, 3, 4, 5, 6, 7", err.getvalue())


class TestLocalNamedI(unittest.TestCase):
    """`for (int i = 0; ..) speed += 1f;`: the packed instance index is `i`,
    so the loop counter took its place and each pass bumped instance
    0, 1 (A0 11, A1 22) instead of this one twice."""

    @needs_cc
    def test_loop_counter_i(self):
        a = ("using UnityEngine;\npublic class A : MonoBehaviour {\n"
             "    public float speed;\n    int f;\n"
             "    void Update() { f++; if (f > 1) return;\n"
             "        for (int i = 0; i < 2; i++) speed += 1f;\n"
             "        Debug.Log(name + \" \" + speed); }\n}\n")
        root = project(self, {"A": a}, [("A", None, "  speed: 10\n"),
                                        ("A", None, "  speed: 20\n")])
        out = run_frames(self, pack(self, root), 1)
        self.assertIn("A0 12", out)
        self.assertIn("A1 22", out)


class TestStopSite(unittest.TestCase):
    """A runtime stop in a helper (`SpriteRenderer.bounds` of a renderer
    crust does not draw) names the C# call: script, line, column."""

    @needs_cc
    def test_helper_stop_has_site(self):
        a = ("using UnityEngine;\npublic class A : MonoBehaviour {\n"
             "    public SpriteRenderer sr;\n"
             "    void Update() {\n"
             "        float x = sr.bounds.extents.x;\n"
             "        Debug.Log(\"x \" + x);\n    }\n}\n")
        extra = ("--- !u!212 &{fid}\nSpriteRenderer:\n"
                 "  m_GameObject: {{fileID: {go}}}\n"
                 "  m_Sprite: {{fileID: 0}}\n")
        root = project(self, {"A": a}, [("A", extra, "  sr: {fileID: 103}\n")])
        out = pack(self, root)
        with open(os.path.join(out, "h.c"), "w") as f:
            f.write('#include "engine_draw.h"\nextern float Time_deltaTime;\n'
                    "int main(int c, char **v) { engine_apply_argv(c, v);\n"
                    "  Time_deltaTime = 1.f / 60.f; engine_tick(); return 0; }\n")
        exe = os.path.join(out, "h")
        r = subprocess.run([_CC, "-O1", "-w", "-I", out, "-o", exe,
                            os.path.join(out, "h.c"),
                            os.path.join(out, "engine.c"),
                            os.path.join(out, "data.c"), "-lm"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        run = subprocess.run([exe, "-logFile", "-"], capture_output=True,
                             text=True, timeout=60)
        self.assertEqual(run.returncode, 70, run.stderr[-2000:])
        self.assertIn("SpriteRenderer.bounds", run.stderr)
        self.assertIn("A.Update () (at Assets/Scripts/A.cs:5:19)", run.stderr)


class TestTwoScriptsOneGameObject(unittest.TestCase):
    """Slime Jump's Player GO also carries AffectedByVortex: every script
    on a GO was folded into the first one's object (fields merged, the
    second had no row, `player.affectedByVortex` was null)."""

    @needs_cc
    def test_second_script_is_its_own_row(self):
        a = ("using UnityEngine;\npublic class A : MonoBehaviour {\n"
             "    public float speed;\n    public B b;\n    int f;\n"
             "    void Update() { f++; if (f > 1) return;\n"
             "        Debug.Log(\"s \" + speed + \" \" + b.speed + \" \" + "
             "GetComponent<B>().speed); }\n}\n")
        b = ("using UnityEngine;\npublic class B : MonoBehaviour {\n"
             "    public float speed;\n"
             "    void Start() { Debug.Log(\"b start \" + speed); }\n}\n")
        extra = ("--- !u!114 &{fid}\nMonoBehaviour:\n"
                 "  m_GameObject: {{fileID: {go}}}\n"
                 "  m_Script: {{fileID: 11500000, guid: %032x}}\n"
                 "  speed: 7\n" % 2)
        root = project(self, {"A": a, "B": b}, [
            ("A", extra, "  speed: 3\n  b: {fileID: 103}\n")])
        out = run_frames(self, pack(self, root), 1)
        self.assertIn("b start 7", out)
        self.assertIn("s 3 7 7", out)


class TestNullFieldRead(unittest.TestCase):
    """`other.v` through a null reference (Slime Jump's unset
    `affectedByVortex.velocity`) read out of bounds; it is Unity's NRE.
    This pack has no script unwinding, so it stops (exit 70) before the
    `read` log."""

    @needs_cc
    def test_nre_stops(self):
        o = ("using UnityEngine;\npublic class O : MonoBehaviour {\n"
             "    public Vector2 v;\n}\n")
        a = ("using UnityEngine;\npublic class A : MonoBehaviour {\n"
             "    public O other;\n    int f;\n"
             "    void Update() {\n        f++;\n"
             "        Debug.Log(\"tick \" + f);\n"
             "        float x = other.v.x;\n"
             "        Debug.Log(\"read \" + x);\n    }\n}\n")
        root = project(self, {"O": o, "A": a}, [("A", None, "  other: {fileID: 0}\n"),
                                                ("O",)])
        out = pack(self, root)
        with open(os.path.join(out, "h.c"), "w") as f:
            f.write('#include "engine_draw.h"\nextern float Time_deltaTime;\n'
                    "int main(int c, char **v) { engine_apply_argv(c, v);\n"
                    "  Time_deltaTime = 1.f / 60.f;\n"
                    "  engine_tick(); engine_tick(); return 0; }\n")
        exe = os.path.join(out, "h")
        r = subprocess.run([_CC, "-O1", "-w", "-I", out, "-o", exe,
                            os.path.join(out, "h.c"),
                            os.path.join(out, "engine.c"),
                            os.path.join(out, "data.c"), "-lm"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        run = subprocess.run([exe, "-logFile", "-"], capture_output=True,
                             text=True, timeout=60)
        self.assertEqual(run.returncode, 70, run.stderr[-2000:])
        self.assertIn("NullReferenceException", run.stderr)
        self.assertIn("A.Update () (at Assets/Scripts/A.cs:8:19)", run.stderr)
        self.assertIn("tick 1", run.stdout)
        self.assertNotIn("read", run.stdout)


class TestMethodGroupStub(unittest.TestCase):
    """`Ev.Add(Show, t)` (Slime Jump's Achievement.OnAchieve) reached
    crust as an undeclared `Show`; it is a CS8000 stub."""

    def test_method_group(self):
        ev = ("using UnityEngine;\nusing System;\npublic class Ev : MonoBehaviour {\n"
              "    public static void Add(Action a, float t) { }\n}\n")
        a = ("using UnityEngine;\npublic class A : MonoBehaviour {\n"
             "    void Show() { }\n"
             "    void Update() { Ev.Add(Show, Time.time); }\n}\n")
        root = project(self, {"Ev": ev, "A": a}, [("A",)])
        out = tempfile.mkdtemp(prefix="upf-out-")
        self.addCleanup(shutil.rmtree, out, True)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            unity_pack.pack(root, out, force=True)
        self.assertIn("`A.Update` is not lowered yet (`Show`: Method group",
                      err.getvalue())


class TestVector2FieldAssign(unittest.TestCase):
    """A Vector2 field store was `set_x(..); set_y(..);`: an unbraced
    `if` / `else` around it did not compile (Slime Jump's `blasterLaunchVel
    *= ..`), and `v = new Vector2(v.y, v.x)` read the new x for y."""

    @needs_cc
    def test_swap_and_unbraced_else(self):
        src = ("using UnityEngine;\npublic class V : MonoBehaviour {\n"
               "    public Vector2 vel = new Vector2(1f, 2f);\n"
               "    bool flip = true;\n"
               "    void Update() {\n"
               "        if (flip)\n            vel = new Vector2(vel.y, vel.x);\n"
               "        else\n            vel *= 2f;\n        flip = false;\n"
               "        Debug.Log(\"v \" + Mathf.RoundToInt(vel.x) + \" \" + "
               "Mathf.RoundToInt(vel.y));\n    }\n}\n")
        root = project(self, {"V": src}, [("V", None, "  vel: {x: 1, y: 2}\n")])
        out = run_frames(self, pack(self, root), 2)
        self.assertIn("v 2 1", out)
        self.assertIn("v 4 2", out)


class TestSetWorldScaleOne(unittest.TestCase):
    """`trs.SetWorldScale(Vector3.one.SetX(x))` on a Transform field and
    `Hook.instance.trs.SetWorldScale(Vector3.one)` (Slime Jump's DoUpdate)."""

    @needs_cc
    def test_field_and_singleton(self):
        ext = ("using UnityEngine;\npublic static class Extensions {\n"
               "    public static Vector3 SetX(this Vector3 v, float x) "
               "{ v.x = x; return v; }\n"
               "    public static void SetWorldScale(this Transform t, Vector3 s)"
               " { t.localScale = s; }\n}\n")
        hook = ("using UnityEngine;\npublic class Hook : MonoBehaviour {\n"
                "    public static Hook instance;\n    public Transform trs;\n"
                "    void Awake() { instance = this; }\n}\n")
        mgr = ("using UnityEngine;\npublic class Mgr : MonoBehaviour {\n"
               "    public Transform colliderTrs;\n    public float xSize = 2f;\n"
               "    void Update() {\n"
               "        colliderTrs.SetWorldScale(Vector3.one.SetX(xSize));\n"
               "        Hook.instance.trs.SetWorldScale(Vector3.one);\n    }\n}\n")
        log = ("using UnityEngine;\npublic class Log : MonoBehaviour {\n"
               "    void LateUpdate() { Debug.Log(name + \" \" + Mathf.RoundToInt("
               "transform.localScale.x * 10f) + \" \" + Mathf.RoundToInt("
               "transform.localScale.y * 10f)); }\n}\n")
        root = project(self, {"Extensions": ext, "Hook": hook, "Mgr": mgr,
                              "Log": log}, [
            ("Mgr", None, "  colliderTrs: {fileID: 121}\n  xSize: 2\n"),
            ("Hook", None, "  trs: {fileID: 131}\n"), ("Log",), ("Log",)])
        out = run_frames(self, pack(self, root), 1)
        self.assertIn("Log2 20 10", out)
        self.assertIn("Log3 10 10", out)


class TestInheritedUpdatables(unittest.TestCase):
    """Slime Jump's update loop: `UpdateWhileEnabled.OnEnable` registers in
    `GM.updatables`, and the pack dispatches `DoUpdate` on each. A class
    inherited what it ran (`GCam : Cam : Single<Cam> : UWE`): a base was
    its own packed array, so GCam had no OnEnable / DoUpdate, `base.M()`
    was dropped, and a generic base's header named no base at all."""

    SCRIPTS = {
        "IUpdatable": "public interface IUpdatable { void DoUpdate(); }\n",
        "GM": """using UnityEngine;
public class GM : MonoBehaviour {
    public static IUpdatable[] updatables = new IUpdatable[0];
    void Update() {
        for (int i = 0; i < updatables.Length; i++) { IUpdatable u = updatables[i]; u.DoUpdate(); }
        Debug.Log("gm");
    }
}
""",
        "UWE": """using UnityEngine;
public class UWE : MonoBehaviour, IUpdatable {
    public virtual void OnEnable() { GM.updatables = GM.updatables.Add(this); }
    public virtual void DoUpdate() { }
}
""",
        "Single": """using UnityEngine;
public class Single<T> : UWE where T : UWE {
    public bool persistant;
    public virtual void Awake() { Debug.Log("single " + persistant); }
}
""",
        "Cam": """using UnityEngine;
public class Cam : Single<Cam> {
    public override void DoUpdate() { HandlePosition(); }
    public virtual void HandlePosition() { Debug.Log("cam pos"); }
}
""",
        "GCam": """using UnityEngine;
public class GCam : Cam {
    public override void Awake() { base.Awake(); Debug.Log("gcam awake"); }
    public override void HandlePosition() { Debug.Log("gcam pos"); base.HandlePosition(); }
}
""",
    }

    @needs_cc
    def test_inherited_members_run(self):
        root = project(self, self.SCRIPTS,
                       [("GM",), ("GCam", None, "  persistant: 1\n")])
        # GM.Update's loop leaves its body: engine_tick runs that dispatch
        lines = run_frames(self, pack(self, root), frames=2)
        self.assertEqual(lines[:2], ["single True", "gcam awake"])
        self.assertEqual(lines.count("gm"), 2)
        self.assertEqual(lines.count("gcam pos"), 2)
        self.assertEqual(lines.count("cam pos"), 2)



class TestOtherPosition(unittest.TestCase):
    """`target.position` through a Transform field, read as a vector value:
    it stopped at a member of the field's read and emptied the method
    (`target.position.x` was already lowered)."""

    @needs_cc
    def test_reads_the_other_objects_position(self):
        watcher = script("W", "Vector2 p = target.position; "
                              "Vector2 d = Vector2.MoveTowards(transform.position, target.position, 0.25f); "
                              'Debug.Log("t " + p.x + " " + p.y + " m " + d.x);',
                         members="    public Transform target;")
        dummy = script("D", "")
        # object 1 (D) sits at x = 1; its Transform is fileID 111
        root = project(self, {"W": watcher, "D": dummy},
                       [("W", None, "  target: {fileID: 111}\n"), ("D",)])
        lines = run_frames(self, pack(self, root))
        self.assertIn("t 1 0 m 0.25", lines)




class TestParentRotationScale(unittest.TestCase):
    """A child's world position is parent * (T R S): its offset turned by
    the parent's world rotation and stretched by its scale. It was the
    parents' positions summed -- a rotated or scaled parent left its
    children where an unrotated one would."""

    def _scene(self, rot_deg, scale):
        import math
        q = (0.0, 0.0, math.sin(math.radians(rot_deg) / 2), math.cos(math.radians(rot_deg) / 2))
        src = script("Kid", 'Debug.Log("w " + transform.position.x + " " + transform.position.y);')
        root = tempfile.mkdtemp(prefix="upf-xf-")
        self.addCleanup(shutil.rmtree, root, True)
        d = os.path.join(root, "Assets", "Scripts")
        os.makedirs(d)
        with open(os.path.join(d, "Kid.cs"), "w") as f:
            f.write(src)
        with open(os.path.join(d, "Kid.cs.meta"), "w") as f:
            f.write("guid: %032x\n" % 1)
        scene = ("%%YAML 1.1\n--- !u!1 &10\nGameObject:\n  m_Name: Parent\n  m_Component:\n"
                 "  - component: {fileID: 11}\n--- !u!4 &11\nTransform:\n  m_GameObject: {fileID: 10}\n"
                 "  m_LocalRotation: {x: 0, y: 0, z: %r, w: %r}\n  m_LocalPosition: {x: 3, y: 0, z: 0}\n"
                 "  m_LocalScale: {x: %r, y: %r, z: 1}\n  m_Children:\n  - {fileID: 21}\n  m_Father: {fileID: 0}\n"
                 "--- !u!1 &20\nGameObject:\n  m_Name: Kid\n  m_Component:\n  - component: {fileID: 21}\n"
                 "  - component: {fileID: 22}\n--- !u!4 &21\nTransform:\n  m_GameObject: {fileID: 20}\n"
                 "  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}\n  m_LocalPosition: {x: 1, y: 0, z: 0}\n"
                 "  m_LocalScale: {x: 1, y: 1, z: 1}\n  m_Children: []\n  m_Father: {fileID: 11}\n"
                 "--- !u!114 &22\nMonoBehaviour:\n  m_GameObject: {fileID: 20}\n"
                 "  m_Script: {fileID: 11500000, guid: %032x}\n" % (q[2], q[3], scale, scale, 1))
        os.makedirs(os.path.join(root, "Assets", "Scenes"))
        with open(os.path.join(root, "Assets", "Scenes", "S.unity"), "w") as f:
            f.write(scene)
        lines = run_frames(self, pack(self, root))
        return [float(v) for v in [l for l in lines if l.startswith("w ")][0].split()[1:]]

    @needs_cc
    def test_unity_semantics(self):
        # the parent at (3, 0); the child at local (1, 0)
        for rot, scale, want in ((0, 1, (4, 0)), (90, 1, (3, 1)),
                                 (0, 2, (5, 0)), (90, 2, (3, 2))):
            x, y = self._scene(rot, scale)
            self.assertAlmostEqual(x, want[0], places=4, msg=(rot, scale))
            self.assertAlmostEqual(y, want[1], places=4, msg=(rot, scale))


DESTRUCTION = os.environ.get("UNITY_2D_DESTRUCTION") or os.path.join(
    os.path.dirname(ROOT), "Unity-2D-Destruction")
needs_destruction = unittest.skipUnless(
    os.path.isdir(DESTRUCTION),
    "Unity-2D-Destruction not found: clone https://github.com/crustos/"
    "Unity-2D-Destruction beside crust (or set UNITY_2D_DESTRUCTION)")


@needs_destruction
class TestUnity2DDestruction(unittest.TestCase):
    """The crust port of Unity-2D-Destruction (its runtime scripts; the
    Editor code is not part of it). The files that translate stay
    translating; every other one is refused with a diagnostic, never a
    crash of the translator -- so the port's frontier is measured, and
    moves only forward. See UNITY_PACK.md, "Unity-2D-Destruction"."""

    BASE = "unity2DDestruction/Assets/2D_Destruction"
    TRANSLATE = (
        "Scripts/MaxInstancesAttribute.cs",
        "Unity-delaunay/Delaunay/Edge.cs",
        "Unity-delaunay/Delaunay/ICoord.cs",
        "Unity-delaunay/Delaunay/LR.cs",
        "Unity-delaunay/geom/Circle.cs",
        "Unity-delaunay/geom/LineSegment.cs",
        "Unity-delaunay/geom/Polygon.cs",
        "Unity-delaunay/geom/Winding.cs",
        "Unity-delaunay/utils/IDisposable.cs",
    )

    def _runtime_files(self):
        import glob
        base = os.path.join(DESTRUCTION, self.BASE)
        return sorted(f for f in glob.glob(os.path.join(base, "**", "*.cs"),
                                           recursive=True)
                      if os.sep + "Editor" + os.sep not in f)

    def test_these_translate(self):
        import tools.csrust as csrust
        for rel in self.TRANSLATE:
            path = os.path.join(DESTRUCTION, self.BASE, rel)
            with open(path, encoding="utf-8-sig") as f:
                csrust.translate(f.read(), path=os.path.basename(path))

    def test_the_rest_is_refused_not_crashed(self):
        import tools.csrust as csrust
        import tools.cs2cpp as cs2cpp
        import tools.cpprust as cpprust
        for path in self._runtime_files():
            with open(path, encoding="utf-8-sig") as f:
                src = f.read()
            try:
                csrust.translate(src, path=os.path.basename(path))
            except (cs2cpp.CsError, cpprust.CppError):
                pass                    # a refusal, with a line and a reason


import tools.unity_pack_hybrid as unity_pack_hybrid  # noqa: E402

_hybrid_ok, _hybrid_why = unity_pack_hybrid.available()
needs_hybrid = unittest.skipUnless(_hybrid_ok, "--hybrid needs: " + _hybrid_why)


@needs_cc
@needs_hybrid
class TestHybrid(unittest.TestCase):
    """--hybrid: a script method the packer cannot lower runs as managed code on DotNetAnywhere, from its own C# source, over the same packed state.

    Without it such a method is an empty stub and a CS8000 warning (the player prints `total=0` below).  With it the method runs: it reads
    what native code wrote and native code reads what it wrote.  And a class whose managed code cannot be built keeps its stub: --hybrid never
    changes what a pack that worked did."""

    SCENE = (
        "%%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: Spark\n  m_Component:\n"
        "  - component: {fileID: 2}\n  - component: {fileID: 3}\n"
        "--- !u!4 &2\nTransform:\n  m_GameObject: {fileID: 1}\n"
        "  m_LocalPosition: {x: 0, y: 0, z: 0}\n"
        "--- !u!114 &3\nMonoBehaviour:\n  m_GameObject: {fileID: 1}\n"
        "  m_Script: {fileID: 11500000, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa}\n%s")

    def _project(self, script, values="  hp: 5\n  speed: 3\n"):
        root = tempfile.mkdtemp(prefix="upack-hy-")
        self.addCleanup(shutil.rmtree, root, True)
        s = os.path.join(root, "Assets", "Scripts")
        os.makedirs(s)
        with open(os.path.join(s, "Spark.cs"), "w") as f:
            f.write(script)
        with open(os.path.join(s, "Spark.cs.meta"), "w") as f:
            f.write("guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
        sc = os.path.join(root, "Assets", "Scenes")
        os.makedirs(sc)
        with open(os.path.join(sc, "S.unity"), "w") as f:
            f.write(self.SCENE % values)
        return root

    def _pack(self, root, **kw):
        out = tempfile.mkdtemp(prefix="upack-hy-out-")
        self.addCleanup(shutil.rmtree, out, True)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                unity_pack.pack(root, out, force=True, **kw)
        except unity_pack.PackError as e:
            e.stderr = err.getvalue()
            raise
        return out, err.getvalue()

    def _play(self, out, frames=4):
        """link the player (DotNetAnywhere in it, if the pack is hybrid) and run `frames` ticks from another directory: it must find its
        managed assembly and corlib.dll beside itself, wherever it is started"""
        with open(os.path.join(out, "harness.c"), "w") as f:
            f.write('#include "engine_draw.h"\nextern float Time_deltaTime;\n'
                    "int main(int argc, char **argv) { int f;\n"
                    "  engine_apply_argv(argc, argv); Time_deltaTime = 1.f / 60.f;\n"
                    "  for (f = 1; f <= %d; f++) engine_tick(); return 0; }\n" % frames)

        def run(cmd):
            r = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        objs, libs = unity_pack_hybrid.link_inputs(out, _CC, run, lambda m: None)
        exe = os.path.join(out, "h")
        run([_CC, "-O0", "-w", "-I", out, "-o", exe, os.path.join(out, "harness.c"),
             os.path.join(out, "engine.c"), os.path.join(out, "data.c")] + objs + libs + ["-lm"])
        other = tempfile.mkdtemp(prefix="upack-hy-cwd-")
        self.addCleanup(shutil.rmtree, other, True)
        r = subprocess.run([exe, "-logFile", "-"], cwd=other, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-1000:])
        return r.stdout.split("\n")[:-1]

    def _script(self, members):
        return "using UnityEngine;\nusing System;\npublic class Spark : MonoBehaviour {\n" + members + "\n}\n"

    # Update is lowered to C; Tally has a lambda, which the lowering cannot take.  hp starts at 5 and Update takes one off first.
    TALLY = """
    public int hp = 3;
    public float speed = 2f;
    public int total;
    void Update() {
        transform.position += new Vector3(speed * Time.deltaTime, 0, 0);
        hp -= 1;
        Tally();
        Debug.Log("hp=" + hp + " total=" + total);
    }
    public void Tally() {
        Func<int, int> sq = x => x * x + hp;
        total = sq(3) + sq(4);
    }"""

    def test_a_stub_runs_as_managed_code_over_the_packed_state(self):
        out, err = self._pack(self._project(self._script(self.TALLY)), hybrid=True)
        self.assertNotIn("CS8000", err)
        self.assertIn("hybrid: 1 managed method(s)", err)
        # sq(3) + sq(4) = 25 + 2 * hp: native Update's hp, read by managed code; its total, read by native code
        self.assertEqual(self._play(out), ["hp=4 total=33", "hp=3 total=31", "hp=2 total=29", "hp=1 total=27"])

    def test_without_hybrid_it_is_still_a_stub(self):
        out, err = self._pack(self._project(self._script(self.TALLY)))
        self.assertIn("warning CS8000", err)
        self.assertIn("`Spark.Tally` is not lowered yet", err)
        for name in ("hybrid_glue.c", "hybrid.managed.dll", "hybrid.ffi.json", "hybrid_generated.cs"):
            self.assertFalse(os.path.exists(os.path.join(out, name)), name)
        self.assertEqual(self._play(out), ["hp=4 total=0", "hp=3 total=0", "hp=2 total=0", "hp=1 total=0"])

    def test_a_pack_with_nothing_to_stub_has_no_hybrid_pieces(self):
        out, err = self._pack(self._project(self._script(
            "    public int hp;\n    void Update() { hp += 1; Debug.Log(\"hp=\" + hp); }")), hybrid=True)
        self.assertNotIn("hybrid", err)
        for name in ("hybrid_glue.c", "hybrid.managed.dll", "hybrid.ffi.json"):
            self.assertFalse(os.path.exists(os.path.join(out, name)), name)

    def test_managed_code_that_does_not_compile_keeps_its_stub(self):
        # `Foo` is not in the managed UnityEngine: the class does not build, so it is exactly what a plain pack gives, with the reason added
        root = self._project(self._script(
            "    public int hp = 3;\n    public int total;\n"
            "    void Update() { hp -= 1; Tally(); Debug.Log(\"hp=\" + hp + \" total=\" + total); }\n"
            "    public void Tally() { total = Foo.Bar(hp); }"))
        out, err = self._pack(root, hybrid=True)
        self.assertIn("warning CS8000", err)
        self.assertIn("hybrid: the managed code does not compile", err)
        self.assertFalse(os.path.exists(os.path.join(out, "hybrid_glue.c")))
        self.assertEqual(self._play(out, 2), ["hp=4 total=0", "hp=3 total=0"])

    def test_strict_is_met_when_managed_code_takes_the_method_and_refused_when_not(self):
        out, err = self._pack(self._project(self._script(self.TALLY)), hybrid=True, strict=True)     # no error: nothing is dropped
        self.assertNotIn("CS8000", err)
        root = self._project(self._script("    public int hp;\n    public void Update() { hp = Foo.Bar(hp); }"))
        with self.assertRaises(unity_pack.PackError) as cm:
            self._pack(root, hybrid=True, strict=True)
        self.assertIn("error CS8000", cm.exception.message)
        self.assertIn("hybrid:", cm.exception.message)

    def test_a_field_the_engine_stores_out_of_reach_is_not_copied(self):
        # `dir` is a Vector2, which the packed engine keeps in two arrays managed code has no accessor for: a managed copy would silently
        # disagree with it, so the class stays a stub
        root = self._project(self._script(
            "    public Vector2 dir;\n    public int total;\n"
            "    void Update() { Tally(); Debug.Log(\"total=\" + total); }\n"
            "    public void Tally() { Func<float, int> f = v => (int)(v * 10f); total = f(dir.x); }"),
            values="  dir: {x: 2, y: 0}\n")
        out, err = self._pack(root, hybrid=True)
        self.assertIn("warning CS8000", err)
        self.assertIn("`Vector2 dir`", err)
        self.assertFalse(os.path.exists(os.path.join(out, "hybrid_glue.c")))

    def test_managed_code_calls_a_lowered_method_and_uses_bool_and_float_fields(self):
        root = self._project(self._script("""
    public int hp = 3;
    public int total;
    public float speed = 2f;
    public bool alive = true;
    void Update() { Tally(); Debug.Log("hp=" + hp + " total=" + total + " alive=" + (alive ? 1 : 0)); }
    public void Bump(int by) { hp += by; }
    public void Tally() {
        Func<int, int> twice = n => n * 2;
        Bump(twice(2));
        total = hp * 10;
        if (hp > 8) alive = false;
    }"""), values="  hp: 5\n  speed: 3\n  alive: 1\n")
        out, err = self._pack(root, hybrid=True)
        self.assertNotIn("CS8000", err)
        # each tick: Bump(4) is a lowered method called from managed code; alive goes false once hp passes 8
        self.assertEqual(self._play(out, 3), ["hp=9 total=90 alive=0", "hp=13 total=130 alive=0", "hp=17 total=170 alive=0"])

    def test_managed_code_may_index_an_array_with_a_uint_or_a_long(self):
        # an unsigned or wide index is a conv.u / conv.i before the element opcode: DotNetAnywhere used to leave its evaluation stack misaligned
        # and crash the player (fixed in its JIT, JIT_NARROW_INDEX_BELOW), where this method must simply run
        root = self._project(self._script("""
    public int total;
    void Update() { Tally(); Debug.Log("total=" + total); }
    public void Tally() {
        int[] xs = new int[4];
        Func<int, int> sq = n => n * n;
        for (uint k = 0; k < 4; k++) xs[k] = sq((int)k + 1);
        uint j = 2; long m = 3;
        total = xs[j] * 100 + xs[m];
    }"""), values="  total: 0\n")
        out, err = self._pack(root, hybrid=True)
        self.assertNotIn("CS8000", err)
        self.assertEqual(self._play(out, 2), ["total=916", "total=916"])

    @unittest.skipUnless(shutil.which("make"), "needs make")
    def test_make_builds_a_hybrid_player_that_runs_its_managed_assembly(self):
        out, err = self._pack(self._project(self._script(self.TALLY)), hybrid=True)
        mk = subprocess.run(["make", "-C", out, "game", "CC=" + _CC, "DNA_BUILD=" + unity_pack_hybrid.build_dir()],
                            capture_output=True, text=True)
        self.assertEqual(mk.returncode, 0, mk.stdout[-800:] + mk.stderr[-1200:])
        game = os.path.join(out, "game")
        for name in ("corlib.dll", "hybrid.managed.dll"):
            self.assertTrue(os.path.exists(os.path.join(out, name)), name)
        other = tempfile.mkdtemp(prefix="upack-hy-cwd-")
        self.addCleanup(shutil.rmtree, other, True)
        r = subprocess.run([game], cwd=other, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        self.assertIn("ticks=60", r.stdout)
        # Update calls the managed Tally on every tick, so a player that cannot load its assembly must stop, saying why
        env = dict(os.environ, UNITY_PACK_MANAGED_DLL=os.path.join(other, "missing.dll"))
        r = subprocess.run([game], cwd=other, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 70)
        self.assertIn("cannot load the managed assembly", r.stderr)
        # and `make clean` takes what it built away
        subprocess.run(["make", "-C", out, "clean"], capture_output=True, text=True)
        self.assertFalse(os.path.exists(game))
        self.assertFalse(os.path.exists(os.path.join(out, "hybrid_glue.o")))

    def test_without_the_toolchain_the_stub_is_reported_with_the_reason(self):
        saved = os.environ.get("DNA_HOME")
        os.environ["DNA_HOME"] = os.path.join(tempfile.gettempdir(), "no-such-dotnetanywhere")
        try:
            out, err = self._pack(self._project(self._script(self.TALLY)), hybrid=True)
        finally:
            if saved is None:
                del os.environ["DNA_HOME"]
            else:
                os.environ["DNA_HOME"] = saved
        self.assertIn("warning CS8000", err)
        self.assertIn("hybrid: DotNetAnywhere not found", err)
        self.assertFalse(os.path.exists(os.path.join(out, "hybrid_glue.c")))


class TestLayerMaskAwake(unittest.TestCase):
    """Slime Jump's Player.Awake: `enabled` of an authored-enabled script,
    `Physics2D.GetLayerCollisionMask(gameObject.layer)` from the authored
    matrix, and the LayerMaskExtensions `Remove` (an unknown name is bit 31,
    `1 << -1`). Layer 4's word is 0x800000f0; less Water (4) and bit 31."""

    @needs_cc
    def test_mask_remove(self):
        a = ("using UnityEngine;\nusing Extensions;\n"
             "public class A : MonoBehaviour {\n    LayerMask m;\n"
             "    void Start() {\n        if (!enabled) return;\n"
             "        m = Physics2D.GetLayerCollisionMask(gameObject.layer);\n"
             "        m = m.Remove(\"Water\", \"Nope\");\n"
             "        Debug.Log(\"m \" + (int)m + \" \" + gameObject.layer);\n"
             "    }\n}\n")
        # Slime Jump's LayerMaskExtensions, the two methods used
        ext = ("using UnityEngine;\nnamespace Extensions\n{\n"
               "\tpublic static class LayerMaskExtensions\n\t{\n"
               "\t\tpublic static LayerMask FromLayerNames (params string[] layerNames)\n"
               "\t\t{\n\t\t\tLayerMask ret = (LayerMask) 0;\n"
               "\t\t\tforeach (string name in layerNames)\n"
               "\t\t\t\tret |= (1 << LayerMask.NameToLayer(name));\n"
               "\t\t\treturn ret;\n\t\t}\n"
               "\t\tpublic static LayerMask Remove (this LayerMask original, params string[] layerNames)\n"
               "\t\t{\n\t\t\tLayerMask invertedOriginal = ~original;\n"
               "\t\t\treturn ~(invertedOriginal | FromLayerNames(layerNames));\n"
               "\t\t}\n\t}\n}\n")
        root = project(self, {"A": a, "LayerMaskExtensions": ext}, [("A",)])
        scene = os.path.join(root, "Assets", "Scenes", "S.unity")
        with open(scene) as f:
            text = f.read().replace("GameObject:\n", "GameObject:\n  m_Layer: 4\n")
        with open(scene, "w") as f:
            f.write(text)
        os.makedirs(os.path.join(root, "ProjectSettings"))
        with open(os.path.join(root, "ProjectSettings",
                               "Physics2DSettings.asset"), "w") as f:
            f.write("  m_LayerCollisionMatrix: %s\n"
                    % ("ffffffff" * 4 + "f0000080" + "ffffffff" * 27))
        out = run_frames(self, pack(self, root), 1)
        self.assertIn("m 224 4", out)

    def test_written_enabled_refused(self):
        a = ("using UnityEngine;\npublic class A : MonoBehaviour {\n"
             "    void Start() { if (!enabled) return; Debug.Log(\"on\"); }\n"
             "    void OnDisable() { enabled = true; }\n}\n")
        root = project(self, {"A": a}, [("A",)])
        with self.assertRaises(unity_pack.PackError):
            pack(self, root)


def _run_rc(test, out, frames=1):
    """Like run_frames, but a stop is the result: (exit code, stdout,
    stderr)."""
    src = os.path.join(out, "h.c")
    with open(src, "w") as f:
        f.write('#include "engine_draw.h"\nextern float Time_deltaTime;\n'
                "int main(int c, char **v) { int f; engine_apply_argv(c, v);\n"
                "  Time_deltaTime = 1.f / 60.f;\n"
                "  for (f = 0; f < %d; f++) engine_tick(); return 0; }\n"
                % frames)
    exe = os.path.join(out, "h")
    r = subprocess.run([_CC, "-O1", "-w", "-I", out, "-o", exe, src,
                        os.path.join(out, "engine.c"),
                        os.path.join(out, "data.c"), "-lm"],
                       capture_output=True, text=True)
    test.assertEqual(r.returncode, 0, r.stderr[-2000:])
    run = subprocess.run([exe, "-logFile", "-"], capture_output=True,
                         text=True, timeout=60)
    return run.returncode, run.stdout, run.stderr


class TestPlayerAwakeItems(unittest.TestCase):
    """Slime Jump's Player.Awake item loop, over a `Gear` class: a field
    array stored from GetComponentsInChildren<Gear>,
    `item.gameObject.activeSelf` and `item.OnGain(this)` on a typed local.
    The result holds bare Gear row indices, so finding a subclass stops
    instead of misreading it. Named `Item` it is Slime Jump's item system,
    out of scope: the store goes and the loop runs no pass."""

    A = ("using UnityEngine;\npublic class A : MonoBehaviour {\n"
         "    public Transform itemsParent;\n    public int got;\n"
         "    Item[] items;\n    void Awake() {\n"
         "        items = itemsParent.GetComponentsInChildren<Item>();\n"
         "        for (int i = 0; i < items.Length; i ++) {\n"
         "            Item item = items[i];\n"
         "            if (item.gameObject.activeSelf)\n"
         "                item.OnGain (this);\n        }\n"
         "        Debug.Log(\"got \" + got);\n    }\n}\n")
    ITEM = ("using UnityEngine;\npublic class Item : MonoBehaviour {\n"
            "    public int v;\n"
            "    public virtual void OnGain (A a) { a.got += v; }\n}\n")

    def _root(self, scripts, child):
        root = project(self, scripts, [("A", None, "  itemsParent: {fileID: 101}\n"),
                                       (child, None, "  v: 5\n")])
        sc = os.path.join(root, "Assets", "Scenes", "S.unity")
        with open(sc) as f:
            text = f.read().replace(
                "Transform:\n  m_GameObject: {fileID: 110}\n",
                "Transform:\n  m_GameObject: {fileID: 110}\n"
                "  m_Father: {fileID: 101}\n")
        with open(sc, "w") as f:
            f.write(text)
        return root

    def _gear(self, extra=()):
        s = {"A": self.A.replace("Item", "Gear"),
             "Gear": self.ITEM.replace("Item", "Gear")}
        s.update(extra)
        return s

    @needs_cc
    def test_items_loop(self):
        root = self._root(self._gear(), "Gear")
        self.assertIn("got 5", run_frames(self, pack(self, root), 1))

    @needs_cc
    def test_item_system_dropped(self):
        root = self._root({"A": self.A, "Item": self.ITEM}, "Item")
        self.assertIn("got 0", run_frames(self, pack(self, root, False), 1))

    @needs_cc
    def test_subclass_found_stops(self):
        b = ("using UnityEngine;\npublic class Blaster : Gear {\n"
             "    public override void OnGain (A a) { a.got += 100; }\n}\n")
        root = self._root(self._gear({"Blaster": b}), "Blaster")
        rc, out, err = _run_rc(self, pack(self, root, strict=False))
        self.assertEqual(rc, 70, out + err)
        self.assertIn("GetComponentsInChildren<Gear> found a Blaster", err)
        self.assertIn("A.Awake () (at Assets/Scripts/A.cs:7:", err)
        self.assertNotIn("got", out)


class TestChildColliderBody(unittest.TestCase):
    """Slime Jump's player: its collider is on a child GameObject with no
    Rigidbody2D, which Unity puts on the nearest ancestor's body -- left
    static, the player fell through the floor."""

    def test_nearest_ancestor_rb(self):
        import tools.unity_pack_physics as phys
        box = {"kind": "box", "enabled": 1}
        plan = {"rigidbody2d": [{"owner_class": "P", "owner_inst": 0,
                                 "body_type": 0}],
                "classes": {
                    "P": {"instances": [{"xf_id": "10"}]},
                    "Mid": {"instances": [{"xf_id": "20", "father_id": "10"}]},
                    "C": {"instances": [
                        {"xf_id": "30", "father_id": "20", "collider2d": box},
                        {"xf_id": "40", "collider2d": box}]}}}
        cols = phys._build_collider2d_tables(plan)
        self.assertEqual([(c["owner_inst"], c["rb2d"], c["body_type"])
                          for c in cols], [(0, 0, 0), (1, -1, 2)])


class TestSharedTransformRow(unittest.TestCase):
    """Slime Jump's Player GameObject also carries a `Test` script: its
    Graphics child followed the Test row, which nothing moves, so the
    sprite hung at the spawn while the Rigidbody2D fell."""

    def test_child_follows_rigidbody_row(self):
        plan = {"classes": {
            "Test": {"instances": [{"xf_id": "10"}]},
            "Player": {"instances": [{"xf_id": "10", "rigidbody2d": {"body_type": 0}}],
                       "fields": [{"name": "t", "ty": "Transform"}]},
            "Graphics": {"instances": [{"xf_id": "20", "father_id": "10"}]}}}
        plan["classes"]["Player"]["instances"][0]["object_refs"] = {"t": "10"}
        unity_pack._attach_transform_parents(plan)
        g = plan["classes"]["Graphics"]["instances"][0]
        self.assertEqual((g["xf_parent_class"], g["xf_parent_inst"]),
                         ("Player", 0))
        unity_pack._resolve_transform_field_targets(plan)
        self.assertEqual(plan["transform_field_targets"][("Player", "t")],
                         [(1, 0, "Player")])


class TestUnpackedClassCall(unittest.TestCase):
    """`g.Use();` on a class crust packs no methods of (Slime Jump's
    `World.Instance.SetPieces()`, `fallerObject.Awake()`): Unity's NRE for
    a null receiver, else a stop at the call -- not a stubbed method."""

    @needs_cc
    def test_null_receiver_nre(self):
        a = ("using UnityEngine;\npublic class A : MonoBehaviour {\n"
             "    public Gadget g;\n    int f;\n"
             "    void Update() {\n        f++; Debug.Log(\"tick \" + f);\n"
             "        g.Use ();\n    }\n}\n")
        g = ("using UnityEngine;\npublic class Gadget : MonoBehaviour {\n"
             "    public void Use () { Debug.Log(\"used\"); }\n}\n")
        root = project(self, {"A": a, "Gadget": g},
                       [("A", None, "  g: {fileID: 0}\n")])
        rc, out, err = _run_rc(self, pack(self, root, strict=False), 2)
        self.assertIn("tick 1", out)
        self.assertNotIn("used", out)
        self.assertIn("A.Update () (at Assets/Scripts/A.cs:7:9)", err)


class TestSpriteSwapDropped(unittest.TestCase):
    """`s = img.sprite;` (Player.Awake's toggle images): the pack draws
    each Image with its authored sprite, so the statement goes with a
    CS8000 warning -- but a null Image is still Unity's NRE, at its
    column."""

    @needs_cc
    def test_null_image_nre(self):
        a = ("using UnityEngine;\nusing UnityEngine.UI;\n"
             "public class A : MonoBehaviour {\n"
             "    public Image img;\n    Sprite s;\n"
             "    void Update() {\n"
             "        s = img.sprite;\n"
             "        Debug.Log(\"after\");\n    }\n}\n")
        root = project(self, {"A": a}, [("A", None, "  img: {fileID: 0}\n")])
        err = io.StringIO()
        out = tempfile.mkdtemp(prefix="upf-out-")
        self.addCleanup(shutil.rmtree, out, True)
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            unity_pack.pack(root, out, force=True)
        self.assertIn("A.cs(7,9): warning CS8000: sprite swaps", err.getvalue())
        rc, so, se = _run_rc(self, out)
        self.assertIn("A.Update () (at Assets/Scripts/A.cs:7:13)", se)
        self.assertNotIn("after", so)


if __name__ == "__main__":
    unittest.main()
