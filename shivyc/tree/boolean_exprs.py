"""Boolean expression nodes in the AST."""

import shivyc.ctypes as ctypes
import shivyc.il_cmds.control as control_cmds
import shivyc.il_cmds.value as value_cmds
import shivyc.il_gen as il_gen
from shivyc.errors import CompilerError
from shivyc.il_gen import ILValue
from shivyc.tree.expr_base import _RExprNode
from shivyc.tree.utils import arith_conversion_type, set_type


class _BoolAndOr(_RExprNode):
    """Base class for && and || operators."""

    def __init__(self, left, right, op):
        """Initialize node."""
        super().__init__()
        self.left = left
        self.right = right
        self.op = op

    # JumpZero for &&, and JumpNotZero for ||
    jump_cmd = None

    # 1 for &&, 0 for ||
    initial_value = 1

    def make_il(self, il_code: "il_gen.ILCode", symbol_table: "il_gen.SymbolTable", c):
        err = f"'{str(self.op)}' operator requires scalar operands"
        left = self.left.make_il(il_code, symbol_table, c)
        if not left.ctype.is_scalar():
            raise CompilerError(err, self.left.r)

        # `other_val` is the result when an operand makes the jump fire: 0 for
        # `&&`, 1 for `||`. `initial_value` is the result otherwise.
        other_val = 1 - self.initial_value

        # Constant-fold when the operands are compile-time constants, so `&&`
        # and `||` may appear in constant contexts -- array sizes, bit-field
        # widths, and the glibc `_Static_assert` fallback, whose bit-field
        # width is `(A && B) ? 2 : -1`. Short-circuit semantics are respected:
        # a left operand that already determines the result (`0 && x`,
        # `1 || x`) folds without the right operand contributing.
        #
        # gcc type-checks *both* operands even when the value short-circuits, so
        # the right operand is probed for its type. The probe runs into a
        # throwaway IL buffer, emitting no IL into the real stream -- which both
        # keeps a folded result free of stray code and lets a runtime
        # short-circuit (`1 || f()`) skip the right operand's side effects.
        if left.literal is not None:
            right_probe = self._probe_type(self.right, symbol_table, c)
            if not right_probe.ctype.is_scalar():
                raise CompilerError(err, self.right.r)
            if self._jump_fires(left):
                return self._literal(il_code, other_val)
            if right_probe.literal is not None:
                fired = self._jump_fires(right_probe)
                return self._literal(
                    il_code, other_val if fired else self.initial_value)
            # Left is constant but does not decide the result and the right
            # operand is not constant: fall through to the runtime code below,
            # which re-evaluates the right operand (guarded by the left jump).

        # Runtime path (unchanged from the non-folding case): the right operand
        # is evaluated after the left jump so a runtime short-circuit skips it.
        out = ILValue(ctypes.integer)

        init = ILValue(ctypes.integer)
        il_code.register_literal_var(init, self.initial_value)

        other = ILValue(ctypes.integer)
        il_code.register_literal_var(other, other_val)

        set_out = il_code.get_label()
        end = il_code.get_label()

        il_code.add(value_cmds.Set(out, init))
        il_code.add(self.jump_cmd(left, set_out))
        right = self.right.make_il(il_code, symbol_table, c)
        if not right.ctype.is_scalar():
            raise CompilerError(err, self.right.r)

        il_code.add(self.jump_cmd(right, set_out))
        il_code.add(control_cmds.Jump(end))
        il_code.add(control_cmds.Label(set_out))
        il_code.add(value_cmds.Set(out, other))
        il_code.add(control_cmds.Label(end))
        return out

    def _probe_type(self, node, symbol_table, c):
        """Evaluate `node` into a throwaway ILCode purely to obtain and validate
        its type, emitting no IL into the real stream. The returned ILValue's
        `.ctype` and `.literal` are valid for the folding decisions above."""
        scratch = il_gen.ILCode()
        # Marked as a probe so the node does not cache the lvalue (and the
        # temporaries) it builds here: the runtime path evaluates `node` again
        # into the real IL, and a cached lvalue would refer to values whose
        # defining commands only ever went into this throwaway buffer.
        scratch.probe = True
        scratch.start_func("<boolop-type-probe>")
        return node.make_il(scratch, symbol_table, c)

    def _jump_fires(self, val):
        """Whether jump_cmd fires for the constant operand `val`: `&&` (which
        jumps on zero) fires when the operand is false; `||` when it is true."""
        try:
            truthy = float(val.literal.val) != 0.0
        except (TypeError, ValueError):
            truthy = bool(val.literal.val)
        # initial_value 1 => && / JumpZero (fires on false); 0 => || / JumpNotZero.
        return (not truthy) if self.initial_value == 1 else truthy

    def _literal(self, il_code, value):
        """Build a literal integer ILValue holding 0 or 1."""
        out = ILValue(ctypes.integer)
        il_code.register_literal_var(out, str(value))
        return out


class BoolAnd(_BoolAndOr):
    """Expression that performs boolean and of two values."""

    jump_cmd = control_cmds.JumpZero
    initial_value = 1


class BoolOr(_BoolAndOr):
    """Expression that performs boolean or of two values."""

    jump_cmd = control_cmds.JumpNotZero
    initial_value = 0


