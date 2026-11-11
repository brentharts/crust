"""Goose in Crust: the front end (`tools/goose.py`), see GOOSE.md.

Phase 1a: the lexer and parser.  The samples of the Goose repository are the
corpus -- every one must parse -- and the unit tests pin the corners the
grammar sketch (spec Appendix D) calls out: `>>` in type argument lists,
`1..2` as a range, `.==`, the statement-ending rule for block-ended
constructs, `T&<u32 in pool.items>`, `f<T>(x)` versus `a < b`.

The samples live in a checkout of https://github.com/aardappel/goose, found
through $GOOSE_ROOT or a `goose` directory beside this repository; without
one those tests are skipped.
"""
import glob
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import tools.goose as G                                    # noqa: E402


def goose_root():
    for c in (os.environ.get("GOOSE_ROOT"), os.path.join(os.path.dirname(ROOT), "goose")):
        if c and os.path.isdir(os.path.join(c, "samples")):
            return c
    return None


def kinds(decls):
    return [d.kind for d in decls]


def body(src):
    """The statements of `fn main() { src }`."""
    d = G.parse("fn main() { %s }" % src)[0]
    return d.body


class TestLexer(unittest.TestCase):
    def toks(self, s):
        return [(t.kind, t.text) for t in G.lex(s)[:-1]]

    def test_range_is_not_a_float(self):
        self.assertEqual(self.toks("1..2"), [("int", "1"), ("op", ".."), ("int", "2")])

    def test_float_forms(self):
        v = [t.val for t in G.lex("1.5 2.5e-3 0x1.8p3 7")[:-1]]
        self.assertEqual(v, [1.5, 0.0025, 12.0, 7])

    def test_identity_ops(self):
        self.assertEqual([t for _, t in self.toks("a .== b .!= c .= d")],
                         ["a", ".==", "b", ".!=", "c", ".=", "d"])

    def test_nested_comments(self):
        self.assertEqual(self.toks("a /* x /* y */ z */ b"), [("ident", "a"), ("ident", "b")])

    def test_strings_and_chars(self):
        t = G.lex("\"a\\n\\x41\" 'h' '\\n'")
        self.assertEqual(t[0].val, b"a\nA")
        self.assertEqual(t[1].val, ord("h"))
        self.assertEqual(t[2].val, 10)

    def test_raw_string_multiline(self):
        src = 'let s = """\n    one\n      two\n    """;'
        t = [x for x in G.lex(src) if x.kind == "str"][0]
        self.assertEqual(t.val, b"one\n  two")

    def test_unterminated_string_has_line(self):
        with self.assertRaises(G.GooseError) as c:
            G.lex('let a = 1;\nlet s = "oops;\n')
        self.assertEqual(c.exception.line, 2)


class TestTypes(unittest.TestCase):
    def ty(self, text):
        d = G.parse("let x: %s;" % text)[0]
        return d.type

    def test_array_family(self):
        for text, mode in [("i32[4]", "fixed"), ("i32[]", "var"), ("i32[varint]", "var"),
                           ("i32[..8]", "limited"), ("i32[..]", "limited"),
                           ("i32[>..]", "grow"), ("i32[>..<]", "grow_shrink")]:
            t = self.ty(text)
            self.assertEqual((t.kind, t.mode), ("Array", mode), text)

    def test_slice_ref_opt(self):
        self.assertEqual(self.ty("u8[:]").kind, "Slice")
        self.assertEqual(self.ty("Node&").kind, "Ref")
        self.assertEqual(self.ty("Node?").kind, "Opt")

    def test_relative_reference(self):
        t = self.ty("Node&<u32 in pool.items>?")
        self.assertEqual(t.kind, "Opt")
        self.assertEqual(t.type.rel[0], "u32")
        self.assertEqual(t.type.rel[1].kind, "Field")

    def test_variable_mode_enum_in_array(self):
        t = self.ty("Shape..[>..]")
        self.assertEqual((t.kind, t.elem.kind), ("Array", "VarMode"))

    def test_nested_generic_closes_two_levels(self):
        t = self.ty("a<b<i32>>")
        self.assertEqual(t.args[0].args[0].name, "i32")

    def test_const_slice(self):
        self.assertEqual(self.ty("const u8[:]").kind, "Const")

    def test_index_type(self):
        t = self.ty("u32 in pool")
        self.assertEqual((t.kind, t.how), ("IndexType", "in"))

    def test_variant_type(self):
        t = self.ty("Shape.Circle")
        self.assertEqual((t.kind, t.variant), ("VariantType", "Circle"))


