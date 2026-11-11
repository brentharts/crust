#!/usr/bin/env python3
"""goose -- front end for a Goose subset (see GOOSE.md).

    python3 tools/goose.py FILE.goose          # parse; print the declarations

Phase 1a: lexer and parser for the whole surface syntax of Goose
(https://github.com/aardappel/goose, spec Appendix D), so that every sample
parses. What the *subset* accepts is decided later, by the checker, which
refuses the rest in Goose terms with the line. Every node carries its line.
"""

import sys


class GooseError(Exception):
    def __init__(self, msg, line=0, path="<goose>"):
        super().__init__(msg)
        self.msg, self.line, self.path = msg, line, path

    def __str__(self):
        return "%s:%d: error: %s" % (self.path, self.line, self.msg)


# ---------------------------------------------------------------------------
# Lexer

class Tok:
    __slots__ = ("kind", "text", "line", "adj", "val")

    def __init__(self, kind, text, line, adj, val=None):
        self.kind, self.text, self.line, self.adj, self.val = kind, text, line, adj, val

    def __repr__(self):
        return "Tok(%s %r @%d)" % (self.kind, self.text, self.line)


# '>' is always lexed alone (with `adj`, "no whitespace before"), so that
# `Foo<Bar<T>>` closes two type lists and `a >> b`, `>=`, `>>=` are rebuilt by
# the parser where an operator is meant.
_OPS3 = (".==", ".!=", "<<=", "...")
_OPS2 = ("::", "=>", "->", ".=", "..", "==", "!=", "<=", "&&", "||", "++", "--",
         "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<")
_ESC = {"n": 10, "t": 9, "r": 13, "0": 0, "\\": 92, '"': 34, "'": 39}


def lex(src, path="<goose>"):
    if src.startswith("﻿"):
        src = src[1:]
    src = src.replace("\r\n", "\n")
    toks, i, n, line = [], 0, len(src), 1

    def err(msg):
        raise GooseError(msg, line, path)

    def esc(j):
        """src[j] is the char after a backslash; returns (value, next j)."""
        c = src[j]
        if c == "x":
            h = src[j + 1:j + 3]
            try:
                return int(h, 16), j + 3
            except ValueError:
                err("bad \\x escape")
        if c in _ESC:
            return _ESC[c], j + 1
        err("unknown escape \\%s" % c)

    while i < n:
        c = src[i]
        adj = not (i > 0 and src[i - 1] in " \t\n")
        if c == "\n":
            line += 1
            i += 1
        elif c in " \t":
            i += 1
        elif src.startswith("//", i):
            while i < n and src[i] != "\n":
                i += 1
        elif src.startswith("/*", i):
            depth, i, start = 1, i + 2, line
            while i < n and depth:
                if src.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif src.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    line += src[i] == "\n"
                    i += 1
            if depth:
                raise GooseError("unterminated comment", start, path)
        elif src.startswith('"""', i):
            start = line
            j = src.find('"""', i + 3)
            if j < 0:
                raise GooseError("unterminated raw string", start, path)
            body = src[i + 3:j]
            line += body.count("\n")
            if body.startswith("\n"):                  # multiline form
                lines = body[1:].split("\n")
                closing = lines.pop()                  # indentation before """
                ind = closing
                out = []
                for ln in lines:
                    if ln.strip() == "":
                        out.append("")
                    elif ln.startswith(ind):
                        out.append(ln[len(ind):])
                    else:
                        raise GooseError("raw string line lacks the closing indentation", start, path)
                body = "\n".join(out)
            toks.append(Tok("str", '"""', start, adj, body.encode()))
            i = j + 3
        elif c == '"':
            j, buf = i + 1, bytearray()
            while True:
                if j >= n or src[j] == "\n":
                    err("unterminated string")
                if src[j] == '"':
                    break
                if src[j] == "\\":
                    v, j = esc(j + 1)
                    buf.append(v)
                else:
                    buf += src[j].encode()
                    j += 1
            toks.append(Tok("str", '"', line, adj, bytes(buf)))
            i = j + 1
        elif c == "'":
            j = i + 1
            if j < n and src[j] == "\\":
                v, j = esc(j + 1)
            elif j < n:
                v, j = ord(src[j]), j + 1
            else:
                err("unterminated character literal")
            if j >= n or src[j] != "'":
                err("unterminated character literal")
            toks.append(Tok("char", "'", line, adj, v))
            i = j + 1
        elif c.isdigit():
            j = i
            if src.startswith(("0x", "0X"), i):
                j = i + 2
                while j < n and (src[j] in "0123456789abcdefABCDEF_"):
                    j += 1
                is_float = False
                if j < n and src[j] == "." and j + 1 < n and src[j + 1] in "0123456789abcdefABCDEF":
                    is_float = True                      # hex float: 0x1.8p3
                    j += 1
                    while j < n and src[j] in "0123456789abcdefABCDEF":
                        j += 1
                if j < n and src[j] in "pP":
                    is_float = True
                    j += 1
                    if j < n and src[j] in "+-":
                        j += 1
                    while j < n and src[j].isdigit():
                        j += 1
                text = src[i:j]
                if is_float:
                    toks.append(Tok("float", text, line, adj, float.fromhex(text)))
                else:
                    toks.append(Tok("int", text, line, adj, int(text.replace("_", ""), 16)))
            else:
                while j < n and (src[j].isdigit() or src[j] == "_"):
                    j += 1
                is_float = False
                if j + 1 < n and src[j] == "." and src[j + 1].isdigit():
                    is_float = True
                    j += 1
                    while j < n and (src[j].isdigit() or src[j] == "_"):
                        j += 1
                if j < n and src[j] in "eE":
                    k = j + 1
                    if k < n and src[k] in "+-":
                        k += 1
                    if k < n and src[k].isdigit():
                        is_float = True
                        while k < n and src[k].isdigit():
                            k += 1
                        j = k
                text = src[i:j].replace("_", "")
                toks.append(Tok("float", text, line, adj, float(text)) if is_float
                            else Tok("int", text, line, adj, int(text)))
            i = j
        elif c.isalpha() or c == "_":
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            toks.append(Tok("ident", src[i:j], line, adj))
            i = j
        else:
            for ops in (_OPS3, _OPS2):
                w = len(ops[0])
                if src[i:i + w] in ops:
                    toks.append(Tok("op", src[i:i + w], line, adj))
                    i += w
                    break
            else:
                if c in "+-*/%&|^~!<>=.,;:(){}[]?#@$":
                    toks.append(Tok("op", c, line, adj))
                    i += 1
                else:
                    err("unexpected character %r" % c)
    toks.append(Tok("eof", "<eof>", line, True))
    return toks


