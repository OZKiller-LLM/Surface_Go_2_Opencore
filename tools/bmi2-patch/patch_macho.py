"""
Patch every BMI1/BMI2 instruction site found by scan_sites.py with a 5-byte
relative JMP into a legacy-equivalent trampoline (built by bmi_equiv.py),
appended in a new segment at the end of the file.

Usage: python3 patch_macho.py <input> <output>
"""
import os, struct, sys
import keystone as k
import capstone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bmi_equiv as be
import scan_sites as ss

ks64 = k.Ks(k.KS_ARCH_X86, k.KS_MODE_64)
PAGE = 0x1000
NEW_SEGNAME = "__BMI2FIX"
NEW_SECTNAME = "__text"


def page_align(x):
    return (x + PAGE - 1) & ~(PAGE - 1)


# ---------------------------------------------------------------------------
# Trampoline assembly text generation (one site -> one body of asm text,
# ending in a jmp back to the instruction right after the original site).
# ---------------------------------------------------------------------------
def _riprel_wrapper(site, inner_text_fn):
    """For the one RIP-relative site: the memory operand's address is only
    valid relative to the ORIGINAL instruction's own position, which is
    meaningless once the read happens from a trampoline elsewhere in the
    file. Load the resolved absolute address into a dedicated scratch
    register picked disjoint from every OTHER role-register this site has
    (never from dst, so the final `pop` is always safe; never from any
    other named operand, so the preamble's overwrite can't silently
    corrupt a value the real instruction still needs to read), dereference
    it into that same register, then call the normal emit_* function
    treating that register as an ordinary register `src` (which is all
    emit_* ever assumes of a register src: it's read, never required to be
    restored by the callee, exactly matching how a real register-form BMI
    instruction treats it).

    Excluding only dst/cnt/rcx here once missed bzhi's `idx` entirely (this
    function originally only checked for a "cnt" key, which shiftx sites
    have but bzhi sites don't -- they have "idx" instead): on the one real
    riprel site, which is a bzhi with idx=rax, addr_reg was then free to be
    chosen as rax too, so the preamble's own `mov rax, [rax]` silently
    overwrote the real idx value with the just-loaded src value before
    bzhi's generator ever got to read it, corrupting CF and the result's
    upper bits whenever n was meant to be >= OperandSize. Caught by the
    real-binary verification, not by the generic fuzz tests (which have no
    riprel cases to generate at all -- there's only one in either binary).
    Now excludes every named register-valued role the site has, not just
    the ones specific to whichever mnemonic motivated the original code."""
    bits = site["bits"]
    exclude = ["rcx"]
    for key in ("dst", "dst_high", "dst_low", "cnt", "idx", "src", "mask"):
        if key in site and not be._is_mem(site[key]):
            exclude.append(be._r64(site[key]))
    (addr_reg,) = be.pick_scratch(1, exclude)
    sized = be._sized(addr_reg, bits)
    preamble = [
        f"push {addr_reg}",
        f"movabs {addr_reg}, {site['riprel_abs_addr']}",
        f"mov {sized}, [{addr_reg}]",
    ]
    inner = inner_text_fn(addr_reg)
    epilogue = [f"pop {addr_reg}"]
    return "\n".join(preamble + [inner] + epilogue)


def build_trampoline_asm(site):
    bits = site["bits"]
    m = site["mnemonic"]

    if m in ("shrx", "shlx", "sarx"):
        def make(src):
            return be.emit_shiftx(m, site["dst"], src, site["cnt"], bits)
    elif m == "rorx":
        def make(src):
            return be.emit_rorx(site["dst"], src, site["imm"], bits)
    elif m == "mulx":
        def make(src):
            return be.emit_mulx(site["dst_high"], site["dst_low"], src, bits)
    elif m == "bzhi":
        def make(src):
            return be.emit_bzhi(site["dst"], src, site["idx"], bits)
    elif m == "pdep":
        def make(src):
            return be.emit_pdep(site["dst"], site["src"], src, bits)
            # (pdep's riprel-replaceable operand would be `mask`; src is
            # always a register per the encoding, see emit_pdep's docstring)
    else:
        raise AssertionError(m)

    if site["riprel"]:
        body = _riprel_wrapper(site, make)
    else:
        src_field = "mask" if m == "pdep" else "src"
        body = make(site[src_field])

    resume_addr = site["address"] + site["size"]
    return body + f"\njmp 0x{resume_addr:x}"