class BoolNot(_RExprNode):
    """Boolean not."""

    def __init__(self, expr):
        """Initialize node."""
        super().__init__()
        self.expr = expr

    def make_il(self, il_code: "il_gen.ILCode", symbol_table: "il_gen.SymbolTable", c):
        """Make code for this node."""

        expr = self.expr.make_il(il_code, symbol_table, c)
        if not expr.ctype.is_scalar():
            err = "'!' operator requires scalar operand"
            raise CompilerError(err, self.r)

        # Constant-fold so `!` may appear in constant contexts such as array
        # sizes and Py_BUILD_ASSERT_EXPR, e.g. `sizeof(char[1 - 2*!(cond)])`.
        if expr.literal is not None:
            out = ILValue(ctypes.integer)
            try:
                is_zero = float(expr.literal.val) == 0.0
            except (TypeError, ValueError):
                is_zero = not expr.literal.val
            il_code.register_literal_var(out, "1" if is_zero else "0")
            return out

        # ILValue for storing the output
        out = ILValue(ctypes.integer)

        # ILValue for zero.
        zero = ILValue(ctypes.integer)
        il_code.register_literal_var(zero, "0")

        # ILValue for one.
        one = ILValue(ctypes.integer)
        il_code.register_literal_var(one, "1")

        # Label which skips the line which sets out to 0.
        end = il_code.get_label()

        il_code.add(value_cmds.Set(out, one))
        il_code.add(control_cmds.JumpZero(expr, end))
        il_code.add(value_cmds.Set(out, zero))
        il_code.add(control_cmds.Label(end))

        return out


class Conditional(_RExprNode):
    """Conditional (ternary) operator: cond ? then : els."""

    def __init__(self, cond, then, els, op):
        """Initialize node."""
        super().__init__()
        self.cond = cond
        self.then = then
        self.els = els
        self.op = op

    def make_il(self, il_code: "il_gen.ILCode", symbol_table: "il_gen.SymbolTable", c):
        """Make code for this node."""
        cond = self.cond.make_il(il_code, symbol_table, c)
        if not cond.ctype.is_scalar():
            err = "conditional operator requires scalar first operand"
            raise CompilerError(err, self.cond.r)

        # If the condition is a compile-time constant, fold to the taken arm.
        # This is what lets `?:` appear in constant contexts such as array
        # sizes, e.g. `int x[sizeof(long) == 8 ? 14 : 9]`.
        if cond.literal is not None:
            taken = self.then if cond.literal.val != 0 else self.els
            return taken.make_il(il_code, symbol_table, c)

        then_store = il_code.get_label()
        els_label = il_code.get_label()
        end = il_code.get_label()

        # If the condition is zero, run the else arm.
        il_code.add(control_cmds.JumpZero(cond, els_label))

        # The then arm is emitted first but, via the jump below, only executes
        # when the condition is nonzero. Its store into `out` is deferred to
        # `then_store` (after both arms are evaluated) so the common result
        # type is known before either store is emitted.
        then_val = self.then.make_il(il_code, symbol_table, c)
        il_code.add(control_cmds.Jump(then_store))

        il_code.add(control_cmds.Label(els_label))
        els_val = self.els.make_il(il_code, symbol_table, c)

        # Both arm types are now known; compute the result type.
        out = ILValue(self._result_ctype(then_val, els_val))

        # Else path: store (converted) else value, then skip the then store.
        set_type(els_val, out.ctype, il_code, output=out)
        il_code.add(control_cmds.Jump(end))

        # Then path: store (converted) then value.
        il_code.add(control_cmds.Label(then_store))
        set_type(then_val, out.ctype, il_code, output=out)

        il_code.add(control_cmds.Label(end))
        return out

    def _result_ctype(self, then_val, els_val):
        """Determine the result type of the conditional operator."""
        t, e = then_val.ctype, els_val.ctype
        if t.is_arith() and e.is_arith():
            return arith_conversion_type(t, e)

        # A pointer paired with a null pointer constant yields the pointer
        # type (e.g. `cond ? p : NULL`).
        if t.is_pointer() and self._is_null_constant(els_val):
            return t
        if e.is_pointer() and self._is_null_constant(then_val):
            return e

        if t.is_pointer() and e.is_pointer():
            # A `void *` operand with any object pointer yields `void *`.
            if t.arg.is_void():
                return t
            if e.arg.is_void():
                return e
            # Pointers to compatible types (ignoring qualifiers) yield that
            # pointer type.
            if t.arg.make_unqual().compatible(e.arg.make_unqual()):
                return t

        # Two compatible structure/union operands yield that type. Qualifiers
        # on the operands don't matter -- the result of `?:` is a non-lvalue,
        # so its type is unqualified (C11 6.5.15). This covers e.g. a const
        # global (CPython's `PyStackRef_NULL`) paired with a function result.
        if t.is_struct_union() and e.is_struct_union():
            if t.make_unqual().compatible(e.make_unqual()):
                return t.make_unqual()

        if t.compatible(e):
            return t

        err = "unsupported operand types for conditional operator"
        raise CompilerError(err, self.r)

    @staticmethod
    def _is_null_constant(val):
        """Whether an ILValue is a null pointer constant (0 or (void*)0)."""
        lit = getattr(val, "literal", None)
        if lit is None or getattr(lit, "val", None) != 0:
            return False
        return val.ctype.is_integral() or (
            val.ctype.is_pointer() and val.ctype.arg.is_void())