class TestExpressions(unittest.TestCase):
    def e(self, text):
        return body("let v = %s;" % text).stmts[0].inits[0]

    def test_precedence(self):
        e = self.e("1 + 2 * 3 << 1 & 7 == 7 && true")
        self.assertEqual(e.op, "&&")
        self.assertEqual(e.left.op, "==")

    def test_shift_and_compare_from_gt_tokens(self):
        self.assertEqual(self.e("a >> 2").op, ">>")
        self.assertEqual(self.e("a >= 2").op, ">=")
        self.assertEqual(self.e("a > 2").op, ">")

    def test_generic_call_versus_comparison(self):
        self.assertEqual(self.e("f<i32>(x)").fn.args[0].name, "i32")
        self.assertEqual(self.e("a < b").op, "<")
        # as in Goose: `f<` commits to type arguments when `(` follows the `>`
        self.assertEqual(self.e("a < b > (c)").kind, "Call")

    def test_slices(self):
        e = self.e("xs[^2..]")
        self.assertEqual((e.kind, e.lo.kind, e.hi), ("Slice", "FromEnd", None))
        e = self.e("xs[..4]")
        self.assertEqual((e.lo, e.hi.kind), (None, "Int"))
        self.assertEqual(self.e("xs[i]").kind, "Index")

    def test_struct_literals(self):
        e = self.e("Point { 1.0, 2.0 }")
        self.assertEqual((e.kind, len(e.inits), e.inits[0].name), ("StructLit", 2, None))
        e = self.e("Player { name: \"gus\", .. }")
        self.assertTrue(e.rest)
        e = self.e("Shape.Circle { r: 1.0 }")
        self.assertEqual(e.type.kind, "Field")

    def test_no_struct_literal_in_condition(self):
        s = body("if a == b { x(); }").value or body("if a == b { x(); }").stmts[0]
        self.assertEqual(s.kind if s.kind != "ExprStmt" else s.expr.kind, "If")

    def test_cast(self):
        e = self.e("x as! u8")
        self.assertEqual((e.kind, e.checked), ("Cast", False))
        self.assertTrue(self.e("x as u8").checked)

    def test_array_literals(self):
        self.assertEqual(self.e("[0; 8]").fill[1].value, 8)
        self.assertEqual(len(self.e("[1, 2, 3,]").items), 3)
        self.assertEqual(self.e("[..n + 5]").cap.kind, "Binary")

    def test_trailing_function_block(self):
        e = self.e("xs.filter() { it % 2 == 0 }")
        self.assertEqual(e.kind, "Call")
        self.assertIsNone(e.block.params)
        e = self.e("fold(xs, 0) { acc, x => acc + x }")
        self.assertEqual([p.name for p in e.block.params], ["acc", "x"])

    def test_match_patterns(self):
        e = self.e('match p { 0 => "a", 1..50 => "b", Circle c => "c", Rect &r => "d", _ => "e" }')
        self.assertEqual([a.pattern.kind for a in e.arms],
                         ["PItems", "PItems", "PVariant", "PVariant", "PWild"])
        self.assertTrue(e.arms[3].pattern.ref)


