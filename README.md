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
| AAPL,ig-platform-id | Data | `00001B59` (= 0x591B0000) |
| framebuffer-patch-enable | Data | `01000000` |
| framebuffer-stolenmem | Data | `00003001` |
| framebuffer-fbmem | Data | `00009000` |

Previously the value was `591B0000`, which is byte-reversed and reads as the invalid platform-id 0x00001B59. With that value the framebuffer never attaches, and macOS falls back to software rendering.

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
