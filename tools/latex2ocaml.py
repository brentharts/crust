#!/usr/bin/env python3
r"""latex2ocaml.py -- a kernel's equation, as the OCaml that is its kernel.

The CrustOS paper states its kernel as one equation (`eq:crustos` in
RosettaMath's `crustos_eq.tex`): a conjunction of two theorems, with the
kernel's definitions written inside it -- under `\underbrace`s and after
`where`.  This reads that LaTeX and writes OCaml:

  * each definition the equation makes becomes a `let`:
      `\underbrace{I}_{\lambda c,\ leb (c.cur) (c.n)}`  ->  `let eq_i c = ..`
      `\underbrace{(rank c)}_{sub (c.n) (c.cur)}`       ->  `let rank c = ..`
      `\underbrace{tick (sched (tick c))}_{run = ..}`   ->  `let run c = ..`
  * `sched c = Nat.rec (\lambda n, Ctx -> Ctx) (\lambda s, s) step K c`,
    with `step = \lambda k ih s, ih E`, is a fold that applies `E` K times:
    a recursion that counts K down, `[@@variant]` the count;
  * each theorem becomes a contract -- the state layer's
    `forall c, Holds (I c) -> Holds (I (run c))` is `[@@requires]` and
    `[@@ensures]` on `run`, and on each function of the equation's own the
    composition passes through ("proofs chained"); the routing layer's
    `forall ns us, Holds (leb (len A) (len B))` is a function returning A
    with `[@@ensures List.length result <= List.length B]`.

What the equation names and does not define -- `tick`, `pass`, `accepted`,
`split`, the context -- comes from a file of primitives, named in the output's
`uses:` line.  The model's fields are naturals, so a contract the equation
states over them also says they are >= 0: without it `sub`, which truncates at
0, would be a subtraction that could wrap.  `tools/ocaml2rust.py` compiles the
output, `tools/rustprove.py` proves its contracts about the compiled code.

    python3 latex2ocaml.py crustos_eq.tex eq:crustos --prims crustos_prims.ml \
        -o generated_crustos.ml
"""
import re
import sys


class ConvertError(Exception):
    pass


# -- LaTeX to a flat string of terms ----------------------------------------

def equation(tex, label):
    """The body of the `equation` environment carrying `\\label{label}`."""
    for m in re.finditer(r'\\begin\{equation\}(.*?)\\end\{equation\}', tex,
                         re.S):
        if '\\label{%s}' % label in m.group(1):
            return m.group(1)
    raise ConvertError('no equation labelled %s' % label)


def macros(tex):
    """`\\newcommand{\\name}[k]{body}` definitions, as {name: (k, body)}."""
    out = {}
    for m in re.finditer(r'\\newcommand\{\\(\w+)\}(?:\[(\d)\])?\{', tex):
        body, i = group(tex, m.end() - 1)
        out[m.group(1)] = (int(m.group(2) or 0), body)
    return out


def group(s, i):
    """The contents of the brace group opening at s[i], and the index after
    its closing brace."""
    assert s[i] == '{', s[i:i + 20]
    depth, j = 0, i
    while True:
        if s[j] == '{':
            depth += 1
        elif s[j] == '}':
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        j += 1


def expand(s, defs):
    """User macros expanded, to a fixed point."""
    for _ in range(10):
        changed = False
        for name, (k, body) in defs.items():
            pat = '\\' + name
            i = 0
            while True:
                i = s.find(pat, i)
                if i < 0:
                    break
                end = i + len(pat)
                if end < len(s) and (s[end].isalpha()):
                    i = end
                    continue
                args = []
                for _a in range(k):
                    while s[end] == ' ':
                        end += 1
                    a, end = group(s, end)
                    args.append(a)
                out = body
                for n_, a in enumerate(args, 1):
                    out = out.replace('#%d' % n_, a)
                s = s[:i] + out + s[end:]
                changed = True
        if not changed:
            return s
    return s


