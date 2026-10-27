# Ready-to-post write-up

Paste this into a new issue on `dortania/OpenCore-Legacy-Patcher`, or into an
InsanelyMac / r/hackintosh thread. It states the problem and the evidence a
driver patcher needs. Edit the first line to suit the venue.

---

**Title:** UHD 615 (0x591C) no acceleration on a non-BMI2 CPU — Kaby Lake MTL driver SIGILLs on Monterey

**Summary**

I'm running macOS Monterey 12.7.6 on a Microsoft Surface Go 2 with an Intel
**Pentium Gold 4425Y** (Amber Lake-Y) and its **UHD 615** iGPU
(`8086:591C`). The GPU is natively supported and the kernel-side framebuffer
works perfectly, but graphics acceleration is impossible because Apple's
user-space Kaby Lake Metal/OpenGL drivers use **BMI2** instructions and this
CPU does not implement BMI2 (Intel fuses off AVX/BMI on Pentium/Celeron
parts). `WindowServer` dies with `SIGILL` the moment it loads the Metal
driver.

The identical iGPU on the Core m3-8100Y Surface Go 2 accelerates fine with the
same config — because that CPU has BMI2/AVX2. No real Mac ever paired a Kaby
Lake iGPU with a non-BMI2 CPU, so stock drivers don't handle it.

**CPU feature flags** (`sysctl machdep.cpu`)

```
machdep.cpu.features:        FPU ... SSE4.1 SSE4.2 x2APIC MOVBE POPCNT AES PCID XSAVE ... RDRAND   (no AVX)
machdep.cpu.leaf7_features:  RDWRFSGS ... SGX SMEP ERMS INVPCID ... RDSEED SMAP ... SSBD           (no BMI1, no BMI2, no AVX2)
```

**Crash** (`/Library/Logs/DiagnosticReports/WindowServer-*.ips`)

```
exception type : EXC_BAD_INSTRUCTION (SIGILL)
faulting image : AppleIntelKBLGraphicsMTLDriver  (short version 18.8.16, build 18.0.8)
faulting bytes : c4 e2 f3 f7 ce ...  ->  SHRX rcx, rsi, rcx   (BMI2), followed by more SHRX/SHLX
```

**Kernel side is healthy** (from `AppleIntelKBLGraphicsFramebuffer` logs)

- EDID read OK: panel BOE NV105WAM-N31, 1920×1280 eDP, 2 lanes, DPCD max link rate 0x0a.
- Modeset succeeds at 164.82 MHz pixel clock; timing registers match the panel.
- Failure is purely user-space: after the MTL driver fails, `WindowServer`
  gets no modes from `IOFBGetDisplayModeInformation()` and marks the display
  offline. With `-igfxvesa` (accel off) the desktop comes up normally.

**Scan of the driver** (used OCLP's `AppleIntelSKLGraphicsMTLDriver`, source
18.8.4, as a representative proxy for the KBL build)

- 682 VEX-encoded BMI instructions in `__TEXT,__text`:
  `shrx` 322, `shlx` 145, `bzhi` 120, `rorx` 49, `mulx` 36, `sarx` 7, `pdep` 3.
- 0 use AVX/YMM — all are scalar GPR BMI1/BMI2 ops.
- 629 register-only forms, 53 memory-operand (only 1 RIP-relative); all ≥5 bytes.
- `AppleIntelSKLGraphicsGLDriver` has ~240 more BMI sites.
- The **kernel** kext, the **framebuffer** kext, the VA/VME video drivers and
  `libigdmd.dylib` contain **0** BMI2 — matching the observed behaviour
  (kernel works, user-space rendering doesn't).

**What I'm asking**

Is a non-BMI2 build/patch of the Kaby Lake (and Skylake) user-space Metal +
OpenGL drivers feasible as a root patch — i.e. rewriting the BMI1/BMI2 sites to
equivalent pre-BMI2 code? OCLP's current Intel payloads still contain BMI2, so
this CPU class isn't covered today. Happy to test builds on the hardware and
provide any further logs.

**Environment**

- Surface Go 2, Pentium Gold 4425Y, UHD 615 (0x591C), 8 GB
- macOS Monterey 12.7.6 (21H1320)
- OpenCore 1.0.5, SMBIOS MacBookPro14,1, AMFI already relaxed (`amfi=0x80`)
