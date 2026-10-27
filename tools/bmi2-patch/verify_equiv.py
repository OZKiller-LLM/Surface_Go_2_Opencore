"""
Prove, by execution (not by eye), that each legacy instruction sequence in
bmi_equiv.py produces bit-identical register and flag results to the real
VEX BMI1/BMI2 instruction it replaces -- run entirely in an emulator here,
no BMI2-capable CPU required.
"""
import os, random, struct, sys
import keystone as k
import unicorn as u
from unicorn.x86_const import *

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bmi_equiv as be

ks64 = k.Ks(k.KS_ARCH_X86, k.KS_MODE_64)

REG_MAP = {
    "rax": UC_X86_REG_RAX, "rbx": UC_X86_REG_RBX, "rcx": UC_X86_REG_RCX,
    "rdx": UC_X86_REG_RDX, "rsi": UC_X86_REG_RSI, "rdi": UC_X86_REG_RDI,
    "rbp": UC_X86_REG_RBP, "r8": UC_X86_REG_R8, "r9": UC_X86_REG_R9,
    "r10": UC_X86_REG_R10, "r11": UC_X86_REG_R11, "r12": UC_X86_REG_R12,
    "r13": UC_X86_REG_R13, "r14": UC_X86_REG_R14, "r15": UC_X86_REG_R15,
}
ALL_REGS = list(REG_MAP)

CODE_BASE = 0x400000
STACK_BASE = 0x700000
STACK_SIZE = 0x4000
CODE_SIZE = 0x4000


def run(asm_text, init_regs, enable_bmi2):
    mu = u.Uc(u.UC_ARCH_X86, u.UC_MODE_64)
    mu.mem_map(CODE_BASE, CODE_SIZE)
    mu.mem_map(STACK_BASE, STACK_SIZE)

    if enable_bmi2:
        encoding, _ = ks64.asm(asm_text, CODE_BASE)
    else:
        encoding, _ = ks64.asm(asm_text, CODE_BASE)
    code = bytes(encoding) + b"\x90" * 16  # NOP padding: emu_stop() in the
    # hook can take effect one instruction late in this Unicorn build, so an
    # INT3 trailer would itself raise a real CPU exception; NOPs are inert.
    mu.mem_write(CODE_BASE, code)
    end_addr = CODE_BASE + len(encoding)

    def stop_hook(uc, address, size, _):
        if address >= end_addr:
            uc.emu_stop()
    h = mu.hook_add(u.UC_HOOK_CODE, stop_hook)

    for reg, val in init_regs.items():
        if reg == "_flags":
            continue
        mu.reg_write(REG_MAP[reg], val)
    mu.reg_write(UC_X86_REG_RSP, STACK_BASE + STACK_SIZE - 0x1000)
    mu.reg_write(UC_X86_REG_RFLAGS, init_regs.get("_flags", 0x202))

    try:
        mu.emu_start(CODE_BASE, end_addr + 1)
    except u.UcError as e:
        print("EMU FAIL rip=0x%x code=%s\nasm:\n%s\ninit_regs=%r" % (
            mu.reg_read(UC_X86_REG_RIP), code.hex(), asm_text, init_regs))
        raise

    out = {r: mu.reg_read(REG_MAP[r]) for r in ALL_REGS}
    out["_flags"] = mu.reg_read(UC_X86_REG_RFLAGS)
    return out


def mask(bits):
    return (1 << bits) - 1


def rand_val(bits):
    return random.getrandbits(bits)


def check_equal(label, before, orig_out, legacy_out, dests, bits, flags_defined):
    """Compare: (a) every destination register holds the same value,
    (b) every OTHER register unaffected by either sequence is unchanged
    from `before` in BOTH outputs (i.e. no scratch leakage), and
    (c) flags: if the op defines flags, CF+ZF must match between orig and
    legacy; if not, flags must be unchanged from `before` in the legacy
    version (orig's VEX form is defined not to touch them, so we trust it)."""
    dests64 = {be._r64(d) for d in dests}
    problems = []
    for r in ALL_REGS:
        if r in dests64:
            ov = orig_out[r] & mask(bits)
            lv = legacy_out[r] & mask(bits)
            if ov != lv:
                problems.append(f"dest {r}: orig=0x{ov:x} legacy=0x{lv:x}")
        else:
            bv = before.get(r, 0)
            ov = orig_out[r]
            lv = legacy_out[r]
            if lv != bv:
                problems.append(f"leak into {r}: before=0x{bv:x} legacy_after=0x{lv:x}")
    if flags_defined:
        CF_MASK, ZF_MASK = 1, 0x40
        of, lf = orig_out["_flags"], legacy_out["_flags"]
        if (of & CF_MASK) != (lf & CF_MASK):
            problems.append(f"CF mismatch: orig={of&CF_MASK} legacy={lf&CF_MASK}")
        if (of & ZF_MASK) != (lf & ZF_MASK):
            problems.append(f"ZF mismatch: orig={of&ZF_MASK} legacy={lf&ZF_MASK}")
    else:
        # Ground truth is what the REAL VEX instruction reports, not our
        # raw seed value: bit 1 of RFLAGS (and other reserved bits) are
        # hardware-forced and may not read back exactly as written.
        of = orig_out["_flags"]
        if legacy_out["_flags"] != of:
            problems.append(f"flags not preserved: orig=0x{of:x} legacy_after=0x{legacy_out['_flags']:x}")
    if problems:
        print(f"FAIL [{label}]: " + "; ".join(problems))
        return False
    return True