def flatten(s, defs):
    """The equation as plain text, and the definitions its braces make:
    `\\overbrace{X}^{label}` is X (its label kept for the conjunct it names);
    `\\underbrace{X}_{Y}` is X, with Y recorded against it."""
    labels, unders = [], []

    def walk(s):
        out, i = [], 0
        while i < len(s):
            for kind in ('overbrace', 'underbrace'):
                tag = '\\' + kind
                if s.startswith(tag, i):
                    body, j = group(s, s.index('{', i))
                    while s[j] in ' \n':
                        j += 1
                    mark, j = group(s, j + 1)
                    inner = walk(body)
                    if kind == 'overbrace':
                        labels.append((clean(mark), clean(inner)))
                    else:
                        unders.append((clean(inner), clean(walk(mark))))
                    out.append(inner)
                    i = j
                    break
            else:
                out.append(s[i])
                i += 1
        return ''.join(out)
    return clean(walk(expand(s, defs))), labels, unders


def clean(s):
    """Spacing, sizing and layout commands gone; symbols as characters."""
    s = re.sub(r'\\label\{[^}]*\}', ' ', s)
    s = re.sub(r'\\(hspace|phantom)\{[^}]*\}', ' ', s)
    s = re.sub(r'\\\\\[[^\]]*\]', ' ¶ ', s)    # the equation's lines
    s = re.sub(r'\\text\{([^}]*)\}', r' "\1" ', s)
    s = re.sub(r'\\textbf\{([^}]*)\}', r' "\1" ', s)
    s = re.sub(r'\\texttt\{([^}]*)\}', r'\1', s)
    s = re.sub(r'\\mathit\{([^}]*)\}', r'\1', s)
    for a, b in ((r'\lambda', ' λ '), (r'\forall', ' ∀ '), (r'\in', ' ∈ '),
                 (r'\to', ' → '), (r'\wedge', ' ∧ ')):
        s = s.replace(a, b)
    s = re.sub(r'\\(footnotesize|bigl|bigr|Bigl|Bigr|quad|,|;|!| )', ' ', s)
    s = re.sub(r'\\begin\{aligned\}|\\end\{aligned\}|&', ' ', s)
    s = s.replace('{', ' ').replace('}', ' ')
    s = s.replace('\\_', '_')
    return re.sub(r'\s+', ' ', s).strip()


# -- terms --------------------------------------------------------------------

TOKEN = re.compile(r'"[^"]*"|λ|∀|∈|→|∧|[A-Za-z_][\w.]*|\d+|[(),=]')


def tokens(s):
    return [t for t in TOKEN.findall(s) if not t.startswith('"')]


class Parser:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def at(self, *v):
        return self.i < len(self.t) and self.t[self.i] in v

    def next(self):
        self.i += 1
        return self.t[self.i - 1]

    def term(self):
        if self.at('λ'):
            self.next()
            names = []
            while not self.at(','):
                names.append(self.next())
            self.next()
            return ('lam', names, self.term())
        left = self.app()
        if self.at('→'):
            self.next()
            return ('arrow', left, self.term())
        return left

    def app(self):
        parts = []
        while self.i < len(self.t) and not self.at(')', ',', '→', '∧', '=',
                                                     '∀', 'where'):
            parts.append(self.atom())
        if not parts:
            raise ConvertError('expected a term at %r' % self.t[self.i:])
        return parts[0] if len(parts) == 1 else ('app', parts[0], parts[1:])

    def atom(self):
        if self.at('('):
            self.next()
            t = self.term()
            self.next()                       # ')'
            return t
        return ('var', self.next())


def term(s):
    p = Parser(tokens(s))
    t = p.term()
    return t


# -- terms to OCaml -----------------------------------------------------------

BINARY = {'leb': '<=', 'ltb': '<', 'add': '+', 'eqb': '='}


def name(n):
    """An OCaml name for the equation's: `I` is `eq_i` (OCaml's values are
    lower case), a dotted field `c.cur` is the accessor `cur c`."""
    return 'eq_' + n.lower() if n[:1].isupper() else n


