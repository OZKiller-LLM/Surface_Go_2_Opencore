# Graphics Acceleration Handover — Surface Go 2 (Pentium Gold 4425Y)

Diagnostic handover for whoever takes the graphics-acceleration work forward
(e.g. an OpenCore Legacy Patcher maintainer, or an InsanelyMac / r/hackintosh
driver patcher). The hard diagnostic work is done and recorded here; the
remaining work and who is equipped to do it are stated at the end.

---

## 1. Hardware / software under investigation

| Item | Value |
| --- | --- |
| Device | Microsoft Surface Go 2, 8 GB |
| CPU | Intel Pentium Gold 4425Y (Amber Lake-Y, 2C/4T, fixed 1.7 GHz, no turbo) |
| iGPU | Intel UHD Graphics 615, PCI device-id `8086:591C` |
| Target OS | macOS Monterey 12.7.6 (build 21H1320) — **fixed requirement** |
| Bootloader | OpenCore 1.0.5 |
| SMBIOS | MacBookPro14,1 (spoofed) |
| Sibling model | Core m3-8100Y Surface Go 2 — same iGPU, accelerates natively (see jlempen's EFI) |

## 2. Symptom

With the iGPU framebuffer set up for acceleration, boot reaches a corrupted
display (red/pink Apple logo, green progress bar) and then the screen stops
updating. The system continues booting behind the frozen image.
With `-igfxvesa` (acceleration off, software rendering) the desktop appears
normally.

## 3. Root cause — CONFIRMED

Apple's **user-space** Kaby Lake graphics drivers (Metal and OpenGL) are
compiled with **BMI2** instructions. The Pentium Gold 4425Y does **not**
implement BMI2 (Intel fuses off the VEX/AVX/BMI instruction set on Pentium and
Celeron parts). When `WindowServer` loads the Metal driver, the first BMI2
instruction raises `#UD`, which macOS delivers as `EXC_BAD_INSTRUCTION` /
`SIGILL`, and `WindowServer` dies before it can bring the display online.

### Evidence

1. **CPU feature flags** (`sysctl machdep.cpu`):
   - `machdep.cpu.features`: has `POPCNT`, `MOVBE`, `RDRAND`, `AES`; **no AVX**.
   - `machdep.cpu.leaf7_features`: `SMEP RDSEED SMAP ... SSBD`; **no BMI1, no BMI2, no AVX2**.

2. **WindowServer crash report** (`/Library/Logs/DiagnosticReports/WindowServer-*.ips`):
   - `exception type EXC_BAD_INSTRUCTION (SIGILL)`
   - faulting image `AppleIntelKBLGraphicsMTLDriver` (CFBundleVersion 18.0.8, short version 18.8.16)
   - faulting instruction bytes begin `c4 e2 f3 f7 ce ...` → decodes to
     `SHRX rcx, rsi, rcx` (a BMI2 instruction), followed by further
     `SHRX`/`SHLX`.

3. **Kernel side proven healthy** (from `AppleIntelKBLGraphicsFramebuffer`
   logs on the failing boot):
   - EDID read OK — panel is **BOE NV105WAM-N31**, 1920×1280, eDP, 2 lanes,
     DPCD max link rate `0x0a` (2.7 Gbps). EDID injection is NOT needed.
   - Modeset succeeds: `1920 x 1280 ... at 164820000 Hz`, timing registers
     match the panel exactly.
   - Failure is purely user-space: `WindowServer` marks the display offline
     (`IOFBGetDisplayModeInformation()` returns no modes) after the MTL driver
     fails to load.

4. **Driver scan (proxy)**: the on-disk KBL drivers for 12.7.6 live inside the
   dyld shared cache and were not scanned directly. As a representative proxy,
   OCLP's `AppleIntelSKLGraphicsMTLDriver` (source version 18.8.4, built from
   the same Apple driver source tree) was disassembled:
   - **682 VEX-encoded BMI instructions** in `__TEXT,__text`:
     `shrx` 322, `shlx` 145, `bzhi` 120, `rorx` 49, `mulx` 36, `sarx` 7, `pdep` 3.
   - **0 use AVX/YMM** — every one is a scalar GPR BMI1/BMI2 operation.
   - Operand forms: 629 register-only, 53 memory-operand (only **1**
     RIP-relative). All instructions are ≥5 bytes.
   - Companion binaries: `AppleIntelSKLGraphicsGLDriver` ~240 BMI sites; the
     **kernel** kext (`AppleIntelSKLGraphics`), the **framebuffer** kext, the
     VA/VME video drivers, and `libigdmd.dylib` contain **0** BMI2 — consistent
     with the kernel side working and only user-space rendering failing.

