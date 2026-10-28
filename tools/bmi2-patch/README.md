# BMI2 instruction patcher (for the Pentium Gold 4425Y graphics driver crash)

## Why this exists

The Pentium Gold 4425Y's CPU lacks BMI1/BMI2/AVX/AVX2 (Intel disables the
VEX instruction encoding on Pentium/Celeron SKUs, even though the silicon
is otherwise Kaby Lake). Apple's Kaby Lake graphics drivers for macOS
Monterey (`AppleIntelKBLGraphicsMTLDriver`, `AppleIntelKBLGraphicsGLDriver`)
use BMI2 instructions in hot paths. On this CPU, WindowServer crashes with
`EXC_BAD_INSTRUCTION / SIGILL` the moment it loads them (confirmed via a
real `WindowServer-*.ips` crash report, decoded to a `SHRX` instruction
inside `AppleIntelKBLGraphicsMTLDriver`), which is why the Surface Go 2
(4425Y) shows a corrupted red-logo/green-bar boot screen: the kernel-side
framebuffer driver works fine, but the user-space Metal/OpenGL driver
never starts.

The fix: patch the driver binaries themselves. Find every BMI1/BMI2
instruction and replace it with a short detour to an equivalent sequence
built only from baseline (pre-BMI2) x86-64 instructions, which the 4425Y
does support.

## Status: patch built and verified against the real binaries -- not yet installed/booted

Both real driver binaries, extracted from the user's own Mac running
Monterey 12.7.6, have been fully patched and verified:

| File | BMI2 sites | Patched | Verified |
|---|---|---|---|
| `AppleIntelKBLGraphicsGLDriver` | 241 | 241 | 241/241, 0 failures |
| `AppleIntelKBLGraphicsMTLDriver` | 682 (incl. the exact site that crashed) | 682 | 682/682, 0 failures |
| `libigdmd.dylib` (both bundles) | 0 | n/a | n/a -- no BMI2 instructions found, not patched |

Verification means: for every single patched site, the ACTUAL trampoline
bytes written into the patched file (not a freshly-regenerated copy) were
executed in the Unicorn CPU emulator against realistic/randomized inputs
(spot-checked sites and every `pdep`/RIP-relative site got 40 trials each)
and their results compared bit-for-bit against the real, unmodified VEX
instruction. ~3,500 real executions total, 0 mismatches as of the last run.
`classify.py`/`scan_sites.py` also confirm zero BMI2 instructions remain in
either patched file's `__TEXT,__text` section afterward.

What's genuinely NOT done: installing the patched files on the Surface Go
2 and confirming it boots with acceleration. That step needs the real
hardware and is next.

## What's in this directory

- **`bmi_equiv.py`** -- for each BMI instruction form found in the drivers
  (`shrx`/`shlx`/`sarx`, `rorx`, `mulx`, `bzhi`, `pdep`), generates a legacy
  equivalent instruction sequence given the instruction's concrete operand
  registers (or a memory operand, for the ~90 real sites that use one).
  Handles register aliasing (destination aliasing a scratch, an implicit
  register, or -- for `mulx` -- the OTHER destination), flag preservation
  where the VEX form doesn't define flag outputs, and correct flag
  computation where it does (`bzhi`).
- **`verify_equiv.py`** -- proves each generated sequence is bit-identical
  to the real VEX instruction via Unicorn, across ~15,500 randomized
  trials covering every instruction form actually present in the two real
  binaries (including memory operands and the `mulx`-aliased-destination
  case). Run with `python3 verify_equiv.py`.
- **`scan_sites.py`** -- disassembles a Mach-O binary's `__TEXT,__text`
  with Capstone, finds every VEX-encoded BMI1/BMI2 site, and extracts full
  structured operand info (registers, memory operand text and its
  base/index/scale/disp, RIP-relative detection and resolved absolute
  target) ready to feed into `bmi_equiv.py`.
- **`patch_macho.py`** -- the actual binary patcher. For each site found
  by `scan_sites.py`: builds its trampoline's assembly text, assembles all
  trampolines sequentially into a new code blob, patches each site to a
  5-byte relative `jmp` into its trampoline (every real site is >=5 bytes,
  so this always fits in place without disturbing surrounding
  instructions), and appends the trampoline blob as a new `__BMI2FIX`
  segment at the end of the file -- its `LC_SEGMENT_64` command is written
  into the existing unused padding between the Mach-O header's load
  commands and the first section's file data (both real binaries have
  several KB of it), so no existing file content ever needs to shift.
  Usage: `python3 patch_macho.py <input> <output>`.
- **`verify_patched_file.py`** -- the strongest check in this project:
  takes an original file and its patched output, and for every site
  re-derives the trampoline's real address from the actual `E9`/rel32
  bytes written at that site, pulls out the actual trampoline bytes that
  shipped in the file, and runs them in Unicorn against the real
  unpatched instruction with matching inputs. Usage:
  `python3 verify_patched_file.py <original> <patched>`.

## Bugs found and fixed along the way (most interesting first)

- **`_riprel_wrapper`'s register exclusion missed `bzhi`'s `idx` entirely.**
  The one RIP-relative site in the whole project is a `bzhi` with
  `idx=rax`. The wrapper that loads a RIP-relative operand's resolved
  absolute address into a scratch register only excluded `dst`/`cnt`/`rcx`
  from that scratch's candidate pool -- `cnt` is shiftx's role-register,
  but bzhi's is `idx`, which nothing excluded. The scratch landed on `rax`
  too, so its own preamble silently overwrote the real `idx` value with
  the just-loaded `src` value before bzhi's generator ever read it,
  corrupting CF and the result whenever `idx` was meant to select "keep
  all bits". Caught only by `verify_patched_file.py` against the real
  site -- there's exactly one RIP-relative instruction in either binary,
  so no generic fuzz test ever exercised this path. Now excludes every
  named register-valued role the site has, not just the ones the
  original code's author happened to think of.