def ocaml(t):
    kind = t[0]
    if kind == 'var':
        v = t[1]
        if '.' in v and not v[0].isupper():
            base, field = v.split('.', 1)
            return '(%s %s)' % (field, base)
        return v if v.isdigit() else name(v)
    if kind == 'lam':
        raise ConvertError('a λ where a value was wanted: %r' % (t,))
    f, args = t[1], t[2]
    if f[0] == 'var':
        h = f[1]
        if h in BINARY and len(args) == 2:
            return '(%s %s %s)' % (ocaml(args[0]), BINARY[h], ocaml(args[1]))
        if h == 'sub' and len(args) == 2:
            # Nat subtraction, which stops at 0
            a, b = ocaml(args[0]), ocaml(args[1])
            return '(if %s <= %s then 0 else %s - %s)' % (a, b, a, b)
        if h == 'ite' and len(args) == 4:
            return '(if %s then %s else %s)' % tuple(ocaml(a) for a in args[1:])
        if h == 'len' and len(args) == 1:
            return '(List.length %s)' % ocaml(args[0])
        if h == 'Holds' and len(args) == 1:
            return ocaml(args[0])
    return '(%s %s)' % (ocaml(f), ' '.join(ocaml(a) for a in args))


# -- the equation, read -------------------------------------------------------

def read(tex, label):
    defs = macros(tex)
    flat, labels, unders = flatten(equation(tex, label), defs)
    return flat, labels, unders