### Why the sibling m3-8100Y works and this does not
The Core m3-8100Y has BMI2/AVX2, so the same drivers and the same OpenCore
config accelerate the identical 0x591C iGPU. No real Mac ever shipped a Kaby
Lake iGPU paired with a CPU lacking BMI2, so this combination is untested by
Apple and unhandled by stock drivers.

## 4. Ruled OUT (do not re-try these — they cannot fix a missing-ISA fault)

- `AAPL,ig-platform-id` variants (`0x591B0000`, `0x59160000`, `0x87C00000`,
  `0x591C0005`), `framebuffer-stolenmem`/`fbmem`/DVMT changes, `device-id`
  spoof — all only affect framebuffer selection, which already works.
- WhateverGreen eDP fixes (`enable-dpcd-max-link-rate-fix`, `force-online`,
  `enable-max-pixel-clock-override`) — link/EDID/modeset are already healthy.
- EDID injection (`AAPL00,override-no-connect`) — EDID reads fine.
- Removing the CPUID spoof — **required** on this CPU; without it Monterey
  hangs during boot. Confirmed: still hangs with `-igfxvesa` and with CPUFriend
  disabled, so the spoof is a boot requirement independent of graphics.
- Stock OCLP: its shipped Intel SKL/KBL MTL driver (scanned above) also
  contains BMI2, so OCLP as-is does not address the no-BMI2 case for the Intel
  iGPU user-space drivers.

## 5. Config state (branch `claude/surface-go2-graphics-accel-dql4b5`)

Already applied and pushed (these are correct and should be kept):
- `DeviceProperties` → `PciRoot(0x0)/Pci(0x2,0x0)` → `AAPL,ig-platform-id` =
  `00001B59` (little-endian for platform-id `0x591B0000`; the original repo
  value `591B0000`/`WRsAAA==` was byte-reversed and invalid).
- `Kernel > Emulate`: Ivy Bridge CPUID spoof **kept** (`Cpuid1Data`
  `A0060300…`, `Cpuid1Mask` `FFFFFFFF…`, `DummyPowerManagement` = true) —
  required to boot.
- `Misc > Security > SecureBootModel` = `Disabled` (was `Default` with blank
  `SystemUUID`, which halted boot with "Grabbed zero system-id for SB").
- `boot-args` already contains `amfi=0x80` (AMFI relaxed), which is relevant to
  any approach that modifies or substitutes signed system binaries.

Known secondary issue (independent of graphics): **BigSurface fails to load**
because its `BatteryManager` class name collides with `SMCBatteryManager`
(`kext ... BatteryManager already has a class by that name`). This disables
Surface-specific input/battery features. Pick one battery kext, or use a
BigSurface build that doesn't register that class.

## 6. Remaining work and routing

The only way to get the 0x591C accelerated **on Monterey specifically** is to
make the Kaby Lake user-space Metal and OpenGL drivers run without BMI2
instructions (either by substituting a driver build that doesn't use them, or
by rewriting the ~680 + ~240 BMI1/BMI2 sites to equivalent pre-BMI2 code) and
installing the result via a root-patch flow (SIP lowered,
`csrutil authenticated-root disable`, install into
`/System/Library/Extensions`, rebuild snapshot; AMFI is already relaxed in
boot-args). Note this must be re-applied after every macOS update.

This is an established technique but a non-trivial binary-engineering task.
The parties equipped to carry it out:

- **OpenCore Legacy Patcher** (`dortania/OpenCore-Legacy-Patcher`): maintainers
  of the root-patch framework and the Intel graphics payloads. File an issue
  with the Section 3 evidence — a Kaby Lake iGPU on a non-BMI2 CPU is a case
  outside their current payload set and is best handled by them.
- **InsanelyMac / r/hackintosh**: individuals there have patched driver
  binaries for Pentium/Celeron hackintoshes and can do the instruction-level
  rewrite.

Hand them Section 1 and Section 3 verbatim — that is the information they would
otherwise spend days reproducing.

## 7. If acceleration proves impractical on Monterey

- Native (no-patch) acceleration may exist on an **older macOS** whose KBL
  driver predates BMI2 codegen (candidates newest-first: High Sierra 10.13.6,
  Mojave 10.14.6, Sierra 10.12.6). A single-file scan of that version's
  `AppleIntelKBLGraphicsMTLDriver` for BMI2 would confirm before committing to
  an install. This is outside the Monterey requirement but is the only
  zero-maintenance accelerated-macOS path.
- Linux and Windows accelerate this iGPU fully; their drivers do not require
  BMI2.
- Software rendering (`-igfxvesa`) is the stable Monterey fallback.
