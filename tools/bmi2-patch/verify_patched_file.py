"""
Prove that the ACTUAL trampoline bytes written into a patched binary (not a
freshly-regenerated copy -- the exact bytes shipped) produce results
identical to the original BMI2 instruction, for every single site found in
that binary. Every site gets at least one realistic-input execution check
(exercising the real scan+assemble+link pipeline end to end); a spot-check
subset (every pdep site, the riprel site, and a sample of the rest) gets
the same multi-trial randomized fuzzing verify_equiv.py used to prove the
generator itself, run here against the real shipped bytes instead of a
freshly generated copy.
"""
import os, random, struct, sys
import capstone
import keystone as k
import unicorn as u
from unicorn.x86_const import *

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bmi_equiv as be
import scan_sites as ss
import patch_macho as pm

ks64 = k.Ks(k.KS_ARCH_X86, k.KS_MODE_64)

REG_MAP = {
    "rax": UC_X86_REG_RAX, "rbx": UC_X86_REG_RBX, "rcx": UC_X86_REG_RCX,
    "rdx": UC_X86_REG_RDX, "rsi": UC_X86_REG_RSI, "rdi": UC_X86_REG_RDI,
    "rbp": UC_X86_REG_RBP, "r8": UC_X86_REG_R8, "r9": UC_X86_REG_R9,
    "r10": UC_X86_REG_R10, "r11": UC_X86_REG_R11, "r12": UC_X86_REG_R12,
    "r13": UC_X86_REG_R13, "r14": UC_X86_REG_R14, "r15": UC_X86_REG_R15,
}
ALL_REGS = list(REG_MAP)

SITE_CODE_BASE = 0x400000    # where we place a COPY of the original instruction
STACK_BASE = 0x700000
STACK_SIZE = 0x4000
DATA_BASE = 0x600000
DATA_SIZE = 0x3000


def rand_val(bits):
    return random.getrandbits(bits)


def mask(bits):
    return (1 << bits) - 1