class TestStatements(unittest.TestCase):
    def test_block_ended_statement_stops_the_expression(self):
        # `foo(1) { it }` then `-x;` is two statements (spec section 2)
        b = body("foo(1) { it } -x;")
        self.assertEqual([s.expr.kind for s in b.stmts], ["Call", "Unary"])

    def test_trailing_value(self):
        b = body("let a = 1; a + 1")
        self.assertEqual(b.value.kind, "Binary")

    def test_assignments(self):
        b = body("a += 1; b .= c; d++; x, y = f();")
        self.assertEqual(kinds(b.stmts), ["Assign", "Assign", "IncDec", "MultiAssign"])
        self.assertEqual(b.stmts[1].op, ".=")

    def test_shift_assign(self):
        b = body("a >>= 2; b <<= 1;")
        self.assertEqual([s.op for s in b.stmts], [">>=", "<<="])

    def test_decl_forms(self):
        b = body("let a, b = f(); var c: i32 = 0; var r .= xs[0]; const k = 3;")
        self.assertEqual([s.mode for s in b.stmts], ["let", "var", "var", "const"])
        self.assertEqual(b.stmts[0].names, ["a", "b"])
        self.assertTrue(b.stmts[2].by_ref)

    def test_for_forms(self):
        b = body("for i in 3 {} for i in 1..4 {} for &x in xs {} for x, i in xs {} "
                 "for j: u32 in 0..10 {}")
        fs = [x.expr for x in b.stmts] + [b.value]
        self.assertIsNone(fs[0].hi)
        self.assertIsNotNone(fs[1].hi)
        self.assertTrue(fs[2].ref)
        self.assertEqual([n for n, _ in fs[3].binds], ["x", "i"])
        self.assertEqual(fs[4].binds[0][1].name, "u32")

    def test_guard_defer_return_from(self):
        b = body("guard ok else { return 1, 2 from load; } defer cleanup(); return from f;")
        self.assertEqual(kinds(b.stmts), ["Guard", "Defer", "ExprStmt"])
        self.assertEqual(b.stmts[0].els.stmts[0].expr.frm.name, "load")

    def test_block_expression_break_value(self):
        b = body('let v = block { guard ok else { break "gone"; } "standing" };')
        self.assertEqual(b.stmts[0].inits[0].kind, "BlockExpr")


class TestDeclarations(unittest.TestCase):
    def test_fn_forms(self):
        ds = G.parse("fn f(a: i64, var b: i64 = 2) -> i64, bool { a }\n"
                     "fn g<T, F>(xs: T[:]) -> T { xs[0] }\n"
                     "recursive fn h() {}\n"
                     "extern \"c\" { fn hypot(x: f64, y: f64) -> f64; }\n")
        self.assertEqual(kinds(ds), ["FnDecl", "FnDecl", "FnDecl", "ExternBlock"])
        self.assertEqual(len(ds[0].rets), 2)
        self.assertEqual([g[0] for g in ds[1].generics], ["T", "F"])
        self.assertIn("recursive", ds[2].quals)

    def test_struct_and_enum(self):
        ds = G.parse("struct P { x: f64, y: f64 = 1.0, pad 4 }\n"
                     "enum S { Circle { r: f64 }, Dot, }\n")
        self.assertEqual([f.kind for f in ds[0].fields], ["Field", "Field", "Pad"])
        self.assertEqual([v.name for v in ds[1].variants], ["Circle", "Dot"])
        self.assertIsNone(ds[1].variants[1].fields)

    def test_imports_and_namespace(self):
        ds = G.parse('namespace a;\nimport std;\nimport gfx from "owner/repo@v1";\n')
        self.assertEqual(kinds(ds), ["Namespace", "Import", "Import"])
        self.assertEqual(ds[2].source, "owner/repo@v1")

    def test_lines_are_kept(self):
        ds = G.parse("\n\nfn f() {}\n\nstruct S { a: i32 }\n")
        self.assertEqual([d.line for d in ds], [3, 5])


class TestRefusals(unittest.TestCase):
    def test_syntax_error_names_the_line(self):
        with self.assertRaises(G.GooseError) as c:
            G.parse("fn f() {\n  let a = ;\n}\n")
        self.assertEqual(c.exception.line, 2)

    def test_missing_brace(self):
        with self.assertRaises(G.GooseError) as c:
            G.parse("fn f() {\n  let a = 1;\n")
        self.assertIn("missing '}'", c.exception.msg)

    def test_cli(self):
        import subprocess
        p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "goose.py"), "/nonexistent.goose"],
                           capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)


@unittest.skipUnless(goose_root(), "no Goose checkout (set GOOSE_ROOT)")
class TestSamples(unittest.TestCase):
    def test_every_sample_parses(self):
        files = sorted(glob.glob(os.path.join(goose_root(), "samples", "*.goose")))
        self.assertGreaterEqual(len(files), 30)
        for f in files:
            with self.subTest(sample=os.path.basename(f)):
                with open(f, encoding="utf-8") as fh:
                    G.parse(fh.read(), f)

    def test_stdlib_parses(self):
        for f in sorted(glob.glob(os.path.join(goose_root(), "stdlib", "*.goose"))):
            with self.subTest(module=os.path.basename(f)):
                with open(f, encoding="utf-8") as fh:
                    G.parse(fh.read(), f)


if __name__ == "__main__":
    unittest.main()