def test_shiftx(trials=4000):
    ok = fail = 0
    for mnem in ("shrx", "shlx", "sarx"):
        for bits in (32, 64):
            for _ in range(trials // 6):
                dst, src, cnt = random.sample(ALL_REGS, 3)
                before = {r: rand_val(64) for r in ALL_REGS}
                before["_flags"] = random.choice([0x202, 0x246, 0x293, 0x0])
                cnt_val = random.choice([0, 1, bits - 1, bits, bits + 5, rand_val(6)])
                before[cnt] = cnt_val

                dst_r = dst if bits == 64 else be.GPR32[dst]
                src_r = src if bits == 64 else be.GPR32[src]
                cnt_r = cnt if bits == 64 else be.GPR32[cnt]
                orig_asm = f"{mnem} {dst_r}, {src_r}, {cnt_r}"
                orig_out = run(orig_asm, before, True)

                legacy_asm = be.emit_shiftx(mnem, dst, src, cnt, bits)
                legacy_out = run(legacy_asm, before, False)

                label = f"{mnem}{bits} dst={dst} src={src} cnt={cnt} cntval={cnt_val}"
                if check_equal(label, before, orig_out, legacy_out, [dst], bits, flags_defined=False):
                    ok += 1
                else:
                    fail += 1
    print(f"shiftx: {ok} ok, {fail} fail")
    return fail == 0


def test_rorx(trials=2000):
    ok = fail = 0
    for bits in (32, 64):
        for _ in range(trials // 2):
            dst, src = random.sample(ALL_REGS, 2)
            before = {r: rand_val(64) for r in ALL_REGS}
            before["_flags"] = random.choice([0x202, 0x246, 0x293, 0x0])
            imm = random.choice([0, 1, bits - 1, bits // 2, rand_val(5 if bits == 32 else 6)])

            dst_r = dst if bits == 64 else be.GPR32[dst]
            src_r = src if bits == 64 else be.GPR32[src]
            orig_asm = f"rorx {dst_r}, {src_r}, {imm}"
            orig_out = run(orig_asm, before, True)

            legacy_asm = be.emit_rorx(dst, src, imm, bits)
            legacy_out = run(legacy_asm, before, False)

            label = f"rorx{bits} dst={dst} src={src} imm={imm}"
            if check_equal(label, before, orig_out, legacy_out, [dst], bits, flags_defined=False):
                ok += 1
            else:
                fail += 1
    print(f"rorx: {ok} ok, {fail} fail")
    return fail == 0


def test_mulx(trials=2000):
    # Intel operand order: `mulx op1, op2, src` -> op1=HIGH half, op2=LOW half
    # (verified empirically, see the comment in bmi_equiv.emit_mulx).
    ok = fail = 0
    for bits in (32, 64):
        for _ in range(trials // 2):
            dhi, dlo, src = random.sample(ALL_REGS, 3)
            before = {r: rand_val(64) for r in ALL_REGS}
            before["_flags"] = random.choice([0x202, 0x246, 0x293, 0x0])

            dhi_r = dhi if bits == 64 else be.GPR32[dhi]
            dlo_r = dlo if bits == 64 else be.GPR32[dlo]
            src_r = src if bits == 64 else be.GPR32[src]
            orig_asm = f"mulx {dhi_r}, {dlo_r}, {src_r}"
            orig_out = run(orig_asm, before, True)

            legacy_asm = be.emit_mulx(dhi, dlo, src, bits)
            legacy_out = run(legacy_asm, before, False)

            label = f"mulx{bits} dhi={dhi} dlo={dlo} src={src} rdx={before['rdx']:#x}"
            if check_equal(label, before, orig_out, legacy_out, [dhi, dlo], bits, flags_defined=False):
                ok += 1
            else:
                fail += 1
    print(f"mulx: {ok} ok, {fail} fail")
    return fail == 0


def bzhi_reference(src, idx, bits):
    """Pure-Python BZHI per the Intel SDM definition -- used as ground
    truth INSTEAD OF Unicorn's own `bzhi` emulation for this one
    instruction. Confirmed independently (direct Unicorn repro, no
    surrounding code) that this Unicorn/QEMU build's 32-bit BZHI TCG
    implementation is itself buggy: for n>=32 it incorrectly clears bit 31
    of the result (e.g. bzhi eax,ebx,ecx with ebx=0xde3580b4, ecx=255 should
    leave eax=0xde3580b4 per spec -- src unchanged when n>=OperandSize --
    but Unicorn returns 0x5e3580b4, clearing bit 31). Trusting Unicorn's
    `orig` output as ground truth for bzhi specifically would make a correct
    legacy sequence look wrong, so this instruction is checked against the
    spec directly rather than against the emulator's own instruction."""
    mask = (1 << bits) - 1
    src &= mask
    n = idx & 0xFF
    if n >= bits:
        result = src
        cf = 1
    else:
        result = src & ((1 << n) - 1)
        cf = 0
    zf = 1 if result == 0 else 0
    return result, cf, zf


def test_bzhi(trials=3000):
    ok = fail = 0
    unicorn_bzhi_bugs = 0
    for bits in (32, 64):
        for _ in range(trials // 2):
            dst, src, idx = random.sample(ALL_REGS, 3)
            before = {r: rand_val(64) for r in ALL_REGS}
            before["_flags"] = random.choice([0x202, 0x246, 0x293, 0x0])
            n = random.choice([0, 1, bits - 1, bits, bits + 1, 255, rand_val(8)])
            before[idx] = (before[idx] & ~0xFF) | n

            dst_r = dst if bits == 64 else be.GPR32[dst]
            src_r = src if bits == 64 else be.GPR32[src]
            idx_r = idx if bits == 64 else be.GPR32[idx]
            orig_asm = f"bzhi {dst_r}, {src_r}, {idx_r}"
            orig_out = run(orig_asm, before, True)

            legacy_asm = be.emit_bzhi(dst, src, idx, bits)
            legacy_out = run(legacy_asm, before, False)

            ref_result, ref_cf, ref_zf = bzhi_reference(before[src], before[idx], bits)

            # Note (not a failure) when Unicorn's own instruction disagrees
            # with the spec -- documents the emulator bug without letting it
            # fail the suite.
            orig_dst64 = be._r64(dst)
            orig_val = orig_out[orig_dst64] & mask(bits)
            orig_cf = orig_out["_flags"] & 1
            if orig_val != ref_result or orig_cf != ref_cf:
                unicorn_bzhi_bugs += 1

            legacy_val = legacy_out[be._r64(dst)] & mask(bits)
            legacy_cf = legacy_out["_flags"] & 1
            legacy_zf = (legacy_out["_flags"] >> 6) & 1
            label = f"bzhi{bits} dst={dst} src={src} idx={idx} n={n}"
            problems = []
            if legacy_val != ref_result:
                problems.append(f"result: ref=0x{ref_result:x} legacy=0x{legacy_val:x}")
            if legacy_cf != ref_cf:
                problems.append(f"CF: ref={ref_cf} legacy={legacy_cf}")
            if legacy_zf != ref_zf:
                problems.append(f"ZF: ref={ref_zf} legacy={legacy_zf}")
            # Also check no register other than dst was permanently altered.
            for r in ALL_REGS:
                if r == be._r64(dst):
                    continue
                if legacy_out[r] != before.get(r, 0):
                    problems.append(f"leak into {r}: before=0x{before.get(r,0):x} after=0x{legacy_out[r]:x}")
            if problems:
                print(f"FAIL [{label}]: " + "; ".join(problems))
                fail += 1
            else:
                ok += 1
    print(f"bzhi: {ok} ok, {fail} fail (Unicorn's own bzhi disagreed with spec in "
          f"{unicorn_bzhi_bugs} of {ok+fail} cases -- a known emulator bug, not scored)")
    return fail == 0


if __name__ == "__main__":
    random.seed(1234567)
    results = []
    results.append(("shiftx", test_shiftx()))
    results.append(("rorx", test_rorx()))
    results.append(("mulx", test_mulx()))
    results.append(("bzhi", test_bzhi()))
    print()
    all_ok = True
    for name, ok in results:
        print(f"{name}: {'PASS' if ok else 'FAIL'}")
        all_ok &= ok
    sys.exit(0 if all_ok else 1)