def run_in_unicorn(code_bytes, code_addr, stop_addr, init_regs, flags,
                    extra_maps=None):
    mu = u.Uc(u.UC_ARCH_X86, u.UC_MODE_64)
    code_page = code_addr & ~0xFFF
    code_map_size = ((code_addr + len(code_bytes) + 0x1000 - code_page + 0xFFF) // 0x1000) * 0x1000
    mu.mem_map(code_page, max(code_map_size, 0x2000))
    mu.mem_write(code_addr, code_bytes + b"\x90" * 32)
    # stop_addr can be a completely different, far-away page from code_addr
    # (a trampoline's real address is in the new high-vmaddr segment; the
    # resume_addr it jumps back to is the original low-vmaddr __text site).
    # UC_HOOK_CODE must successfully fetch the instruction at stop_addr
    # before its callback can fire at all, so that page needs to be mapped
    # too, or the fetch itself faults before the hook ever runs.
    stop_page = stop_addr & ~0xFFF
    if stop_page < code_page or stop_page >= code_page + code_map_size:
        mu.mem_map(stop_page, 0x1000)
        mu.mem_write(stop_page, b"\x90" * 0x1000)
    mu.mem_map(STACK_BASE, STACK_SIZE)
    if extra_maps:
        for base, data in extra_maps:
            map_size = ((len(data) + 0xFFF) // 0x1000) * 0x1000
            mu.mem_map(base, max(0x1000, map_size))
            mu.mem_write(base, data)

    for reg, val in init_regs.items():
        if reg == "_flags":
            continue
        mu.reg_write(REG_MAP[reg], val)
    mu.reg_write(UC_X86_REG_RSP, STACK_BASE + STACK_SIZE - 0x1000)
    mu.reg_write(UC_X86_REG_RFLAGS, flags)

    def stop_hook(uc, address, size, _):
        # Exact match, not >=: a trampoline's real address (in the new
        # high-vmaddr segment) can be numerically LARGER than the
        # resume_addr it jumps back to in __text, so '>=' would fire on the
        # trampoline's very first instruction instead of only at the real
        # exit point. (Caught via a manual isolated repro after this test
        # harness reported near-universal failures that the generator
        # itself -- proven correct in verify_equiv.py -- could not explain.)
        if address == stop_addr:
            uc.emu_stop()
    # No begin/end restriction: stop_addr can be outside the trampoline's
    # own code page (see the stop_page mapping above for why), so the hook
    # must be able to fire there too.
    mu.hook_add(u.UC_HOOK_CODE, stop_hook)

    mu.emu_start(code_addr, stop_addr + 0x40, count=20000)
    out = {r: mu.reg_read(REG_MAP[r]) for r in ALL_REGS}
    out["_flags"] = mu.reg_read(UC_X86_REG_RFLAGS)
    return out


def build_inputs(site, trial_seed):
    """One concrete set of initial register values + (optional) memory
    content for this site, deterministic given trial_seed."""
    rnd = random.Random(hash(trial_seed))
    before = {r: rnd.getrandbits(64) for r in ALL_REGS}
    before["_flags"] = rnd.choice([0x202, 0x246, 0x293, 0x0])
    bits = site["bits"]

    if "cnt" in site:
        before[be._r64(site["cnt"])] = rnd.choice(
            [0, 1, bits - 1, bits, bits + 5, rnd.getrandbits(6)])
    if "idx" in site:
        n = rnd.choice([0, 1, bits - 1, bits, bits + 1, 255, rnd.getrandbits(8)])
        before[be._r64(site["idx"])] = (before[be._r64(site["idx"])] & ~0xFF) | n

    extra_maps = None
    mem_val = None
    mi = site.get("mem_info")
    if mi and not site["riprel"]:
        data_offset = 0x1000
        mem_val = rnd.getrandbits(64)
        data = bytearray(DATA_SIZE)
        data[data_offset:data_offset + 8] = mem_val.to_bytes(8, "little")
        # Solve for base/index register values so that
        # base + index*scale + disp == DATA_BASE + data_offset, i.e. the
        # effective address really lands on the data we just wrote --
        # using the REAL disp/scale from the instruction (via mem_info)
        # rather than assuming a zero displacement.
        index_val = 1 if mi["index"] else 0
        base_val = DATA_BASE + data_offset - mi["disp"] - index_val * mi["scale"]
        before[be._r64(mi["base"])] = base_val
        if mi["index"]:
            before[be._r64(mi["index"])] = index_val
        extra_maps = [(DATA_BASE, bytes(data))]
    elif site["riprel"]:
        page = site["riprel_abs_addr"] & ~0xFFF
        data = bytearray(0x1000)
        off = site["riprel_abs_addr"] - page
        mem_val = rnd.getrandbits(64)
        data[off:off + 8] = mem_val.to_bytes(8, "little")
        extra_maps = [(page, bytes(data))]

    return before, extra_maps, mem_val


def bzhi_reference(src, idx, bits):
    """Same independent spec-based reference used in verify_equiv.py --
    needed here too, for the same reason: this Unicorn/QEMU build's own
    32-bit bzhi TCG implementation is itself buggy (for n>=32 it clears
    bit 31 of the result, confirmed by direct repro), so the REAL VEX
    instruction's own output (orig_out) cannot be trusted as ground truth
    specifically for bzhi -- every failure this file ever reported on a
    bzhi site, across both the generic generator tests and this real-binary
    check, has had that exact signature (only the top bit of the result
    differs)."""
    mask_ = (1 << bits) - 1
    src &= mask_
    n = idx & 0xFF
    if n >= bits:
        return src, 1, int(src == 0)
    result = src & ((1 << n) - 1)
    return result, 0, int(result == 0)


def check(site, orig_out, legacy_out, before, mem_val=None):
    bits = site["bits"]
    dests = set()
    for key in ("dst", "dst_high", "dst_low"):
        if key in site:
            dests.add(be._r64(site[key]))
    problems = []

    if site["mnemonic"] == "bzhi":
        src_val = mem_val if mem_val is not None else before[be._r64(site["src"])]
        ref_result, ref_cf, ref_zf = bzhi_reference(src_val, before[be._r64(site["idx"])], bits)
        dst64 = be._r64(site["dst"])
        lv = legacy_out[dst64] & mask(bits)
        if lv != ref_result:
            problems.append(f"dest {dst64}: ref=0x{ref_result:x} legacy=0x{lv:x}")
        CF, ZF = 1, 0x40
        if (legacy_out["_flags"] & CF) != ref_cf:
            problems.append(f"CF: ref={ref_cf} legacy={legacy_out['_flags'] & CF}")
        if bool(legacy_out["_flags"] & ZF) != bool(ref_zf):
            problems.append(f"ZF: ref={ref_zf} legacy={bool(legacy_out['_flags'] & ZF)}")
    else:
        for r in ALL_REGS:
            if r not in dests:
                continue
            ov = orig_out[r] & mask(bits)
            lv = legacy_out[r] & mask(bits)
            if ov != lv:
                problems.append(f"dest {r}: orig=0x{ov:x} legacy=0x{lv:x}")
        if legacy_out["_flags"] != orig_out["_flags"]:
            problems.append(f"flags: orig=0x{orig_out['_flags']:x} legacy=0x{legacy_out['_flags']:x}")

    for r in ALL_REGS:
        if r in dests:
            continue
        if legacy_out[r] != before.get(r, 0):
            problems.append(f"leak into {r}")
    return problems


def verify(orig_path, patched_path, trials_per_spotcheck=40, verbose_fail_limit=10):
    orig_data, *_ , sites = ss.scan_file(orig_path)
    patched_data = open(patched_path, "rb").read()

    _, _, _, patched_segments = ss.parse_macho_header(patched_data)
    new_seg = next(s for s in patched_segments if s["segname"] == pm.NEW_SEGNAME)

    fails = 0
    checked = 0
    fail_details = []

    for i, site in enumerate(sites):
        # decode the actual 5-byte jmp written at this site in the patched file
        jmp_bytes = patched_data[site["file_offset"]:site["file_offset"] + 5]
        assert jmp_bytes[0] == 0xE9, f"site {i} not patched with E9 jmp: {jmp_bytes.hex()}"
        rel32 = struct.unpack("<i", jmp_bytes[1:])[0]
        tramp_addr = site["address"] + 5 + rel32
        assert new_seg["vmaddr"] <= tramp_addr < new_seg["vmaddr"] + new_seg["vmsize"], \
            f"site {i} trampoline target 0x{tramp_addr:x} outside new segment"
        tramp_file_off = new_seg["fileoff"] + (tramp_addr - new_seg["vmaddr"])
        resume_addr = site["address"] + site["size"]

        # Find the trampoline's own real length by disassembling forward
        # until its trailing jmp back to resume_addr. NOT just "the first
        # jmp mnemonic" -- pdep's own generated code has an unconditional
        # `jmp loop_lbl` for its internal loop's back-edge, which is a
        # DIFFERENT jmp that appears earlier and targets a LOCAL label, not
        # resume_addr (caught via this exact confusion on a real pdep site:
        # it matched that backward loop jmp and so produced a truncated,
        # wrong tramp_len). Match on the target address instead, which is
        # unambiguous since internal jmps/branches never target resume_addr.
        probe = patched_data[tramp_file_off:tramp_file_off + 0x400]
        tramp_len = None
        for ins in capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64).disasm(probe, tramp_addr):
            if ins.mnemonic == "jmp" and ins.op_str == f"0x{resume_addr:x}":
                tramp_len = (ins.address - tramp_addr) + ins.size
                break
        assert tramp_len is not None, f"site {i} trampoline has no tail jmp to resume_addr within 0x400 bytes"

        # Unicorn/QEMU bug, confirmed independent of this project's own code
        # (minimal repro: two mapped regions both near the top of the 48-bit
        # canonical address space -- which both a trampoline's real vmaddr
        # and a real __text resume_addr are, since both come from the
        # original dyld shared cache's address layout -- corrupts execute
        # permissions on the second one, even though mem_read/mem_write on
        # it work fine). Worked around here, not in the trampoline itself:
        # the real jmp's target byte-correctness is already checked above
        # via the site's own E9/rel32 decode, so for executing the
        # trampoline's OWN instructions in isolation it's enough to
        # redirect a LOCAL COPY's tail jmp to a sentinel address within the
        # same already-mapped page, rather than mapping resume_addr's real
        # (separate, equally-high) address a second time.
        sentinel = tramp_addr + 0x300
        assert sentinel > tramp_addr + tramp_len, "sentinel overlaps trampoline body"
        redirect_enc, _ = ks64.asm(f"jmp 0x{sentinel:x}", tramp_addr + tramp_len - 5)
        assert len(redirect_enc) == 5, "redirected jmp didn't assemble to 5 bytes"
        tramp_bytes = bytearray(probe[:tramp_len])
        tramp_bytes[tramp_len - 5:tramp_len] = bytes(redirect_enc)
        resume_addr_for_run = sentinel

        n_trials = trials_per_spotcheck if (
            site["mnemonic"] in ("pdep",) or site["riprel"] or i % 15 == 0
        ) else 1

        site_ok = True
        for t in range(n_trials):
            before, extra_maps, mem_val = build_inputs(site, trial_seed=(i, t))

            # original: assemble the UNMODIFIED bytes and run. For a
            # RIP-relative site this MUST run at the real original address
            # (not the fixed SITE_CODE_BASE test address), since the
            # instruction's displacement is position-dependent -- running
            # it anywhere else makes it read from a different, likely
            # unmapped, computed address. The real site address and its
            # riprel target (already mapped via extra_maps) are close
            # enough together (confirmed: a few MB apart here) that they
            # don't trigger the far-apart-ultra-high-regions Unicorn bug
            # worked around elsewhere in this file.
            orig_code_addr = site["address"] if site["riprel"] else SITE_CODE_BASE
            orig_out = run_in_unicorn(
                site["orig_bytes"], orig_code_addr,
                orig_code_addr + site["size"],
                before, before["_flags"], extra_maps)

            # legacy: the trampoline bytes exactly as they sit in the
            # patched file, placed at their REAL vmaddr (so any absolute
            # movabs/jmp inside resolves correctly), run from there, with
            # the stop point set to the (patched) site's own resume_addr
            legacy_out = run_in_unicorn(
                bytes(tramp_bytes), tramp_addr, resume_addr_for_run,
                before, before["_flags"], extra_maps)

            problems = check(site, orig_out, legacy_out, before, mem_val)
            if problems:
                site_ok = False
                if fails < verbose_fail_limit:
                    fail_details.append(
                        f"site[{i}] 0x{site['address']:x} {site['mnemonic']} "
                        f"{site['op_str']} trial {t}: " + "; ".join(problems))
                fails += 1
                break
        checked += 1

    for d in fail_details:
        print("FAIL", d)
    total_point_checks = sum(
        (trials_per_spotcheck if (s["mnemonic"] in ("pdep",) or s["riprel"] or i % 15 == 0) else 1)
        for i, s in enumerate(sites)
    )
    print(f"{patched_path}: {checked} sites checked, {fails} failed "
          f"({total_point_checks} total executions)")
    return fails == 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: verify_patched_file.py <original> <patched>")
        sys.exit(1)
    random.seed(99)
    ok = verify(sys.argv[1], sys.argv[2])
    sys.exit(0 if ok else 1)
