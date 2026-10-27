"""
Legacy (pre-BMI2) equivalents for the VEX-encoded BMI1/BMI2 instructions found
in Apple's Kaby Lake graphics drivers, for CPUs (Pentium/Celeron) where Intel
disables the VEX instruction encoding entirely.

Each function takes concrete register names (as they appear in the
*original* instruction, straight from Capstone) and returns an assembly
source string implementing the same operation using only baseline SSE2-era
x86-64 instructions (no VEX, no BMI1/BMI2), safe to assemble standalone and
splice into a detour trampoline.

Design rules followed throughout (see commit message / README for the
reasoning):
  - A trampoline must not permanently change any register except the
    instruction's own destination(s).
  - Any instruction that the VEX form does NOT define as writing flags
    (SHRX/SHLX/SARX/RORX/MULX) must leave RFLAGS exactly as found
    (pushfq/popfq around the computation).
  - Any instruction that DOES define flag outputs (BZHI) must reproduce
    those flags, not just leave old ones in place.
  - All scratch registers are chosen, per call site, disjoint from the
    instruction's own operand registers -- never hard-coded -- so aliasing
    between dst/src/count and the scratch registers can never corrupt a
    result.
  - A register we push to protect its original value is restored with a
    real `pop` UNLESS it is itself a destination register, in which case
    the saved slot is discarded with `add rsp, 8` so the freshly computed
    result in that register is not overwritten.
"""

GPR64 = ["rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp",
         "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15"]
GPR32 = {"rax": "eax", "rbx": "ebx", "rcx": "ecx", "rdx": "edx",
         "rsi": "esi", "rdi": "edi", "rbp": "ebp",
         "r8": "r8d", "r9": "r9d", "r10": "r10d", "r11": "r11d",
         "r12": "r12d", "r13": "r13d", "r14": "r14d", "r15": "r15d"}
R32_TO_R64 = {v: k for k, v in GPR32.items()}
GPR8 = {"rax": "al", "rbx": "bl", "rcx": "cl", "rdx": "dl",
        "rsi": "sil", "rdi": "dil", "rbp": "bpl",
        "r8": "r8b", "r9": "r9b", "r10": "r10b", "r11": "r11b",
        "r12": "r12b", "r13": "r13b", "r14": "r14b", "r15": "r15b"}


def _r64(reg):
    """Normalize a register name (32- or 64-bit) to its 64-bit form."""
    return R32_TO_R64.get(reg, reg)


def pick_scratch(n, exclude):
    """Pick n distinct 64-bit scratch registers not in `exclude`."""
    excl = {_r64(r) for r in exclude}
    pool = [r for r in GPR64 if r not in excl]
    if len(pool) < n:
        raise ValueError("not enough free registers to pick scratch from")
    return pool[:n]


def _sized(reg64, bits):
    return reg64 if bits == 64 else GPR32[reg64]


def _is_mem(operand):
    """True if `operand` is a memory-operand expression (e.g.
    'qword ptr [r10 + 0xd0]') rather than a bare register name. Capstone's
    rendered operand text round-trips byte-identically through Keystone
    (verified directly against real instructions from the driver binaries),
    so it's used here as-is with no manual reconstruction. Safe to read
    verbatim in every emit_* function below without excluding its base/index
    registers from scratch selection: in each function the memory read is
    always the FIRST instruction to touch any operand register (before rcx
    or any scratch register is clobbered), so correctness holds regardless
    of whether the memory operand's addressing registers happen to overlap
    dst/cnt/scratch -- there is nothing to alias yet at the point it's read."""
    return "[" in operand


def _src_text(src, bits):
    """Render a src operand (register name or memory-operand text) at the
    right size. Memory text already carries its own size qualifier (Capstone
    picks 'dword ptr' / 'qword ptr' to match the real instruction), so it is
    used verbatim; only a plain register name gets resized."""
    if _is_mem(src):
        return src
    return _sized(_r64(src), bits)


