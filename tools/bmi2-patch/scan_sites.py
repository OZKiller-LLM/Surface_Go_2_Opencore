"""
Scan a Mach-O x86_64 binary's __TEXT,__text section for VEX-encoded
BMI1/BMI2 instructions and extract full, structured operand information for
each site -- everything bmi_equiv.py's emit_* functions need to generate a
legacy replacement, plus what the patcher needs to splice it in.
"""
import struct
import capstone
from capstone import x86


BMI_MNEMONICS = {"shrx", "shlx", "sarx", "rorx", "mulx", "bzhi", "pdep"}


def parse_macho_header(data):
    """Returns (ncmds, sizeofcmds, header_size, segments) where segments is
    a list of dicts with segname, vmaddr, vmsize, fileoff, filesize, and
    sections (list of dicts: sectname, segname, addr, size, offset)."""
    magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags, reserved = \
        struct.unpack_from("<IiiIIIII", data, 0)
    assert magic == 0xFEEDFACF, f"not a 64-bit Mach-O (magic={hex(magic)})"
    header_size = 32
    segments = []
    off = header_size
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, off)
        if cmd == 0x19:  # LC_SEGMENT_64
            segname = data[off + 8:off + 24].rstrip(b"\0").decode()
            vmaddr, vmsize, fileoff, filesize, maxprot, initprot, nsects, sflags = \
                struct.unpack_from("<QQQQiiII", data, off + 24)
            sections = []
            s = off + 72
            for _ in range(nsects):
                sectname = data[s:s + 16].rstrip(b"\0").decode()
                secsegname = data[s + 16:s + 32].rstrip(b"\0").decode()
                addr, sz, foff, align, reloff, nreloc, sflags2 = \
                    struct.unpack_from("<QQIIIII", data, s + 32)
                sections.append({
                    "sectname": sectname, "segname": secsegname,
                    "addr": addr, "size": sz, "offset": foff,
                })
                s += 80
            segments.append({
                "segname": segname, "vmaddr": vmaddr, "vmsize": vmsize,
                "fileoff": fileoff, "filesize": filesize,
                "cmd_offset": off, "cmd_size": size, "sections": sections,
            })
        off += size
    return ncmds, sizeofcmds, header_size, segments


def find_text_section(segments):
    for seg in segments:
        if seg["segname"] != "__TEXT":
            continue
        for sect in seg["sections"]:
            if sect["sectname"] == "__text":
                return sect["addr"], sect["offset"], sect["size"]
    raise SystemExit("no __TEXT,__text section found")


def _reg_name(md, reg_id):
    return md.reg_name(reg_id) if reg_id != 0 else None


def _mem_text(ins, op):
    """Reconstruct the memory operand's assembly text by slicing it out of
    Capstone's op_str (the only reliable way to get Keystone-compatible
    text without re-deriving formatting rules by hand -- verified earlier
    to round-trip byte-identically through Keystone). Each BMI mnemonic
    here has exactly one memory-capable operand, so its text is whichever
    comma-separated field of op_str contains a '['."""
    for field in ins.op_str.split(", "):
        if "[" in field:
            return field.strip()
    raise ValueError(f"no memory field found in op_str: {ins.op_str!r}")