# ---------------------------------------------------------------------------
# AST

class N:
    """A node: `kind`, `line`, and whatever fields the kind has."""

    def __init__(self, kind, line, **kw):
        self.kind, self.line = kind, line
        self.__dict__.update(kw)

    def __repr__(self):
        f = ", ".join("%s=%r" % (k, v) for k, v in self.__dict__.items()
                      if k not in ("kind", "line"))
        return "%s(%s)" % (self.kind, f)


UINTS = ("u8", "u16", "u32", "u64", "varint")
PRIMS = ("bool", "i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64",
         "f32", "f64", "varint")
ASSIGN = ("=", ".=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=")
KEYWORDS = {"fn", "thread_fn", "let", "var", "const", "struct", "enum", "type", "if", "else",
            "match", "while", "for", "loop", "return", "break", "continue", "guard", "defer",
            "as", "in", "null", "true", "false", "import", "namespace", "extern", "export"}

# binary operators, loosest first
_LEVELS = (("||",), ("&&",), ("<", "<=", ">", ">=", "==", "!=", ".==", ".!="),
           ("|",), ("^",), ("&",), ("<<", ">>"), ("+", "-"), ("*", "/", "%"))


class Parser:
    def __init__(self, toks, path="<goose>"):
        self.t, self.p, self.path = toks, 0, path
        self.nostruct = False
        self.stmt_start = -1
        self.ended = False

    # -- token helpers ------------------------------------------------------
    @property
    def tok(self):
        return self.t[self.p]

    def peek(self, k=1):
        return self.t[min(self.p + k, len(self.t) - 1)]

    def err(self, msg, tok=None):
        raise GooseError(msg, (tok or self.tok).line, self.path)

    def is_op(self, text, k=0):
        t = self.peek(k) if k else self.tok
        return t.kind == "op" and t.text == text

    def is_kw(self, text, k=0):
        t = self.peek(k) if k else self.tok
        return t.kind == "ident" and t.text == text

    def next(self):
        t = self.t[self.p]
        if t.kind != "eof":
            self.p += 1
        return t

    def accept_op(self, text):
        if self.is_op(text):
            return self.next()

    def accept_kw(self, text):
        if self.is_kw(text):
            return self.next()

    def expect_op(self, text):
        if not self.is_op(text):
            self.err("expected '%s', found '%s'" % (text, self.tok.text))
        return self.next()

    def expect_kw(self, text):
        if not self.is_kw(text):
            self.err("expected '%s', found '%s'" % (text, self.tok.text))
        return self.next()

    def ident(self, what="a name"):
        t = self.tok
        if t.kind != "ident" or t.text in KEYWORDS - {"self"}:
            self.err("expected %s, found '%s'" % (what, t.text))
        return self.next().text

    def gt_op(self):
        """The operator starting at '>' tokens: '>>=', '>>', '>=' or '>', or None."""
        if not self.is_op(">"):
            return None
        a, b = self.peek(1), self.peek(2)
        if a.kind == "op" and a.text == ">" and a.adj:
            if b.kind == "op" and b.text == "=" and b.adj:
                return ">>="
            return ">>"
        if a.kind == "op" and a.text == "=" and a.adj:
            return ">="
        return ">"

    def eat_gt(self, op):
        for _ in range(len(op)):
            self.next()

    def try_(self, fn):
        save = (self.p, self.nostruct)
        try:
            return fn()
        except GooseError:
            self.p, self.nostruct = save
            return None

    # -- program ------------------------------------------------------------
    def program(self):
        decls = []
        while self.tok.kind != "eof":
            d = self.topdecl()
            if d is not None:
                decls.append(d)
        return decls

    def qname(self):
        line = self.tok.line
        if self.accept_op("::"):
            return N("Name", line, name=self.ident(), ns="")
        n = self.ident()
        if self.is_op("::"):
            self.next()
            return N("Name", line, name=self.ident(), ns=n)
        return N("Name", line, name=n, ns=None)

    def topdecl(self):
        t = self.tok
        line = t.line
        if self.accept_op(";"):
            return None
        if t.kind != "ident":
            self.err("unexpected '%s' at top level" % t.text)
        w = t.text
        if w == "namespace":
            self.next()
            name = self.ident()
            self.expect_op(";")
            return N("Namespace", line, name=name)
        if w == "import":
            self.next()
            if self.accept_op("."):
                pass
            name = self.ident()
            if self.is_kw("from"):
                self.next()
                src = self.tok
                if src.kind != "str":
                    self.err("expected a string after 'from'")
                self.next()
                self.expect_op(";")
                return N("Import", line, name=name, source=src.val.decode())
            while self.accept_op("."):
                name += "." + self.ident()
            self.expect_op(";")
            return N("Import", line, name=name, source=None)
        if w in ("struct", "root"):
            return self.struct_decl()
        if w == "enum":
            return self.enum_decl()
        if w == "type":
            self.next()
            name = self.decl_ident()
            self.expect_op("=")
            ty = self.type()
            self.expect_op(";")
            return N("TypeAlias", line, name=name, type=ty)
        if w == "extern" and self.peek().kind == "str" and self.is_op("{", 2):
            return self.extern_block()
        if w in ("fn", "thread_fn", "extern", "export", "checkfree", "simd", "inline", "recursive"):
            return self.fn_decl()
        if w in ("let", "var", "const", "reusable"):
            d = self.decl_stmt(top=True)
            return d
        self.err("unexpected '%s' at top level" % w)

    def struct_decl(self):
        line = self.tok.line
        root = bool(self.accept_kw("root"))
        self.expect_kw("struct")
        name = self.decl_ident()
        generics = self.generics() if self.is_op("<") else []
        self.expect_op("{")
        fields = self.fieldlist()
        self.expect_op("}")
        return N("Struct", line, name=name, generics=generics, fields=fields, root=root)

    def decl_ident(self):
        """name, ns::name or ::name, as one string."""
        if self.accept_op("::"):
            return "::" + self.ident()
        n = self.ident()
        if self.accept_op("::"):
            return n + "::" + self.ident()
        return n

    def generics(self):
        self.expect_op("<")
        out = []
        while not self.is_op(">"):
            name = self.ident()
            bound = self.type() if self.accept_op(":") else None
            out.append((name, bound))
            if not self.accept_op(","):
                break
        self.expect_op(">")
        return out

    def fieldlist(self):
        fields = []
        while not self.is_op("}"):
            line = self.tok.line
            if self.is_kw("pad"):
                self.next()
                size = self.next().val if self.tok.kind == "int" else None
                fields.append(N("Pad", line, size=size))
            else:
                mode = None
                if self.is_kw("let") or self.is_kw("const"):
                    mode = self.next().text
                name = self.ident()
                self.expect_op(":")
                ty = self.type()
                default = self.expr() if self.accept_op("=") else None
                fields.append(N("Field", line, name=name, type=ty, default=default, mode=mode))
            if not self.accept_op(","):
                break
        return fields

    def enum_decl(self):
        line = self.tok.line
        self.expect_kw("enum")
        name = self.decl_ident()
        generics = self.generics() if self.is_op("<") else []
        self.expect_op("{")
        variants = []
        while not self.is_op("}"):
            vl = self.tok.line
            vname = self.ident()
            fields = None
            if self.accept_op("{"):
                fields = self.fieldlist()
                self.expect_op("}")
            variants.append(N("Variant", vl, name=vname, fields=fields))
            if not self.accept_op(","):
                break
        self.expect_op("}")
        return N("Enum", line, name=name, generics=generics, variants=variants)

    def extern_block(self):
        line = self.tok.line
        self.expect_kw("extern")
        lib = self.next().val.decode()
        self.expect_op("{")
        fns = []
        while not self.is_op("}"):
            fl = self.tok.line
            self.expect_kw("fn")
            name = self.decl_ident()
            params = self.params()
            rets = self.rettypes()
            sym = None
            if self.accept_op("="):
                sym = self.next().val.decode()
            self.expect_op(";")
            fns.append(N("FnDecl", fl, name=name, generics=[], params=params, rets=rets,
                         body=None, quals=["extern"], sym=sym))
        self.expect_op("}")
        return N("ExternBlock", line, lib=lib, fns=fns)

    def fn_decl(self, nested=False):
        line = self.tok.line
        quals = []
        sym = None
        while self.tok.kind == "ident" and self.tok.text not in ("fn", "thread_fn"):
            q = self.next().text
            quals.append(q)
            if q in ("export", "extern") and self.tok.kind == "str":
                sym = self.next().val.decode()
            if self.tok.kind == "eof":
                self.err("expected 'fn'")
        kw = self.next().text                          # fn | thread_fn
        name = self.decl_ident()
        generics = self.generics() if self.is_op("<") else []
        params = self.params()
        rets = self.rettypes()
        body = None
        if self.accept_op("="):
            sym = self.next().val.decode()
            self.expect_op(";")
        elif self.accept_op(";"):
            pass
        else:
            body = self.block()
        return N("FnDecl", line, name=name, generics=generics, params=params, rets=rets,
                 body=body, quals=quals, sym=sym, thread=(kw == "thread_fn"))

    def params(self):
        self.expect_op("(")
        out = []
        while not self.is_op(")"):
            line = self.tok.line
            var = bool(self.accept_kw("var"))
            name = self.ident()
            ty = default = None
            if self.accept_op(":"):
                ty = self.type()
                if self.accept_op("="):
                    default = self.expr()
            out.append(N("Param", line, name=name, type=ty, default=default, var=var))
            if not self.accept_op(","):
                break
        self.expect_op(")")
        return out

    def rettypes(self):
        if not self.accept_op("->"):
            return []
        out = [self.type()]
        while self.accept_op(","):
            out.append(self.type())
        return out

    # -- types --------------------------------------------------------------
    def type(self, allow_dd=True, allow_index=True):
        line = self.tok.line
        const = bool(self.accept_kw("const"))
        t = self.tok
        if self.accept_op("("):
            ty = self.type()
            self.expect_op(")")
        elif t.kind == "ident" and t.text == "fn":
            self.next()
            args, rets = [], []
            if self.accept_op("("):
                while not self.is_op(")"):
                    args.append(self.type())
                    if not self.accept_op(","):
                        break
                self.expect_op(")")
                if self.accept_op("->"):
                    if self.accept_op("("):
                        while not self.is_op(")"):
                            rets.append(self.type())
                            if not self.accept_op(","):
                                break
                        self.expect_op(")")
                    else:
                        rets.append(self.type())
            ty = N("FnType", line, args=args, rets=rets)
        elif t.kind == "ident" and t.text in PRIMS:
            self.next()
            ty = N("Prim", line, name=t.text)
            if allow_index and t.text in ("u8", "u16", "u32", "u64") and self.tok.kind == "ident" \
                    and self.tok.text in ("in", "upto"):
                how = self.next().text
                ty = N("IndexType", line, base=t.text, how=how, pool=self.pool_name())
                return ty
        elif t.kind == "ident" or (t.kind == "op" and t.text == "::"):
            name = self.qname()
            args = self.tyargs() if self.is_op("<") else None
            ty = N("Named", line, name=name, args=args)
        else:
            self.err("expected a type, found '%s'" % t.text)
        ty = self.type_postfix(ty, allow_dd)
        if const:
            ty = N("Const", line, type=ty)
        return ty

    def pool_name(self):
        line = self.tok.line
        q = self.qname()
        while self.is_op(".") and self.peek().kind == "ident":
            self.next()
            q = N("Field", line, base=q, name=self.ident())
        return q

    def tyargs(self):
        self.expect_op("<")
        out = []
        while not self.is_op(">"):
            out.append(self.type())
            if not self.accept_op(","):
                break
        self.expect_op(">")
        return out

    def type_postfix(self, ty, allow_dd=True):
        while True:
            line = self.tok.line
            if self.is_op("[") :
                self.next()
                ty = self.array_suffix(ty, line)
            elif self.is_op("&"):
                self.next()
                rel = None
                if self.is_op("<") and self.peek().kind == "ident" and self.peek().text in UINTS:
                    self.next()
                    width = self.next().text
                    pool = None
                    if self.accept_kw("in"):
                        pool = self.pool_name()
                    self.expect_op(">")
                    rel = (width, pool)
                ty = N("Ref", line, type=ty, rel=rel)
            elif self.is_op("?"):
                self.next()
                ty = N("Opt", line, type=ty)
            elif allow_dd and self.is_op("..") and self._dd_is_type():
                self.next()
                ty = N("VarMode", line, type=ty)
            elif self.is_op(".") and self.peek().kind == "ident" and ty.kind == "Named":
                self.next()
                ty = N("VariantType", line, type=ty, variant=self.ident())
            else:
                return ty

    def _dd_is_type(self):
        nx = self.peek()
        return nx.kind == "op" and nx.text in ("[", "&", "?", ")", ",", ">", "=", ";", "{", "}")

    def array_suffix(self, elem, line):
        if self.accept_op("]"):
            return N("Array", line, elem=elem, mode="var", size=None)
        if self.accept_op(":"):
            self.expect_op("]")
            return N("Slice", line, elem=elem)
        if self.is_op(">") and self.peek().kind == "op" and self.peek().text == "..":
            self.next(); self.next()
            shrink = bool(self.accept_op("<"))
            self.expect_op("]")
            return N("Array", line, elem=elem, mode="grow_shrink" if shrink else "grow", size=None)
        if self.accept_op(".."):
            size = None if self.is_op("]") else self.expr()
            self.expect_op("]")
            return N("Array", line, elem=elem, mode="limited", size=size)
        t = self.tok
        if t.kind == "ident" and t.text in UINTS and self.is_op("]", 1):
            self.next(); self.next()
            return N("Array", line, elem=elem, mode="var", size=t.text)
        size = self.expr()
        self.expect_op("]")
        return N("Array", line, elem=elem, mode="fixed", size=size)

    # -- statements ---------------------------------------------------------
    def block(self):
        line = self.tok.line
        self.expect_op("{")
        saved = (self.nostruct, self.stmt_start, self.ended)
        self.nostruct = False
        stmts, value = [], None
        while not self.is_op("}"):
            if self.tok.kind == "eof":
                self.err("missing '}' for block opened at line %d" % line)
            s, is_value = self.stmt()
            if is_value:
                value = s
                break
            if s is not None:
                stmts.append(s)
        self.expect_op("}")
        self.nostruct, self.stmt_start, self.ended = saved
        return N("Block", line, stmts=stmts, value=value)

    def decl_stmt(self, top=False):
        line = self.tok.line
        reusable = None
        if self.accept_kw("reusable"):
            reusable = "[]" if (self.is_op("[") and self.next() and self.expect_op("]")) else "pool"
        mode = self.next().text                         # let | var | const
        names = [self.decl_ident() if top else self.ident()]
        while self.accept_op(","):
            names.append(self.ident())
        ty = self.type() if self.accept_op(":") else None
        by_ref, inits = False, []
        if self.is_op("=") or self.is_op(".="):
            by_ref = self.next().text == ".="
            inits = self.exprlist()
        self.expect_op(";")
        return N("Decl", line, mode=mode, names=names, type=ty, by_ref=by_ref, inits=inits,
                 reusable=reusable, top=top)

    def exprlist(self):
        out = [self.expr()]
        while self.accept_op(","):
            if self.is_op(";") or self.is_op(")") or self.is_op("}"):
                break
            out.append(self.expr())
        return out

    def stmt(self):
        """Returns (node, is_trailing_value)."""
        t = self.tok
        line = t.line
        self.stmt_start, self.ended = self.p, False
        if t.kind == "ident":
            w = t.text
            if w in ("let", "var") or (w == "const" and not self.is_kw("for", 1)
                                       and not self.is_kw("if", 1)) or w == "reusable":
                return self.decl_stmt(), False
            if w == "guard":
                self.next()
                saved = self.nostruct
                self.nostruct = True
                cond = self.expr()
                self.nostruct = saved
                els = None
                if self.accept_kw("else"):
                    els = self.block()
                else:
                    self.expect_op(";")
                return N("Guard", line, cond=cond, els=els), False
            if w == "defer":
                self.next()
                if self.is_op("{"):
                    body = self.block()
                    self.accept_op(";")
                else:
                    body, is_value = self.simple_stmt(require_semi=False)
                    if is_value:
                        body = N("ExprStmt", line, expr=body)
                return N("Defer", line, body=body), False
            if w in ("fn", "recursive", "inline", "checkfree", "simd") and \
                    (self.peek().kind == "ident"):
                return self.fn_decl(nested=True), False
            if w in ("struct", "enum", "type") and self.peek().kind == "ident":
                return self.topdecl(), False
            if w == "const" and self.peek().kind == "ident" and self.peek().text in ("for", "if"):
                return self.const_stmt(), False
        return self.simple_stmt(require_semi=False)

    def const_stmt(self):
        line = self.tok.line
        self.next()                                     # const
        if self.accept_kw("if"):
            self.nostruct, saved = True, self.nostruct
            cond = self.expr()
            self.nostruct = saved
            then = self.block()
            els = None
            if self.accept_kw("else"):
                els = self.const_stmt_else()
            return N("ConstIf", line, cond=cond, then=then, els=els)
        self.expect_kw("for")
        self.expect_op("<")
        names = [self.ident()]
        while self.accept_op(","):
            names.append(self.ident())
        self.expect_op(">")
        self.expect_kw("in")
        if self.is_op("["):
            self.next()
            src = ("list", self.exprlist() if not self.is_op("]") else [])
            self.expect_op("]")
        else:
            what = self.ident()
            src = (what, self.tyargs())
        body = self.block()
        return N("ConstFor", line, names=names, source=src, body=body)

    def const_stmt_else(self):
        if self.is_kw("const") and self.is_kw("if", 1):
            return self.const_stmt()
        return self.block()

    def simple_stmt(self, require_semi):
        line = self.tok.line
        start = self.p
        self.stmt_start, self.ended = start, False
        e = self.expr()
        ended = self.ended and self.p > start
        t = self.tok
        if t.kind == "op" and t.text == "," and not require_semi:
            targets = [e]
            while self.accept_op(","):
                targets.append(self.expr())
            t = self.tok
            if t.kind == "op" and t.text in ("=", ".="):
                self.next()
                rhs = self.exprlist()
                self.expect_op(";")
                return N("MultiAssign", line, op=t.text, targets=targets, values=rhs), False
            self.err("expected '=' in a multiple assignment, found '%s'" % t.text)
        if t.kind == "op" and (t.text in ASSIGN or self.gt_op() == ">>="):
            op = self.gt_op() if t.text == ">" else t.text
            if t.text == ">":
                self.eat_gt(op)
            else:
                self.next()
            rhs = self.expr()
            self.expect_op(";")
            return N("Assign", line, op=op, target=e, value=rhs), False
        if t.kind == "op" and t.text in ("++", "--"):
            self.next()
            self.expect_op(";")
            return N("IncDec", line, op=t.text, target=e), False
        if self.is_op(";"):
            self.next()
            return N("ExprStmt", line, expr=e), False
        if self.is_op("}") and not require_semi:
            return e, True                              # the block's value
        if ended and not require_semi:
            return N("ExprStmt", line, expr=e), False
        self.err("expected ';', found '%s'" % t.text)

    # -- expressions ----------------------------------------------------------
    def expr(self, level=0):
        if level == len(_LEVELS):
            return self.unary()
        left = self.expr(level + 1)
        ops = _LEVELS[level]
        while not self.ended:
            t = self.tok
            op = None
            if t.kind == "op":
                if t.text == ">":
                    g = self.gt_op()
                    if g in ops:
                        op = g
                elif t.text in ops:
                    op = t.text
            if op is None:
                break
            line = t.line
            if t.text == ">":
                self.eat_gt(op)
            else:
                self.next()
            right = self.expr(level + 1)
            left = N("Binary", line, op=op, left=left, right=right)
        return left

    def unary(self):
        t = self.tok
        if t.kind == "op" and t.text in ("-", "!", "~", "&"):
            self.next()
            return N("Unary", t.line, op=t.text, operand=self.unary())
        start = self.p
        return self.postfix(self.primary(), start)

    def starts_stmt(self, start):
        return start == self.stmt_start

    def control_done(self, start, node):
        """A control construct that began a statement ends it: no operator continues."""
        self.ended = self.starts_stmt(start)
        return node

    def primary(self):
        t = self.tok
        line = t.line
        start = self.p
        k = t.kind
        if k == "int":
            self.next()
            return N("Int", line, value=t.val, text=t.text)
        if k == "float":
            self.next()
            return N("Float", line, value=t.val)
        if k == "str":
            self.next()
            return N("Str", line, value=t.val)
        if k == "char":
            self.next()
            return N("Int", line, value=t.val, text="'", char=True)
        if k == "op":
            if t.text == "(":
                self.next()
                saved = self.nostruct
                self.nostruct = False
                e = self.expr()
                self.nostruct = saved
                self.expect_op(")")
                return N("Paren", line, expr=e)
            if t.text == "[":
                return self.array_lit()
            if t.text == "{":
                saved = (self.stmt_start, self.ended)
                b = self.block()
                self.stmt_start, self.ended = saved
                return self.control_done(start, b)
            if t.text == "::":
                return self.name_expr()
            self.err("expected an expression, found '%s'" % t.text)
        if k != "ident":
            self.err("expected an expression, found end of file" if k == "eof"
                     else "expected an expression, found '%s'" % t.text)
        w = t.text
        if w in ("true", "false"):
            self.next()
            return N("Bool", line, value=(w == "true"))
        if w == "null":
            self.next()
            return N("Null", line)
        if w == "if":
            return self.control_done(start, self.if_expr())
        if w == "match":
            return self.control_done(start, self.match_expr())
        if w == "while":
            self.next()
            saved = self.nostruct
            self.nostruct = True
            cond = self.expr()
            self.nostruct = saved
            return self.control_done(start, N("While", line, cond=cond, body=self.ctl_block()))
        if w == "loop":
            self.next()
            return self.control_done(start, N("Loop", line, body=self.ctl_block()))
        if w == "for":
            return self.control_done(start, self.for_expr())
        if w == "block" and self.is_op("{", 1):
            self.next()
            return self.control_done(start, N("BlockExpr", line, body=self.ctl_block()))
        if w == "return":
            self.next()
            vals, frm = [], None
            if self.is_kw("from") and self.peek().kind == "ident" and self.peek().text not in KEYWORDS:
                self.next()
                frm = self.qname()
            elif not (self.is_op(";") or self.is_op("}")):
                vals = self.exprlist()
                if self.is_kw("from") and self.peek().kind == "ident":
                    self.next()
                    frm = self.qname()
            return N("Return", line, values=vals, frm=frm)
        if w == "break":
            self.next()
            val = None
            if not (self.is_op(";") or self.is_op("}")):
                val = self.expr()
            return N("Break", line, value=val)
        if w == "continue":
            self.next()
            return N("Continue", line)
        if w in KEYWORDS and w != "self":
            self.err("unexpected '%s'" % w)
        return self.name_expr()

    def ctl_block(self):
        saved = (self.stmt_start, self.ended)
        b = self.block()
        self.stmt_start, self.ended = saved
        return b

    def name_expr(self):
        line = self.tok.line
        start = self.p
        name = self.qname()
        # f<T>(..), f<T>{..}, T<..>.x : commit to type arguments only when what follows says so
        if self.is_op("<"):
            def attempt():
                args = self.tyargs()
                if self.is_op("(") or self.is_op("{") and not self.nostruct \
                        or (self.is_op(".") and self.peek().kind == "ident"):
                    return args
                raise GooseError("not type arguments", line, self.path)
            args = self.try_(attempt)
            if args is not None:
                name = N("Name", line, name=name.name, ns=name.ns, args=args)
        if self.is_op("{") and not self.nostruct and self.struct_lit_ahead():
            return self.struct_lit(name)
        return name

    def named_fields_ahead(self):
        a, b = self.peek(1), self.peek(2)
        if a.kind == "op" and a.text == "}":
            return True
        return a.kind == "ident" and b.kind == "op" and b.text == ":"

    def struct_lit_ahead(self):
        nx = self.peek()
        if nx.kind == "op" and nx.text == "}":
            return True
        # `{ name: ...` or `{ expr, ...` -- but `{ stmt; ... }` after a bare name is not ours
        return True

    def struct_lit(self, ty):
        line = self.tok.line
        self.expect_op("{")
        saved = self.nostruct
        self.nostruct = False
        inits, rest = [], False
        while not self.is_op("}"):
            if self.is_op(".."):
                self.next()
                rest = True
                break
            il = self.tok.line
            name = None
            if self.tok.kind == "ident" and self.is_op(":", 1) and not self.is_op("::", 1):
                name = self.next().text
                self.next()
            inits.append(N("FieldInit", il, name=name, value=self.expr()))
            if not self.accept_op(","):
                break
        self.expect_op("}")
        self.nostruct = saved
        return N("StructLit", line, type=ty, inits=inits, rest=rest)

    def array_lit(self):
        line = self.tok.line
        self.expect_op("[")
        saved = self.nostruct
        self.nostruct = False
        if self.accept_op("]"):
            self.nostruct = saved
            return N("ArrayLit", line, items=[], fill=None)
        if self.accept_op(".."):
            cap = self.expr()
            self.expect_op("]")
            self.nostruct = saved
            return N("ArrayLit", line, items=[], fill=None, cap=cap)
        first = self.expr()
        if self.accept_op(";"):
            count = self.expr()
            self.expect_op("]")
            self.nostruct = saved
            return N("ArrayLit", line, items=None, fill=(first, count))
        items = [first]
        while self.accept_op(","):
            if self.is_op("]"):
                break
            items.append(self.expr())
        self.expect_op("]")
        self.nostruct = saved
        return N("ArrayLit", line, items=items, fill=None)

    def if_expr(self):
        line = self.tok.line
        self.expect_kw("if")
        saved = self.nostruct
        self.nostruct = True
        cond = self.expr()
        self.nostruct = saved
        then = self.ctl_block()
        els = None
        if self.accept_kw("else"):
            if self.is_kw("if"):
                els = self.if_expr()
            else:
                els = self.ctl_block()
        return N("If", line, cond=cond, then=then, els=els)

    def match_expr(self):
        line = self.tok.line
        self.expect_kw("match")
        outer = (self.stmt_start, self.ended)
        saved = self.nostruct
        self.nostruct = True
        scrut = self.expr()
        self.nostruct = saved
        self.expect_op("{")
        arms = []
        while not self.is_op("}"):
            al = self.tok.line
            pat = self.pattern()
            self.expect_op("=>")
            self.nostruct = False
            self.stmt_start = -1
            body = self.expr()
            self.nostruct = saved
            arms.append(N("Arm", al, pattern=pat, body=body))
            if not self.accept_op(",") and not self.is_op("}"):
                if body.kind not in ("Block", "If", "Match", "BlockExpr", "While", "Loop", "For"):
                    self.err("expected ',' or '}' after a match arm, found '%s'" % self.tok.text)
        self.expect_op("}")
        self.stmt_start, self.ended = outer
        return N("Match", line, scrutinee=scrut, arms=arms)

    def pattern(self):
        line = self.tok.line
        t = self.tok
        if t.kind == "ident" and t.text == "_" and not self.is_op(",", 1):
            self.next()
            return N("PWild", line)
        if t.kind == "ident" and t.text not in ("true", "false") and \
                (self.peek().kind == "ident" and self.peek().text != "if"
                 or self.is_op("&", 1)):
            variant = self.qname()
            ref = bool(self.accept_op("&"))
            bind = self.ident()
            return N("PVariant", line, variant=variant, ref=ref, bind=bind)
        items = []
        while True:
            lo = self.bound()
            hi = self.bound() if self.accept_op("..") else None
            items.append((lo, hi))
            if not self.accept_op(","):
                break
        return N("PItems", line, items=items)

    def bound(self):
        line = self.tok.line
        neg = bool(self.accept_op("-"))
        t = self.tok
        if t.kind in ("int", "char"):
            self.next()
            return N("Int", line, value=-t.val if neg else t.val, text=t.text)
        if t.kind == "float":
            self.next()
            return N("Float", line, value=-t.val if neg else t.val)
        if t.kind == "str":
            self.next()
            return N("Str", line, value=t.val)
        if t.kind == "ident" and t.text in ("true", "false"):
            self.next()
            return N("Bool", line, value=(t.text == "true"))
        q = self.qname()
        while self.accept_op("."):
            q = N("Field", line, base=q, name=self.ident())
        return q

    def for_expr(self):
        line = self.tok.line
        self.expect_kw("for")
        ref = bool(self.accept_op("&"))
        binds = []
        while True:
            name = self.ident()
            ty = self.type(allow_index=False) if self.accept_op(":") else None
            binds.append((name, ty))
            if not self.accept_op(","):
                break
        self.expect_kw("in")
        saved = self.nostruct
        self.nostruct = True
        lo = self.expr()
        hi = self.expr() if self.accept_op("..") else None
        self.nostruct = saved
        body = self.ctl_block()
        return N("For", line, ref=ref, binds=binds, iter=lo, hi=hi, body=body)

    def postfix(self, e, start=-1):
        while not self.ended:
            t = self.tok
            line = t.line
            if t.kind == "op":
                if t.text == "(":
                    e = self.call(e, None, line, start)
                    continue
                if t.text == "[":
                    self.next()
                    e = self.index_or_slice(e, line)
                    continue
                if t.text == ".":
                    self.next()
                    if self.tok.kind == "int":
                        e = N("Field", line, base=e, name=str(self.next().val))
                        continue
                    name = self.ident("a field name")
                    targs = None
                    if self.is_op("<"):
                        def attempt():
                            a = self.tyargs()
                            if self.is_op("("):
                                return a
                            raise GooseError("not type arguments", line, self.path)
                        targs = self.try_(attempt)
                    if targs is not None or self.is_op("("):
                        e = N("Field", line, base=e, name=name)
                        e = self.call(e, targs, line, start)
                        continue
                    if self.is_op("{") and not self.nostruct and e.kind in ("Name", "Field") \
                            and (name[:1].isupper() or self.named_fields_ahead()):
                        e = self.struct_lit(N("Field", line, base=e, name=name))
                        continue
                    e = N("Field", line, base=e, name=name)
                    continue
            elif t.kind == "ident" and t.text == "as":
                self.next()
                bang = bool(self.accept_op("!"))
                ty = self.type(allow_dd=False)
                e = N("Cast", line, expr=e, type=ty, checked=not bang)
                continue
            break
        return e

    def call(self, fn, targs, line, chain_start=-1):
        start = self.p
        self.expect_op("(")
        saved = self.nostruct
        self.nostruct = False
        args = []
        while not self.is_op(")"):
            args.append(self.expr())
            if not self.accept_op(","):
                break
        self.expect_op(")")
        self.nostruct = saved
        node = N("Call", line, fn=fn, targs=targs, args=args, block=None)
        if self.is_op("{") and not self.nostruct:
            node.block = self.trailing_block()
            # a call with a trailing block that begins a statement ends it
            if chain_start == self.stmt_start:
                self.ended = True
        return node

    def trailing_block(self):
        line = self.tok.line
        self.expect_op("{")
        saved = (self.nostruct, self.stmt_start, self.ended)
        self.nostruct = False
        params = None

        def try_params():
            out = []
            while True:
                var = bool(self.accept_kw("var"))
                ref = bool(self.accept_op("&"))
                name = self.ident()
                ty = self.type() if self.accept_op(":") else None
                out.append(N("Param", self.tok.line, name=name, type=ty, default=None, var=var, ref=ref))
                if not self.accept_op(","):
                    break
            self.expect_op("=>")
            return out
        params = self.try_(try_params)
        stmts, value = [], None
        while not self.is_op("}"):
            if self.tok.kind == "eof":
                self.err("missing '}' for block opened at line %d" % line)
            s, is_value = self.stmt()
            if is_value:
                value = s
                break
            if s is not None:
                stmts.append(s)
        self.expect_op("}")
        self.nostruct, self.stmt_start, self.ended = saved
        return N("FnBlock", line, params=params, body=N("Block", line, stmts=stmts, value=value))

    def index_or_slice(self, base, line):
        saved = self.nostruct
        self.nostruct = False

        def bound():
            frm_end = bool(self.accept_op("^"))
            return N("FromEnd", self.tok.line, expr=self.expr()) if frm_end else self.expr()

        if self.accept_op(".."):
            lo = None
            hi = None if self.is_op("]") else bound()
            self.expect_op("]")
            self.nostruct = saved
            return N("Slice", line, base=base, lo=lo, hi=hi)
        first = bound()
        if self.accept_op(".."):
            hi = None if self.is_op("]") else bound()
            self.expect_op("]")
            self.nostruct = saved
            return N("Slice", line, base=base, lo=first, hi=hi)
        self.expect_op("]")
        self.nostruct = saved
        return N("Index", line, base=base, index=first)


def parse(src, path="<goose>"):
    try:
        return Parser(lex(src, path), path).program()
    except GooseError as e:
        e.path = path
        raise


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    path = argv[1]
    try:
        with open(path, encoding="utf-8") as f:
            decls = parse(f.read(), path)
    except GooseError as e:
        print(e, file=sys.stderr)
        return 1
    for d in decls:
        what = getattr(d, "name", getattr(d, "lib", ""))
        print("%4d  %-10s %s" % (d.line, d.kind, what))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