# ---------------------------------------------------------------------------
# Load commands whose fields are absolute file offsets into __LINKEDIT.
# When __LINKEDIT is relocated, every one of these must be shifted by the
# same delta, or they point at garbage (or, worse, at bytes that happen to
# decode as something else) in the moved file.
# ---------------------------------------------------------------------------
LC_SYMTAB = 0x2
LC_DYSYMTAB = 0xb
LC_FUNCTION_STARTS = 0x26
LC_DATA_IN_CODE = 0x29
LC_SEGMENT_SPLIT_INFO = 0x1e
LC_DYLIB_CODE_SIGN_DRS = 0x2b
LC_DYLD_EXPORTS_TRIE = 0x80000033
LC_DYLD_CHAINED_FIXUPS = 0x80000034
LC_DYLD_INFO = 0x22
LC_DYLD_INFO_ONLY = 0x80000022
# linkedit_data_command shape: cmd, cmdsize, dataoff, datasize
_LINKEDIT_DATA_CMDS = {
    LC_FUNCTION_STARTS, LC_DATA_IN_CODE, LC_SEGMENT_SPLIT_INFO,
    LC_DYLIB_CODE_SIGN_DRS, LC_DYLD_EXPORTS_TRIE, LC_DYLD_CHAINED_FIXUPS,
}


def _walk_commands(data, ncmds, header_size):
    """Yields (offset, cmd, cmdsize) for every load command."""
    off = header_size
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, off)
        yield off, cmd, cmdsize
        off += cmdsize


def _shift_linkedit_offset_fields(data, ncmds, header_size, delta):
    """Shifts every load-command field holding an absolute file offset into
    __LINKEDIT by `delta`, matching __LINKEDIT's own relocation. A 0 value
    in one of these fields conventionally means "not present" and must stay
    0, never gain delta."""
    def bump(offset):
        (val,) = struct.unpack_from("<I", data, offset)
        if val != 0:
            struct.pack_into("<I", data, offset, val + delta)

    for off, cmd, cmdsize in list(_walk_commands(data, ncmds, header_size)):
        if cmd == LC_SYMTAB:
            bump(off + 8)   # symoff
            bump(off + 16)  # stroff
        elif cmd == LC_DYSYMTAB:
            for field_off in (32, 40, 48, 56, 64, 72):
                # tocoff, modtaboff, extrefsymoff, indirectsymoff,
                # extreloff, locreloff
                bump(off + field_off)
        elif cmd in _LINKEDIT_DATA_CMDS:
            bump(off + 8)   # dataoff
        elif cmd in (LC_DYLD_INFO, LC_DYLD_INFO_ONLY):
            for field_off in (8, 16, 24, 32, 40):
                # rebase_off, bind_off, weak_bind_off, lazy_bind_off,
                # export_off
                bump(off + field_off)