def scan(data, text_addr, text_offset, text_bytes):
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    md.skipdata = True

    sites = []
    for ins in md.disasm(text_bytes, text_addr):
        if ins.mnemonic not in BMI_MNEMONICS:
            continue
        # Confirm this is really the VEX-encoded form (not a same-named
        # legacy mnemonic -- doesn't apply here, but keep the same check
        # used throughout this project for consistency).
        b = ins.bytes
        i = 0
        while i < len(b) and b[i] in (0x66, 0x67, 0xF2, 0xF3, 0x2E, 0x3E,
                                       0x26, 0x36, 0x64, 0x65, 0xF0):
            i += 1
        if i >= len(b) or b[i] not in (0xC4, 0xC5):
            continue

        ops = ins.operands
        mem_op = next((o for o in ops if o.type == x86.X86_OP_MEM), None)
        riprel = (mem_op is not None and mem_op.mem.base == x86.X86_REG_RIP)
        riprel_abs_addr = None
        if riprel:
            riprel_abs_addr = ins.address + ins.size + mem_op.mem.disp

        # Structured memory-operand info (base/index reg names, scale, disp)
        # kept alongside the rendered text -- the text is what's handed to
        # bmi_equiv/Keystone (verified to round-trip byte-identically), but
        # a test harness needs the structured form to compute the real
        # effective address when setting up base/index registers, rather
        # than re-parsing the text or assuming a zero displacement.
        mem_info = None
        if mem_op is not None and not riprel:
            mem_info = {
                "base": _reg_name(md, mem_op.mem.base),
                "index": _reg_name(md, mem_op.mem.index),
                "scale": mem_op.mem.scale,
                "disp": mem_op.mem.disp,
            }

        site = {
            "address": ins.address,
            "file_offset": text_offset + (ins.address - text_addr),
            "size": ins.size,
            "orig_bytes": bytes(ins.bytes),
            "mnemonic": ins.mnemonic,
            "op_str": ins.op_str,
            "riprel": riprel,
            "riprel_abs_addr": riprel_abs_addr,
            "mem_info": mem_info,
        }

        # bits: operand width, taken from the first register operand's size
        reg_op = next(o for o in ops if o.type == x86.X86_OP_REG)
        site["bits"] = reg_op.size * 8

        if ins.mnemonic in ("shrx", "shlx", "sarx"):
            dst = _reg_name(md, ops[0].reg)
            src = _mem_text(ins, mem_op) if mem_op else _reg_name(md, ops[1].reg)
            cnt = _reg_name(md, ops[2].reg)
            site.update(dst=dst, src=src, cnt=cnt)
        elif ins.mnemonic == "rorx":
            dst = _reg_name(md, ops[0].reg)
            src = _mem_text(ins, mem_op) if mem_op else _reg_name(md, ops[1].reg)
            imm = ops[2].imm
            site.update(dst=dst, src=src, imm=imm)
        elif ins.mnemonic == "mulx":
            # operand order dst_high, dst_low, src (verified empirically --
            # see bmi_equiv.emit_mulx's docstring)
            dst_high = _reg_name(md, ops[0].reg)
            dst_low = _reg_name(md, ops[1].reg)
            src = _mem_text(ins, mem_op) if mem_op else _reg_name(md, ops[2].reg)
            site.update(dst_high=dst_high, dst_low=dst_low, src=src)
        elif ins.mnemonic == "bzhi":
            dst = _reg_name(md, ops[0].reg)
            src = _mem_text(ins, mem_op) if mem_op else _reg_name(md, ops[1].reg)
            idx = _reg_name(md, ops[2].reg)
            site.update(dst=dst, src=src, idx=idx)
        elif ins.mnemonic == "pdep":
            dst = _reg_name(md, ops[0].reg)
            src = _reg_name(md, ops[1].reg)  # always a register (VEX.vvvv)
            mask = _mem_text(ins, mem_op) if mem_op else _reg_name(md, ops[2].reg)
            site.update(dst=dst, src=src, mask=mask)
        else:
            raise AssertionError(ins.mnemonic)

        sites.append(site)
    return sites


def scan_file(path):
    data = open(path, "rb").read()
    ncmds, sizeofcmds, header_size, segments = parse_macho_header(data)
    text_addr, text_offset, text_size = find_text_section(segments)
    text_bytes = data[text_offset:text_offset + text_size]
    sites = scan(data, text_addr, text_offset, text_bytes)
    return data, ncmds, sizeofcmds, header_size, segments, sites


if __name__ == "__main__":
    import sys
    data, ncmds, sizeofcmds, header_size, segments, sites = scan_file(sys.argv[1])
    print(f"{sys.argv[1]}: {len(sites)} BMI2 sites")
    riprel = [s for s in sites if s["riprel"]]
    print(f"  {len(riprel)} RIP-relative")
    for s in riprel:
        print(f"    {hex(s['address'])} {s['mnemonic']} {s['op_str']} -> abs 0x{s['riprel_abs_addr']:x}")
    by_mnem = {}
    for s in sites:
        by_mnem[s["mnemonic"]] = by_mnem.get(s["mnemonic"], 0) + 1
    print("  by mnemonic:", by_mnem)
