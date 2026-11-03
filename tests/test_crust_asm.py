"""`asm!`: Rust inline assembly lowered to ShivyCX's GCC-style `asm`.

Templates stay in Intel syntax, as Rust writes them; the statement is wrapped
in `.intel_syntax noprefix`. These programs run on the real CPU -- plain
arithmetic, `rdtsc`, and Linux `syscall` for explicit-register operands.
"""

import unittest

import shivyc.crust as crust
from tests.test_crust_extras import _rs


class TestAsmOperands(unittest.TestCase):

    def test_register_operands_in_out_inout(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let mut r: u64 = 30;
    unsafe { asm!("add {0}, {1}", inout(reg) r, in(reg) 12_u64, options(nomem, nostack)); }
    let o: u64;
    unsafe { asm!("mov {}, {}", out(reg) o, in(reg) r, options(nomem, nostack)); }
    o as i32
}
"""), 42)

    def test_named_operands_and_const(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let a: u64;
    unsafe { asm!("lea {dst}, [{src} + {src}*2]", dst = out(reg) a, src = in(reg) 5_u64, options(nomem, nostack)); }
    let mut b: u64 = a;
    unsafe { asm!("add {0}, {n}", inout(reg) b, n = const 5, options(nomem, nostack)); }
    b as i32
}
"""), 20)

    def test_explicit_registers_and_clobbers_via_syscall(self):
        self.assertEqual(_rs("""
fn getpid() -> u64 {
    let mut r: u64 = 39;
    unsafe {
        asm!("syscall", inlateout("rax") r, lateout("rcx") _, lateout("r11") _, options(nostack));
    }
    r
}
fn main() -> i32 { if getpid() > 0 { 7 } else { 0 } }
"""), 7)

    def test_sized_outputs_in_named_registers(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let lo: u32; let hi: u32;
    unsafe { asm!("rdtsc", out("eax") lo, out("edx") hi, options(nomem, nostack)); }
    if lo != 0 || hi != 0 { 5 } else { 0 }
}
"""), 5)

    def test_r8_to_r11_are_bound_inputs(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let x: u64;
    unsafe { asm!("mov {0}, r10", out(reg) x, in("r10") 77_u64, options(nomem, nostack)); }
    x as i32
}
"""), 77)

    def test_multiple_template_strings_are_lines(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let mut r: u64 = 1;
    unsafe { asm!("add {0}, 2", "add {0}, 3", inout(reg) r, options(nomem, nostack)); }
    r as i32
}
"""), 6)

    def test_inout_with_separate_output(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let x: u64 = 40;
    let y: u64;
    unsafe { asm!("add {0}, 2", inout(reg) x => y, options(nomem, nostack)); }
    y as i32
}
"""), 42)

    def test_escaped_braces_pass_through(self):
        c = crust.translate("""
fn f() { unsafe { asm!("nop {{ }}", options(nomem)); } }
""")
        self.assertIn("nop { }", c)

    def test_noreturn_marks_the_call_as_diverging(self):
        c = crust.translate("""
fn halt() -> ! { unsafe { asm!("hlt", options(noreturn)) } }
""")
        self.assertIn("hlt", c)


class TestCpuid(unittest.TestCase):

    def test_cpuid_returns_real_data_and_keeps_rbx(self):
        self.assertEqual(_rs("""
use core::arch::x86_64::__cpuid;
fn main() -> i32 {
    let leaf0 = __cpuid(0);
    let feat = __cpuid_count(1, 0);
    let mut bad = 0;
    if leaf0.eax == 0 { bad += 1; }
    let b0 = leaf0.ebx & 0xFF;          // 'G'enuineIntel or 'A'uthenticAMD
    if b0 != 0x47 && b0 != 0x41 { bad += 2; }
    if feat.edx == 0 { bad += 4; }
    bad
}
"""), 0)


class TestAsmRefusals(unittest.TestCase):

    def _err(self, body):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate("fn f(x: u64) { unsafe { %s } }" % body)
        return str(cm.exception)

    def test_att_syntax(self):
        self.assertIn("att_syntax", self._err(
            'asm!("nop", options(att_syntax));'))

    def test_operand_modifiers(self):
        self.assertIn("modifiers", self._err(
            'asm!("mov {:e}, 1", out(reg) _x);'))

    def test_sym_operands(self):
        self.assertIn("`sym`", self._err('asm!("call {}", sym f);'))

    def test_unknown_register(self):
        self.assertIn("not supported", self._err(
            'asm!("nop", in("xmm0") x);'))

    def test_template_cannot_name_an_explicit_register_operand(self):
        self.assertIn("explicit-register", self._err(
            'asm!("mov {0}, 1", in("rax") x);'))

    def test_template_operand_out_of_range(self):
        self.assertIn("operand 3", self._err(
            'asm!("mov {3}, 1", in(reg) x);'))


if __name__ == "__main__":
    unittest.main()