def convert(tex, label, prims, fields_natural=('cur', 'n', 'ticks')):
    flat, labels, unders = read(tex, label)
    lets, notes = [], []

    # definitions from the underbraces
    fn_defs = {}                   # name -> (params, body term)
    composition = None
    for below, above in unders:
        if not above or above.startswith('"'):
            continue
        a_toks = tokens(above)
        if len(a_toks) >= 2 and a_toks[1] == '=':
            # `run = <text>`: the braced term is `run`'s body
            fn_defs[a_toks[0]] = (['c'], term(below))
            composition = a_toks[0]
            continue
        head = tokens(below)
        body = term(above)
        if len(head) == 1:                    # `I` := λ c, ..
            if body[0] != 'lam':
                continue
            fn_defs[head[0]] = (body[1], body[2])
        elif len(head) >= 3 and head[0] == '(' and head[-1] == ')':
            fn_defs[head[1]] = (head[2:-1], body)     # `(rank c)` := ..

    # definitions after `where`: `name args = body`
    # each on a line of its own: the equation's `\\[..]` breaks
    for line in flat.split('¶'):
        line = line.replace('"where"', ' ').split(' ∧ ')[0].strip()
        m = re.match(r'([A-Za-z_]\w*)((?: [a-z]\w*)*) = (.*)$', line)
        if m and not line.startswith('∀'):
            fn_defs[m.group(1)] = (m.group(2).split(), term(m.group(3)))

    # the theorems: the conjuncts, each under its overbrace label
    theorems = []
    for lab, body in labels:
        theorems.append((lab.strip('"').strip(), body))

    # -- emit ---------------------------------------------------------------
    out = ['(* generated by tools/latex2ocaml.py from `%s` -- do not edit.'
           % label,
           '',
           '   uses: %s' % prims,
           '',
           '   Every definition below is one the equation makes, in its braces',
           '   or after `where`; every contract is one of its two theorems. *)',
           '']
    nat = ' && '.join('%s c >= 0' % f for f in fields_natural)
    out.append('(* the model\'s fields are naturals *)')
    out.append('let natural c = %s' % nat)
    out.append('')
    invariant = None
    state = next((t for lab, t in theorems if 'state' in lab), None)
    if state is not None:
        # ∀ c ∈ Ctx, Holds (I c) → Holds (I (F c)): I is the invariant
        body = state
        while body[0] == 'app' and body[1] == ('var', '∀'):
            body = body
            break
        inv_name = None
        for n_, (params, t) in fn_defs.items():
            if n_[:1].isupper() and len(params) == 1:
                inv_name = n_
        invariant = name(inv_name) if inv_name else None

    order = []
    for n_ in fn_defs:
        if n_[:1].isupper():
            order.insert(0, n_)
        else:
            order.append(n_)
    # the invariant, the pieces, the step, the loop, then the composition
    rank = lambda n_: (0 if n_[:1].isupper() else 3 if n_ == composition
                       else 2 if n_ in ('sched',) else 1)
    order.sort(key=rank)
    step_expr = None
    if 'step' in fn_defs:
        params, t = fn_defs['step']
        if t[0] == 'lam':
            params, t = t[1], t[2]
        # λ k ih s, ih E : the loop applies E to its state
        if len(params) == 3 and t[0] == 'app' and t[1] == ('var', params[1]) \
                and len(t[2]) == 1:
            step_expr = (params[2], t[2][0])
    guard = lambda v: ('[@@requires natural %s && %s %s] '
                       '[@@ensures natural result && %s result]'
                       % (v, invariant, v, invariant)) if invariant else ''
    for n_ in order:
        params, t = fn_defs[n_]
        if n_ == 'step':
            if step_expr is None:
                raise ConvertError('`step` is not λ k ih s, ih E')
            s, e = step_expr
            out.append('(* step = fun k ih s -> ih E: E is what one pass does *)')
            out.append('let step %s = %s' % (s, ocaml(e)))
            out.append('  ' + guard(s))
            out.append('')
            continue
        if t[0] == 'app' and t[1] == ('var', 'Nat.rec'):
            # Nat.rec (λ n, T) (λ s, s) f K x: f's ih applied K times
            base, f, count, arg = t[2][1], t[2][2], t[2][3], t[2][4]
            if base[0] != 'lam' or base[2] != ('var', base[1][0]):
                raise ConvertError('Nat.rec whose base is not the identity')
            loop = '%s_loop1' % n_
            out.append('(* %s: Nat.rec with the identity at 0 -- a fold that'
                       ' applies `%s` %s times *)' % (n_, f[1], ocaml(count)))
            out.append('let rec %s k s = if k <= 0 then s else %s (k - 1) '
                       '(%s s)' % (loop, loop, f[1]))
            out.append('  %s [@@variant k]' % guard('s'))
            out.append('let %s %s = %s %s %s' % (n_, ' '.join(params), loop,
                                                 ocaml(count), ocaml(arg)))
            out.append('  ' + guard(params[0]))
            out.append('')
            continue
        is_inv = n_[:1].isupper()
        out.append('let %s %s = %s' % (name(n_), ' '.join(params), ocaml(t)))
        if n_ == composition:
            out.append('  (* the state layer: %s *)' % (
                'the invariant survives the composition, for every state'))
            out.append('  ' + guard(params[0]))
        out.append('')
    routing = next((t for lab, t in theorems if 'routing' in lab), None)
    routing = routing and clean(routing).replace('"', '')
    if routing is not None:
        # ∀ ns us, Holds (leb (len A) (len B))
        body, binders = routing, []
        toks = tokens(routing)
        p = Parser(toks)
        if p.at('∀'):
            p.next()
            while not p.at(','):
                binders.append(p.next())
            p.next()
        claim = p.term()
        if claim[0] == 'app' and claim[1] == ('var', 'Holds'):
            claim = claim[2][0]
        if not (claim[0] == 'app' and claim[1] == ('var', 'leb')):
            raise ConvertError('the routing layer is not `leb (len A) (len B)`')
        a, b = claim[2]
        if a[0] == 'app' and a[1] == ('var', 'len'):
            a = a[2][0]
        out.append('(* the routing layer: never longer than its input *)')
        out.append('let routing_layer %s = %s' % (' '.join(binders),
                                                  ocaml(a)))
        out.append('  [@@ensures List.length result <= %s]' % ocaml(b))
        out.append('')
    return '\n'.join(out)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    path, label = argv[0], argv[1]
    prims = argv[argv.index('--prims') + 1] if '--prims' in argv \
        else 'crustos_prims.ml'
    with open(path) as fh:
        tex = fh.read()
    text = convert(tex, label, prims)
    if '-o' in argv:
        with open(argv[argv.index('-o') + 1], 'w') as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