def _restore_or_discard(pushed_reg64, dests, flag_safe=False):
    """pop <reg> to restore it, unless it aliases a destination register
    (in which case the stale saved copy is simply discarded).

    flag_safe=True uses `lea rsp, [rsp+8]` instead of `add rsp, 8` to drop
    the slot -- ADD writes RFLAGS, which would corrupt a result the caller
    has already finalized (BZHI's CF/ZF, computed earlier and meant to be
    the last thing that touches flags); LEA never touches flags."""
    if pushed_reg64 in {_r64(d) for d in dests}:
        return "lea rsp, [rsp+8]" if flag_safe else "add rsp, 8"
    return f"pop {pushed_reg64}"


# ---------------------------------------------------------------------------
# SHRX / SHLX / SARX  --  dst = src >> cnt | src << cnt | src >>(arith) cnt
# No flags written by the VEX form -> must preserve RFLAGS exactly.
# ---------------------------------------------------------------------------
_SHIFT_OP = {"shrx": "shr", "shlx": "shl", "sarx": "sar"}


def emit_shiftx(mnemonic, dst, src, cnt, bits):
    op = _SHIFT_OP[mnemonic]
    dst64, cnt64 = _r64(dst), _r64(cnt)
    # src may be a register name or memory-operand text (see _is_mem); only
    # a register form needs to be kept out of the scratch pool -- a memory
    # operand's own addressing registers need no such exclusion (see
    # _is_mem's docstring for why).
    exclude = [dst64, cnt64, "rcx"]
    if not _is_mem(src):
        exclude.append(_r64(src))
    (scratch,) = pick_scratch(1, exclude)
    s = _sized(scratch, bits)
    d = _sized(dst64, bits)
    srcv = _src_text(src, bits)

    lines = [
        "pushfq",
        f"push {scratch}",
        f"push rcx",
        f"mov {s}, {srcv}",       # capture src BEFORE rcx is clobbered
        f"mov rcx, {cnt64}",      # safe even if cnt==rcx (mov rcx,rcx) or src==rcx (already captured)
        f"{op} {s}, cl",
        f"mov {d}, {s}",          # write result (scratch != dst by construction)
        _restore_or_discard("rcx", [dst64]),
        # LIFO unwind: push order was [flags(bottom), scratch, rcx(top)], so
        # pop order must be [rcx, scratch, flags] -- scratch before popfq,
        # never after (popfq-before-scratch would pop scratch's saved value
        # into RFLAGS instead of the real flags).
        f"pop {scratch}",
        "popfq",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# RORX  --  dst = ror(src, imm8).  No flags written.
# ---------------------------------------------------------------------------
def emit_rorx(dst, src, imm, bits):
    dst64, src64 = _r64(dst), _r64(src)
    (scratch,) = pick_scratch(1, [dst64, src64])
    s = _sized(scratch, bits)
    d = _sized(dst64, bits)
    srcv = _sized(src64, bits)
    lines = [
        "pushfq",
        f"push {scratch}",
        f"mov {s}, {srcv}",
        f"ror {s}, {imm}",
        f"mov {d}, {s}",
        f"pop {scratch}",   # LIFO: scratch (pushed last) before popfq
        "popfq",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MULX  --  Intel operand order is `mulx dst_high, dst_low, src`:
#   dst_high:dst_low = (implicit RDX) * src.
# (Verified empirically with Unicorn, not just from memory of the SDM text --
# `mulx op1, op2, src` with RDX=2, src=0x8000000000000000 gives op1=1,
# op2=0, i.e. op1 is the HIGH half, op2 is the LOW half.)
# No flags written. RDX is read, not clobbered as a source identity unless
# it also happens to be one of the destinations.
# ---------------------------------------------------------------------------
def emit_mulx(dst_high, dst_low, src, bits):
    dhi64, dlo64 = _r64(dst_high), _r64(dst_low)
    src64 = _r64(src)
    impl = "rdx" if bits == 64 else "edx"
    s1, s2 = pick_scratch(2, [dlo64, dhi64, "rax", "rdx"])
    ds1 = _sized(s1, bits)
    ds2 = _sized(s2, bits)
    srcv = _sized(src64, bits)
    lines = [
        "pushfq",
        "push rax",
        "push rdx",
        f"push {s1}",
        f"push {s2}",
        f"mov {ds1}, {srcv}",        # snapshot src before rax/rdx are touched
        f"mov {_sized('rax', bits)}, {impl}",  # rax = original implicit multiplicand
        f"mul {ds1}",                 # rdx:rax = rax * s1(=src)
        f"mov {ds2}, {_sized('rdx', bits)}",   # stash HIGH result
        f"mov {ds1}, {_sized('rax', bits)}",   # stash LOW result (s1 free again)
        f"mov {_sized(dhi64, bits)}, {ds2}",   # dst_high <- HIGH
        f"mov {_sized(dlo64, bits)}, {ds1}",   # dst_low  <- LOW
        _restore_or_discard(s2, [dlo64, dhi64]),
        _restore_or_discard(s1, [dlo64, dhi64]),
        _restore_or_discard("rdx", [dlo64, dhi64]),
        _restore_or_discard("rax", [dlo64, dhi64]),
        "popfq",
    ]
    return "\n".join(lines)



_label_counter = [0]


def _fresh_label(prefix):
    """A label unique across calls. NASM-style numeric local labels (1f/1:)
    are NOT supported by this Keystone build (confirmed: `jae 1f\\nnop\\n1:`
    fails to assemble on its own, deterministically, with no surrounding
    code at all) -- plain named labels work fine and are used everywhere
    instead. Unique names also make it safe to later concatenate several
    trampolines into one assembler call without label collisions."""
    _label_counter[0] += 1
    return f"{prefix}_{_label_counter[0]}"


def emit_bzhi(dst, src, idx, bits):
    """BZHI legacy equivalent, flags computed last so nothing after
    overwrites CF/ZF: compute n, compute result into dst, THEN set CF via
    cmp (re-done, side-effect-free re-derivation) and ZF falls out of a
    final `or dst, 0` / `test` done as the very last flag-affecting op,
    with CF fixed up immediately after via sahf-style trick is overkill --
    instead we build the flags with a single `bt`+`cmp` pair ordered so the
    last instruction executed is the one whose flags we want to keep: we
    reorder so TEST (for ZF) runs, and then we OR in CF using `stc`/`clc`
    is wrong (OR/STC would stomp it right back). Correct approach: compute
    CF into a GPR bit (via SETAE), compute result + ZF via a final TEST,
    then restore CF from the saved bit via `bt reg,0` into CF, in that
    exact order so TEST's flag writes land first and the CF fixup lands
    last."""
    # idx is always a register in BZHI's encoding (it's the VEX.vvvv field);
    # only src (the ModRM operand) can be memory -- see _is_mem's docstring
    # for why its addressing registers need no exclusion from the pool.
    dst64, idx64 = _r64(dst), _r64(idx)
    exclude = [dst64, idx64, "rcx"]
    if not _is_mem(src):
        exclude.append(_r64(src))
    # rcx excluded for the same reason as in emit_shiftx above: it's used
    # unconditionally for the shift count in the n<OperandSize branch.
    s1, s2, s3 = pick_scratch(3, exclude)
    d = _sized(dst64, bits)
    srcv = _src_text(src, bits)
    r1 = _sized(s1, bits)
    r2 = _sized(s2, bits)
    cfsave = s3  # 64-bit, holds 0/1 for "n >= OperandSize"
    opsz = bits
    skip_lbl = _fresh_label("bzhi_skip")
    lines = [
        f"push {s1}",
        f"push {s2}",
        f"push {s3}",
        "push rcx",
        # src is read FIRST, before s1/idx touches anything: if src is a
        # memory operand, its base/index registers must still hold their
        # original values when this executes (that's the guarantee _is_mem's
        # docstring relies on) -- reading idx into s1 before this would
        # clobber s1 if it happens to alias one of src's addressing
        # registers, corrupting the read (caught by the memory-operand
        # fuzz tests; this ordering is why shiftx needs no such care while
        # bzhi, which touches a scratch register for idx first, did).
        f"mov {r2}, {srcv}",                  # r2 = src snapshot
        f"mov {r1}, {_sized(idx64, bits)}",
        f"and {_sized(s1, 32)}, 0xff",       # s1(low32) = n, upper bits of r1 cleared by 32-bit and
        f"xor {cfsave}, {cfsave}",
        f"cmp {r1}, {opsz}",
        f"setae {GPR8[cfsave]}",                # cfsave = 1 if n>=OperandSize else 0 (future CF)
        f"jae {skip_lbl}",
        f"mov rcx, {s1}",
        f"mov {r1}, 1",
        f"shl {r1}, cl",
        f"sub {r1}, 1",
        f"and {r2}, {r1}",
        f"{skip_lbl}:",
        f"mov {d}, {r2}",          # final result in dst
        f"test {d}, {d}",          # sets ZF from the real result (also clears CF/OF -- fixed next)
        f"bt {cfsave}, 0",          # CF := bit 0 of cfsave (1 or 0), overwriting TEST's CF
        # flag_safe=True below: this is the last thing that touches RFLAGS
        # (no popfq follows, unlike shiftx/rorx/mulx), so any discard here
        # must use LEA, not ADD, or it would stomp the CF/ZF just set above.
        # (s1/s2/s3 are disjoint from dst by construction and so never hit
        # the discard path, but rcx can alias dst and does need this.)
        _restore_or_discard("rcx", [dst64], flag_safe=True),
        _restore_or_discard(s3, [dst64], flag_safe=True),
        _restore_or_discard(s2, [dst64], flag_safe=True),
        _restore_or_discard(s1, [dst64], flag_safe=True),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PDEP  --  dst = bits of src deposited into the positions marked by mask's
# set bits (all other dst bits 0). Operand order verified empirically with
# Unicorn: `pdep dst, src, mask` (src=VEX.vvvv, always a register; mask is
# the ModRM operand and CAN be memory, though neither real driver binary
# uses that form). No flags written -> preserve RFLAGS like shrx/rorx/mulx.
#
# Classic isolate-lowest-set-bit algorithm (same one compiler-rt's software
# __pdep fallback uses), which runs popcount(mask) iterations rather than a
# fixed 64:
#   result = 0; s = src; m = mask
#   while (m) { lsb = m & -m; if (s & 1) result |= lsb; s >>= 1; m &= m-1; }
# ---------------------------------------------------------------------------
def emit_pdep(dst, src, mask, bits):
    dst64 = _r64(dst)
    exclude = [dst64]
    if not _is_mem(src):
        exclude.append(_r64(src))
    if not _is_mem(mask):
        exclude.append(_r64(mask))
    acc, s, m, lsb = pick_scratch(4, exclude)
    d = _sized(dst64, bits)
    acc_s, s_s, m_s, lsb_s = (_sized(r, bits) for r in (acc, s, m, lsb))
    src_text = _src_text(src, bits)
    mask_text = _src_text(mask, bits)

    loop_lbl = _fresh_label("pdep_loop")
    done_lbl = _fresh_label("pdep_done")
    skip_or_lbl = _fresh_label("pdep_skip_or")

    lines = [
        "pushfq",
        f"push {acc}",
        f"push {s}",
        f"push {m}",
        f"push {lsb}",
        # mask (the only operand of the two that PDEP's encoding allows to be
        # memory -- src is always VEX.vvvv, always a register) is read FIRST,
        # before any scratch register is written, for the same reason BZHI's
        # src read was moved first: a later read's addressing registers must
        # not have already been clobbered by an earlier scratch write.
        f"mov {m_s}, {mask_text}",
        f"mov {s_s}, {src_text}",
        f"xor {acc_s}, {acc_s}",
        f"{loop_lbl}:",
        f"test {m_s}, {m_s}",
        f"jz {done_lbl}",
        f"mov {lsb_s}, {m_s}",
        f"neg {lsb_s}",
        f"and {lsb_s}, {m_s}",         # lsb = m & -m (lowest set bit of m)
        f"shr {s_s}, 1",                # CF = bit about to be tested; s >>= 1
        f"jnc {skip_or_lbl}",
        f"or {acc_s}, {lsb_s}",
        f"{skip_or_lbl}:",
        f"mov {lsb_s}, {m_s}",
        f"sub {lsb_s}, 1",
        f"and {m_s}, {lsb_s}",          # m &= m-1 (clear lowest set bit)
        f"jmp {loop_lbl}",
        f"{done_lbl}:",
        f"mov {d}, {acc_s}",            # final result -- written before any pop,
        # so a pop that aliases dst (handled via discard below) can't clobber it
        _restore_or_discard(lsb, [dst64]),
        _restore_or_discard(m, [dst64]),
        _restore_or_discard(s, [dst64]),
        _restore_or_discard(acc, [dst64]),
        "popfq",
    ]
    return "\n".join(lines)
