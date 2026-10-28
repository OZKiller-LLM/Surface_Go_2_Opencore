# EFI/boot diagnostics collector

A double-clickable `.app` that gathers the logs and files normally needed
to debug an OpenCore/Hackintosh boot, graphics, or EFI issue on this
machine, into one timestamped zip on the Desktop -- instead of typing out
`log show`, `nvram -p`, `system_profiler`, etc. by hand each time something
goes wrong.

Same AppleScript/`osacompile` approach as `tools/gui-installer/` (see that
folder's README for why) -- no compiled code, no toolchain needed, a
native admin-password dialog, nothing sent anywhere automatically.

## What it collects

Into `~/Desktop/BMI2Diagnostics-<timestamp>/`, then zipped:

- `boot.log` -- the full `log show --last boot`.
- `graphics_wifi_bt.log` -- the same boot, filtered to lines mentioning
  `igfx`, `BTCoex`, `itlwm`, `BigSurface`, `AppleIntelKBL`, or
  `WindowServer` -- the subsystems this repo's troubleshooting sections
  (graphics acceleration, the Wi-Fi/BT coexistence boot loop, Type
  Cover/BigSurface) actually care about.
- `panics/` -- any `.ips`/`.panic`/`.crash` reports from the last 14 days,
  from both `/Library/Logs/DiagnosticReports` (system-level, e.g. kernel
  panics, WindowServer crashes) and the current console user's own
  `~/Library/Logs/DiagnosticReports`.
- `nvram.txt` -- the full NVRAM dump, plus `boot-args.txt` with just the
  current boot-args.
- `system_profile.txt` -- hardware, macOS version, displays, Bluetooth,
  Wi-Fi, NVMe, and storage info.
- `ioreg_full.txt` and `kextstat.txt` -- the full IORegistry dump and
  currently-loaded kext list (useful for confirming whether a given kext,
  or our patched graphics driver, actually loaded).
- `EFI_config.plist` and any `opencore-*.txt` log files, read directly off
  the live EFI partition (best-effort -- only present if OpenCore's
  `Misc > Debug > Target` has disk logging enabled; this step mounts the
  EFI partition temporarily and unmounts it again afterward if it mounted
  it itself).

Collection is best-effort: if one item fails (e.g. the EFI log isn't
there), everything else is still collected, and the completion dialog
says which items succeeded or failed.

## Build steps (do this on the Mac)

1. Copy `DiagnosticsCollector.applescript` (this folder) onto the Mac.
2. Build the app bundle:
   ```
   osacompile -o "DiagnosticsCollector.app" DiagnosticsCollector.applescript
   ```
   (Or: open it in Script Editor, then File > Export, File Format:
   Application.)

No resources need to be added afterward (unlike the GUI installer), so no
re-signing step is required here -- `osacompile`'s own signature stays
valid. Since it's still only ad-hoc signed, the first launch may get a
Gatekeeper "unidentified developer" warning -- right-click (Control-click)
the app and choose **Open** once.

## Running it

Double-click (or right-click > Open, the first time) `DiagnosticsCollector.app`.
It will ask for confirmation, prompt for your admin password, collect
everything above, zip it, and offer to reveal it in Finder. Share the
resulting `.zip` (or specific files from inside it) however you'd
normally share a file with me.
