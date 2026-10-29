![OpenCore logo](https://github.com/acidanthera/OpenCorePkg/raw/master/Docs/Logos/OpenCore_with_text_Small.png)

# Surface-Go-2-OpenCore
After a long long period of trial and error, I finally able to boot into macOS on this Surface Go 2. 
The **Pentium Gold Y4425 Microsoft Surface Go 2 (8gb / 64gb eMMC model)**  thanks to [Acidanthera's OpenCore bootloader](https://github.com/acidanthera/OpenCorePkg) and [atisDu's OpenCore files](https://github.com/atisDu/Surface_go_opencore) The OpenCore version of this repository is 1.0.5! So consider updating it and the kexts from Acidanthera's  repos.
The latest macOS Version running is Monterey 12.7.4 

For Core m3-8100Y Microsoft Surface Go 2 please visit [jlempen's OpenCore files](https://github.com/jlempen/Surface-Go-2-OpenCore).

# Surface-Go-2-OpenCore-Linux-DualBoot
For the same model, if you want to dual boot linux, please use this efi files instead https://github.com/jalower/Surface-Go-2-Hackintosh-Linux-Dualboot

# Important!!!!!
Please add your own serial number and UUID by using Gen SMBIOS in this guide https://dortania.github.io/OpenCore-Install-Guide/config.plist/#creating-your-config-plist

# Issues pending to fix
Camera not working

It is able to turn on Bluetooth now, able to conncet to Airpods Pro 2, but the connection is unstable.

Able to pair with my iPhone, but unable to connet due to the error of " (Device) is unsupported"

Screen mirroring

Brightness control

Trackpad gesture

# Graphics acceleration (Intel UHD 615, device-id 0x591C)
The Pentium Gold 4425Y uses the same UHD 615 iGPU (`8086:591C`) as the Core m3-8100Y model, and macOS supports it natively through WhateverGreen. No device-id spoof or custom driver is needed.

Acceleration depends on `DeviceProperties > Add > PciRoot(0x0)/Pci(0x2,0x0) > AAPL,ig-platform-id` being written in little-endian byte order:

| Key | Type | Value |
| --- | --- | --- |
| AAPL,ig-platform-id | Data | `00001659` (= 0x59160000) |

Previously the value was `591B0000`, which is byte-reversed and reads as the invalid platform-id 0x00001B59. With that value the framebuffer never attaches, and macOS falls back to software rendering.

**Update:** after fixing the byte order, boot still didn't reach the desktop
(see `tools/bmi2-patch/`'s graphics-acceleration work and the "Wi-Fi/Bluetooth
coexistence boot loop" section below for that saga). Diagnostics collected
with `tools/gui-diagnostics/` once the Wi-Fi/BT loop was out of the way
showed the *kernel's own* `AppleIntelKBLGraphicsFramebuffer` still failing:
```
(AppleIntelKBLGraphicsFramebuffer) PlatformID is undefined
```
and `ioreg` confirmed the device itself ended up with
`"AAPL,ig-platform-id" = <ffffffff>` -- every other injected framebuffer
property (`framebuffer-stolenmem`, `framebuffer-fbmem`,
`framebuffer-patch-enable`) landed on the device correctly at the same
`PciRoot(0x0)/Pci(0x2,0x0)` path, so DeviceProperties injection itself
isn't the problem. `0x591B0000` is a real, documented Kaby Lake "laptop
default" platform-id (see WhateverGreen's `FAQ.IntelHD.en.md`), but it
appears to be keyed to hardware that actually reports device-id `0x591B` --
this machine's real `device-id` (confirmed via `ioreg`) is `0x591C`, and the
pairing didn't match anything in Apple's framebuffer kext's own table,
hence "undefined".

**Fix (not yet confirmed on hardware):** switched to `0x59160000` -- the
platform-id Apple's own framebuffer kext falls back to by default when none
is specified at all (per WhateverGreen's manual: "without AAPL,ig-platform-id
the following ID is assumed: 59160000"), and one of the lowest-stolen-memory
KBL entries (35 MB total), closest to what was already configured, so no
DVMT firmware change should be needed. Dropped the `framebuffer-stolenmem`/
`framebuffer-fbmem`/`framebuffer-patch-enable` overrides entirely, since
those were tuned for `0x591B0000`'s needs (which wants a separate 21 MB
`FBMEM` carve-out) and `0x59160000`'s table entry expects `FBMEM: 0 bytes`
-- mixing the old overrides with the new platform-id risked a kernel panic
per WhateverGreen's own warning about mismatched stolen-memory sizing.

If `0x59160000` still doesn't attach a real display, the next candidates
(in order of how closely their declared stolen-memory needs match what's
currently configured, i.e. lowest risk first) are `0x87C00000` (35 MB,
`framebuffer-fbmem` 0 bytes) and `0x591C0005` (58 MB total -- this one
specifically corresponds to device-id `0x591C`, matching this hardware
exactly, but needs DVMT pre-allocation raised to 64 MB in UEFI firmware
setup first, or it risks a panic rather than just a blank screen).

**Confirmed working** -- `0x59160000` fixed the kernel-level framebuffer:
the boot logo is no longer discolored (previously red/green-corrupted),
confirming a real display now attaches correctly. Boot still doesn't
reach the desktop, but for an unrelated reason -- see "CPU power-management
ACPI hang" below.

## CPU power-management ACPI hang
With the Type Cover detached (see "Wi-Fi/Bluetooth coexistence boot loop"
below -- a *different*, already-resolved hang) and the platform-id fixed,
boot progresses much further, through USB enumeration, kext collection
loading, and WhateverGreen/Lilu's igfx patches, then stops dead partway
through a dynamically-loaded ACPI table:
```
ACPI: Dynamic OEM Table Load:
ACPI: SSDT @0xFFFFFF9518CCF698 0003FF (v02 PmRef  CpuOCst  00003001 INTL 20160527)
ACPI: Executed 1 blocks of module-level executable AML code
ACPI: Dynami[cut off here]
```
Adding `-v keepsyms=1 debug=0x100` to boot-args (to force any real crash to
surface a full panic/backtrace instead of silently resetting) did **not**
produce a panic -- the system just stays frozen at this exact text,
confirming a genuine deadlock in the kernel's ACPI AML interpreter while
executing a CPU power-management table (`PmRef CpuOCst`), not a crash.

This is unrelated to graphics. `CPUFriend.kext`/`CPUFriendDataProvider.kext`
(which inject a custom CPU power-management table to make XCPM behave for
this non-genuine CPU) are the most directly related kexts to what's
visibly hanging, so both are **temporarily disabled** as the next
diagnostic step. Note: disabling CPUFriend alone did *not* fix an earlier,
different pre-boot hang (see "CPUID spoof is required" below) -- but that
was before the CPUID spoof existed and happened much earlier in boot, so
it doesn't rule CPUFriend out for this new, later hang. Not yet confirmed
either way.

**Update:** with CPUFriend disabled, boot got past that *specific*
`PmRef CpuOCst` table -- but then hit two more hangs in sequence, each
clearing only when something about the attached USB devices changed:

1. With the Type Cover attached: a `DK: AppleUserUSBHostHIDDevice-...
   waiting for server com.apple.driverkit.AppleUserHIDDrivers-...` hang
   (see "Wi-Fi/Bluetooth coexistence boot loop" below for the unrelated,
   earlier hang that section is actually about -- this is a *different*
   stall, for a USB HID DriverKit server, not Wi-Fi/BT). Detaching the
   Type Cover got past it.
2. With *all* external USB devices (keyboard, mouse, flash drive)
   unplugged, that same `DK: ... waiting for server` stall didn't recur at
   all -- pointing at `USBInjectAll.kext`'s generic ("inject every
   possible port") USB port map as the likely cause: DriverKit's HID
   device matching seems to stall on at least some of those generically
   injected ports, regardless of which physical device is attached to
   them. A proper port map specific to this board (e.g. built with
   USBToolBox) would be the real fix; not yet done.
3. But with no USB devices attached, boot then hit *another* dynamic
   `ACPI: Dynamic OEM Table Load:` hang -- this time with no table name
   even printed before it froze, so a different instance than
   `PmRef CpuOCst`. **CPUFriend was already disabled** at this point,
   confirming CPUFriend wasn't the true root cause of the ACPI hang
   category -- disabling it (and separately, removing the Type Cover)
   most likely just changed device-enumeration timing enough to dodge
   that one specific earlier occurrence. This looks timing-sensitive
   rather than cleanly attributable to one kext.

None of this repo's own SSDTs (`SSDT-EC-USBX-LAPTOP`, `SSDT-PNLF`,
`SSDT-SURFACE`, `SSDT-XOSI`) are CPU-power-related by name, so the
dynamically-loaded table is coming from the Surface Go 2's own firmware
ACPI tables, not anything injected here -- likely triggered by the
kernel's legacy ACPI power-management path (active because
`DummyPowerManagement = true` already routes around XCPM). Added
`acpi_layer=0xffffffff acpi_level=0xffffffff` to boot-args to get full
ACPI method-level tracing, since without it we don't even get a table
name to go on. Not yet run with this boot-arg.

## Secure Boot
`Misc > Security > SecureBootModel` is set to `Disabled`. With `Default` and `ApECID` = `0`, OpenCore builds the Secure Boot identity from `SystemUUID`. When that is blank or zero, boot stops with `OCB: Grabbed zero system-id for SB, this is not allowed`.

## CPUID spoof is required (Kernel > Emulate)
The Ivy Bridge CPUID spoof (`Cpuid1Data` `A0060300...`, `Cpuid1Mask` `FFFFFFFF...`, `DummyPowerManagement` = `true`) must stay enabled on the Pentium Gold 4425Y. Without it, Monterey 12.7.6 hangs during boot, even with `-igfxvesa` or with CPUFriend disabled. The Core m3-8100Y model does not need the spoof.

## If the boot screen is still corrupted (red Apple logo, green progress bar)
Try these one at a time and note the result of each:
1. Add `-v` to `boot-args`, and photograph the last lines on screen if it hangs.
2. If it reaches the desktop, run `system_profiler SPDisplaysDataType` and `log show --last boot --predicate 'eventMessage CONTAINS[c] "igfx" OR sender CONTAINS "AppleIntelKBL"'`.
3. Increase DVMT pre-allocation to 64 MB (see jlempen's guide), then delete `framebuffer-patch-enable`, `framebuffer-stolenmem` and `framebuffer-fbmem`. The 9 MB `fbmem` value is smaller than a single 1920x1280 32-bit frame (9.375 MB).
4. Try alternative platform-ids: `00001659` (0x59160000), `0000C087` (0x87C00000), or `05001C59` (0x591C0005, needs 64 MB DVMT).

## Wi-Fi/Bluetooth coexistence boot loop (AirportItlwm / IntelBluetoothFirmware)
After installing the BMI2-patched graphics drivers (see `tools/bmi2-patch/`)
and removing `-igfxvesa` to test, a verbose (`-v`) boot never reached the
desktop -- instead it sat scrolling the same block of text forever:
```
Setting BTCoex Profile: band:0
Profile[0]: mode:7; desense:0; desense_level:0; chain_power_offset:0,0,0,0,
...
Setting BTCoex Config: enable_2G:1, profile_2g:0, enable_5G:1, profile_5G:0
AppleKeyStore:7456:139: unexpected session: 100000 uid: -1 requested by: 139
AppleKeyStore:11114:139: operation failed (sel: 7 ret: e00002c2, -1, 100000)
Setting BTCoex Profile: band:0
...
```
This is `AirportItlwm` programming Bluetooth/Wi-Fi coexistence profiles for
the Intel AX200 combo card (see `setBTCOEX_PROFILES`/`setBTCOEX_CONFIG` in
`OpenIntelWireless/itlwm`'s `AirportItlwm/AirportSTAIOCTL.cpp`), repeating
over and over -- meaning that subsystem is crash-looping and
re-initializing instead of settling down. The `AppleKeyStore` lines are
normal pre-login keybag noise, not the cause.

Critically, this happens **before** the kernel hands the display off to
WindowServer -- while still showing scrolling verbose text, not a
graphical boot screen -- so this is blocking boot earlier than, and
independently of, the graphics driver patch. We had no way to tell from
this whether the BMI2 patch itself was working.

**Diagnostic step taken:** `AirportItlwm.kext`, `IntelBluetoothFirmware.kext`,
and `BlueToolFixup.kext` are temporarily disabled (`Kernel > Add`) to get
past this and confirm whether the graphics patch gets the system to the
desktop at all, with Wi-Fi/Bluetooth as a known, separate, temporarily
sacrificed feature. **Not yet confirmed working** -- if the system reaches
the desktop with these disabled, re-enable them one at a time (Wi-Fi
first, then Bluetooth) to isolate which one is actually looping, and
report back. If it still doesn't reach the desktop even with all three
disabled, the loop wasn't the (sole) blocker and something else is.

## Type Cover (keyboard/trackpad) and battery -- BigSurface failed to load
A verbose boot log showed `BigSurface` failing to load entirely:
```
OSMetaClass: Kext com.xavier.BigSurface class BatteryManager is a duplicate; kext ru.usrsse2.SMCBatteryManager already has a class by that name.
Kext com.xavier.BigSurface failed to load (0xdc000817).
Dependency com.xavier.BigSurface of kext com.xavier.BigSurfaceHIDDriver failed to load.
```
`BigSurfaceHIDDriver` (the Type Cover's actual keyboard/trackpad HID driver) depends on `BigSurface`, so this one conflict silently broke both -- not just the battery.

`BigSurface` ships its own purpose-built battery driver for Surface hardware (`IOKitPersonalities`: "Surface Battery", "Surface Battery SMBus Controller", talking to the real Surface battery/SMBus controller via ACPI), with an internal helper class that happens to also be named `BatteryManager` -- the exact same internal class name the generic VirtualSMC plugin `SMCBatteryManager.kext` registers. Only one kext can own a given class name in the kernel; `SMCBatteryManager` loads first and wins, so `BigSurface` (and everything that depends on it) never loads at all.

**Tried:** `SMCBatteryManager.kext` was disabled (`Kernel > Add`) so `BigSurface`'s own Surface-specific battery driver and `BigSurfaceHIDDriver` could load instead. **Reverted** -- on real hardware this removed the menu bar battery icon entirely and did not bring up the Type Cover (keyboard/trackpad). `SMCBatteryManager.kext` is back to enabled.

The Type Cover on this particular unit did not work correctly in Windows either, so the leading theory is a hardware fault in the Cover itself (or its connector) rather than this kext conflict -- the `BatteryManager` class collision documented above is real and still worth knowing about if you have a known-good Cover, but it is likely not the root cause of this unit's issue.


# What is fixed and confirmed to be working
Siri is working

Sleep is working

Battery is fixed and is able to show in menu bar

The icons and graphics for booting screen is enabled and working.

Audio is working

iServices are working for me (e.g. iCloud, iMessages....)

The wifi card (Intel AX200) works in macOS

DRM seems to be working

# Issues from atisDU that are unsure
macOS only works with eMMC storage if you use the [EmeraldSDHC kext](https://github.com/acidanthera/EmeraldSDHC) which as of writing this (11.07.2024 and the EmeraldSDHC kext version 0.1.2) does not seem to work with the SD host controller found in
the Surface Go (4gb / eMMC). Even legacy ATA controller kexts dont work. Which means that internal storage won't work.

# What is confirmed to be working from atisDU
The realtek SD card reader works

The touch (keyboard) cover and trackpad works with the [BigSurface kext](https://github.com/Xiashangning/BigSurface), comprising of modified Voodoo input kexts.

# How to make it boot
Install the image through macrecovery according to the [Dortania OpenCore install guide](https://dortania.github.io/OpenCore-Install-Guide/) 
The laptop is in the platform of kaby lake cpu's or simply use the provided config.plist, but change the PlatformInfo SMBIOS accordingly, for which a guide can be found in 
the opencore install guide.

Put the EFI folder in the root of the usb drive and download the mac os Monterey recovery folder by following the OpenCore install guide.


## Disclaimer
This repository is neither a howto nor an installation manual. Using these files requires at least basic knowledge of [Acidanthera's OpenCore bootloader](https://github.com/acidanthera/OpenCorePkg), ACPI, UEFI and the art of hackintoshing in general. I recommend reading the excellent [Dortania's OpenCore Install Guide](https://dortania.github.io/OpenCore-Install-Guide), as well as all its linked resources.