- **`mulx` with `dst_high == dst_low` aliased.** The compiler does emit
  `mulx r, r, src` in these binaries (confirmed: several real sites).
  Real hardware/Unicorn writes HIGH last when the two destinations alias
  (HIGH wins), but the generator wrote LOW last. `verify_equiv.py`'s
  original `mulx` test always sampled 3 *distinct* registers for
  dst_high/dst_low/src, so it never hit this case -- closed with a
  dedicated `test_mulx_aliased_dest` in addition to the generator fix.
- **Unicorn/QEMU engine bug: two mapped regions both near the top of the
  48-bit canonical address space corrupt execute permissions on the
  second one**, even though `mem_read`/`mem_write` on it work fine (a
  real trampoline's vmaddr and the real `__text` resume address it jumps
  back to are both such addresses, since both come from the original
  dyld shared cache's layout). Confirmed via a minimal repro independent
  of this project's own code. Worked around in `verify_patched_file.py`
  by redirecting a local copy's trailing jmp to a sentinel within the
  trampoline's own already-mapped page for execution purposes only --
  the real jmp's byte-correctness is checked separately, by decoding the
  site's own `E9`/rel32 bytes directly.
- **A Mach-O patching mistake that would have corrupted the whole file**:
  an early version inserted the new `LC_SEGMENT_64` command into the
  header's unused padding with a bytearray *insert*, which grows the
  array and silently shifts every byte after it -- including the jmp
  patches already written into `__text` and the entire rest of the file.
  Fixed to *overwrite* the padding in place (same length in, same length
  out). Caught immediately by `verify_patched_file.py` reading back
  garbage at every site's expected jmp location.
- **Unicorn/QEMU's own 32-bit `bzhi` TCG emulation is independently
  buggy**: for index values `n >= 32` it incorrectly clears bit 31 of the
  result, though the instruction is specified to leave the source
  unchanged in that case (CF is also wrong at some `n == OperandSize - 1`
  boundaries). Both `verify_equiv.py` and `verify_patched_file.py` check
  `bzhi` sites against an independent, hand-written Python reference
  model of the Intel SDM's definition instead of trusting Unicorn's own
  instruction as ground truth -- and separately count (without failing
  the suite) how often Unicorn's own output disagrees with the spec,
  which is routinely 40-45% of all bzhi cases. This is a bug in the
  emulator, not in anything shipped here.

## Installing on the Surface Go 2

A GUI installer that does steps 2-4 below for you is in
`tools/gui-installer/` -- see that folder's README to build and run it.
Manual steps:

1. Boot to Recovery, `csrutil disable` (full SIP off) and
   `csrutil authenticated-root disable` (needed since any modification
   to `/System` content breaks the sealed volume's snapshot hash tree).
2. Reboot to the desktop (with `-igfxvesa` in boot-args so it actually
   reaches one). The booted `/System` is a sealed, read-only APFS
   snapshot -- `csrutil` settings only lift the signature checks, not the
   read-only mount -- so mount the real volume read-write first:
   ```
   sudo mkdir -p /System/Volumes/Update/mnt1
   diskutil info -plist / > /tmp/rootinfo.plist
   ROOT_ID=$(/usr/libexec/PlistBuddy -c 'Print :DeviceIdentifier' /tmp/rootinfo.plist)
   IS_SNAP=$(/usr/libexec/PlistBuddy -c 'Print :APFSSnapshot' /tmp/rootinfo.plist 2>/dev/null || echo false)
   [ "$IS_SNAP" = "true" ] && ROOT_ID="${ROOT_ID%??}"
   sudo mount -o nobrowse -t apfs "/dev/$ROOT_ID" /System/Volumes/Update/mnt1
   ```
   (this is the same approach OpenCore Legacy Patcher's root-volume
   patcher uses, see `sys_patch/mount/mount.py`'s
   `RootVolumeMount._mount_root_volume()`). Then copy the two patched
   files to these paths **under that mount**, not under `/` (the live
   system has no existing main executable there -- only `libigdmd.dylib`
   is present, since Apple folds these into the dyld shared cache instead
   of shipping a loose on-disk copy -- so this creates a new file rather
   than overwriting one; the same thing OCLP does for this same class of
   driver bundle, see its `install_new_file()` / `fix_permissions()` in
   `sys_patch/utilities/files.py`):
   - `/System/Volumes/Update/mnt1/System/Library/Extensions/AppleIntelKBLGraphicsGLDriver.bundle/Contents/MacOS/AppleIntelKBLGraphicsGLDriver`
   - `/System/Volumes/Update/mnt1/System/Library/Extensions/AppleIntelKBLGraphicsMTLDriver.bundle/Contents/MacOS/AppleIntelKBLGraphicsMTLDriver`

   Then explicitly fix ownership and permissions on both (a bare `cp`
   doesn't guarantee these; OCLP always does this after copying):
   `chmod 755 <path>` and `chown root:wheel <path>`.
3. Ad-hoc re-sign both (neither original extracted file carried its own
   code signature -- the dyld shared cache is signed as one unit, not
   per-dylib -- so this adds a fresh one rather than replacing anything):
   `codesign -f -s - <path>` on each.
4. Rebuild the sealed snapshot from that mount, not from `/`:
   `sudo bless --folder /System/Volumes/Update/mnt1/System/Library/CoreServices --bootefi --create-snapshot`,
   then `sudo umount /System/Volumes/Update/mnt1`.
5. Remove `-igfxvesa` from boot-args and reboot.

(The GUI installer in `tools/gui-installer/` does all of step 2 onward for
you, including the mount/unmount.)