# ---------------------------------------------------------------------------
# Patch driver
# ---------------------------------------------------------------------------
def patch_file(in_path, out_path, verbose=True):
    data, ncmds, sizeofcmds, header_size, segments, sites = ss.scan_file(in_path)
    data = bytearray(data)
    if verbose:
        print(f"{in_path}: {len(sites)} BMI2 sites found")

    cmds_end = header_size + sizeofcmds
    # Zero-fill sections (__bss, __common, __thread_bss) legitimately report
    # offset=0 (no file-backed content) -- exclude them, or they'd make this
    # look like there's no slack at all.
    first_section_foff = min(
        sect["offset"]
        for seg in segments for sect in seg["sections"]
        if sect["offset"] != 0
    )
    slack = first_section_foff - cmds_end
    NEW_CMD_SIZE = 72 + 80  # segment_command_64 + one section_64
    if slack < NEW_CMD_SIZE:
        raise SystemExit(
            f"not enough header slack ({slack} bytes) for the new segment "
            f"command ({NEW_CMD_SIZE} bytes) -- would require shifting "
            f"existing file content, which this patcher deliberately does "
            f"not attempt"
        )

    # __LINKEDIT must end up the LAST thing in the file: codesign's own
    # structural ("strict") validation of a Mach-O requires nothing to
    # follow it, since it appends the code signature directly after
    # __LINKEDIT's content. The straightforward approach -- append the new
    # __BMI2FIX segment at the then-current end of file -- places it AFTER
    # __LINKEDIT instead, which codesign rejects outright with "main
    # executable failed strict validation" (confirmed against the real
    # patched files on real hardware: codesign ran, but refused to sign).
    # Fix: cut __LINKEDIT off the end, append __BMI2FIX where __LINKEDIT
    # used to start, then re-append __LINKEDIT's own bytes after that --
    # shifting every load-command field that stores an absolute file
    # offset into __LINKEDIT by the same delta its segment command gets.
    linkedit = next(seg for seg in segments if seg["segname"] == "__LINKEDIT")
    old_linkedit_fileoff = linkedit["fileoff"]
    linkedit_filesize = linkedit["filesize"]
    assert old_linkedit_fileoff + linkedit_filesize == len(data), (
        "__LINKEDIT is not the last thing in the input file -- this "
        "patcher assumes it is (true for both real driver binaries) and "
        "would need a different relocation strategy otherwise"
    )
    linkedit_bytes = bytes(data[old_linkedit_fileoff:old_linkedit_fileoff + linkedit_filesize])
    del data[old_linkedit_fileoff:]

    max_vm_end = max(seg["vmaddr"] + seg["vmsize"] for seg in segments)
    new_vmaddr = page_align(max_vm_end)
    new_fileoff = page_align(len(data))

    # --- assemble every trampoline sequentially, each one's address known
    # only once all prior trampolines' sizes are known (their own trailing
    # jmp needs the correct address to compute its rel32) ---
    tramp_bytes = bytearray()
    site_tramp_addr = {}
    for site in sites:
        asm_text = build_trampoline_asm(site)
        addr = new_vmaddr + len(tramp_bytes)
        try:
            encoding, _ = ks64.asm(asm_text, addr)
        except Exception as e:
            raise SystemExit(
                f"failed to assemble trampoline for site at "
                f"0x{site['address']:x} ({site['mnemonic']} {site['op_str']}):"
                f" {e}\n--- asm ---\n{asm_text}"
            )
        site_tramp_addr[site["address"]] = addr
        tramp_bytes += bytes(encoding)
        # 8-byte align each trampoline's start for tidiness/debuggability
        pad = (-len(tramp_bytes)) % 8
        tramp_bytes += b"\x90" * pad

    if verbose:
        print(f"  trampolines: {len(tramp_bytes)} bytes @ vmaddr 0x{new_vmaddr:x}, "
              f"fileoff 0x{new_fileoff:x}")

    # --- patch each site: E9 rel32 (5-byte relative jmp), NOP-fill any
    # remaining bytes of a longer original instruction ---
    for site in sites:
        tramp_addr = site_tramp_addr[site["address"]]
        jmp_site_addr = site["address"]
        rel32 = tramp_addr - (jmp_site_addr + 5)
        patched = b"\xE9" + struct.pack("<i", rel32)
        patched += b"\x90" * (site["size"] - 5)
        assert len(patched) == site["size"]
        data[site["file_offset"]:site["file_offset"] + site["size"]] = patched

    # --- append trampoline content at end of file ---
    content_start = len(data)
    if content_start < new_fileoff:
        data += b"\x00" * (new_fileoff - content_start)
    data += tramp_bytes
    new_filesize = len(tramp_bytes)

    # --- re-append __LINKEDIT after __BMI2FIX, now the true end of file,
    # and shift every load command field that points into it ---
    new_linkedit_fileoff = page_align(len(data))
    if len(data) < new_linkedit_fileoff:
        data += b"\x00" * (new_linkedit_fileoff - len(data))
    data += linkedit_bytes
    linkedit_delta = new_linkedit_fileoff - old_linkedit_fileoff
    struct.pack_into("<Q", data, linkedit["cmd_offset"] + 40, new_linkedit_fileoff)
    _shift_linkedit_offset_fields(data, ncmds, header_size, linkedit_delta)

    if verbose:
        print(f"  __LINKEDIT moved: fileoff 0x{old_linkedit_fileoff:x} -> "
              f"0x{new_linkedit_fileoff:x} (delta +0x{linkedit_delta:x})")

    # --- insert the new LC_SEGMENT_64 (+ one section) into header slack ---
    segname_b = NEW_SEGNAME.encode().ljust(16, b"\x00")
    sectname_b = NEW_SECTNAME.encode().ljust(16, b"\x00")
    segsectname_b = NEW_SEGNAME.encode().ljust(16, b"\x00")
    VM_PROT_READ, VM_PROT_EXECUTE = 1, 4
    seg_cmd = struct.pack(
        "<II16sQQQQiiII",
        0x19, NEW_CMD_SIZE,                       # cmd, cmdsize
        segname_b,
        new_vmaddr, page_align(new_filesize),      # vmaddr, vmsize
        new_fileoff, new_filesize,                 # fileoff, filesize
        VM_PROT_READ | VM_PROT_EXECUTE,             # maxprot
        VM_PROT_READ | VM_PROT_EXECUTE,             # initprot
        1, 0,                                        # nsects, flags
    )
    S_ATTR_PURE_INSTRUCTIONS = 0x80000000
    S_ATTR_SOME_INSTRUCTIONS = 0x00000400
    sect_cmd = struct.pack(
        "<16s16sQQIIIIIIII",
        sectname_b, segsectname_b,
        new_vmaddr, new_filesize,
        new_fileoff, 4,                              # offset, align(2^4=16)
        0, 0,                                          # reloff, nreloc
        S_ATTR_PURE_INSTRUCTIONS | S_ATTR_SOME_INSTRUCTIONS,
        0, 0, 0,                                        # reserved1-3
    )
    new_cmd = seg_cmd + sect_cmd
    assert len(new_cmd) == NEW_CMD_SIZE

    # OVERWRITE the slack bytes in place -- same length in, same length out
    # -- never insert here. An insertion would grow the bytearray and shift
    # every byte after cmds_end (the jmp patches already written into
    # __text, and the entire rest of the file) by len(new_cmd), silently
    # desynchronizing every file offset in the Mach-O from the section/
    # segment metadata that describes them. (Caught this exact bug via
    # verify_patched_file.py: the jmp bytes it read back at each site's
    # file_offset were garbage because an earlier version of this function
    # inserted instead of overwrote.)
    assert cmds_end + len(new_cmd) <= first_section_foff, (
        "new load command overran the start of section data -- this should "
        "be unreachable given the slack check above"
    )
    data[cmds_end:cmds_end + len(new_cmd)] = new_cmd
    struct.pack_into("<I", data, 16, ncmds + 1)          # ncmds
    struct.pack_into("<I", data, 20, sizeofcmds + len(new_cmd))  # sizeofcmds

    assert new_linkedit_fileoff + linkedit_filesize == len(data), (
        "__LINKEDIT is not the last thing in the output file -- codesign "
        "would reject this the same way it rejected the first version"
    )

    with open(out_path, "wb") as f:
        f.write(data)

    if verbose:
        print(f"  wrote {out_path} ({len(data)} bytes, was {len(bytearray(open(in_path,'rb').read()))})")
    return len(sites)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: patch_macho.py <input> <output>")
        sys.exit(1)
    patch_file(sys.argv[1], sys.argv[2])
