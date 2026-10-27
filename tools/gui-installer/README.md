# GUI installer for the BMI2-patched graphics drivers

A double-clickable `.app` that installs the patched
`AppleIntelKBLGraphicsGLDriver` / `AppleIntelKBLGraphicsMTLDriver` binaries
built by `tools/bmi2-patch/`, instead of doing it by hand in Terminal. It
asks for your admin password through the normal macOS authentication
dialog (via `do shell script ... with administrator privileges`) -- no
Terminal needed to run it.

**Status: built but not run on real hardware yet**, same as the patcher
itself -- I have no macOS machine to test this on. The shell commands it
runs are the exact ones from `tools/bmi2-patch/README.md`'s manual install
steps, which are themselves modeled on OpenCore Legacy Patcher's proven
`install_new_file()` / `fix_permissions()` pattern for this same class of
driver bundle (see that README's "Installing" section). Please report back
whether it works.

## Why AppleScript instead of a compiled app

`osacompile` (part of macOS) and `do shell script ... with administrator
privileges` give a real native admin-password dialog and installer-style
`display dialog` prompts with zero compiled code and nothing that needs a
toolchain to build or sign -- appropriate for a one-off installer like this.
I can't compile or test a Swift/AppKit app from this environment, so this
keeps the implementation to something verifiable by reading it.

## Build steps (do this on the Mac)

1. Copy `Installer.applescript` (this folder) onto the Mac.
2. Build the app bundle:
   ```
   osacompile -o "BMI2GraphicsPatchInstaller.app" Installer.applescript
   ```
   (Or: open it in Script Editor, then File > Export, File Format:
   Application.)
3. Put the two patched driver files into the app's Resources folder, named
   **exactly** as below with no file extension:
   ```
   mkdir -p "BMI2GraphicsPatchInstaller.app/Contents/Resources"
   cp /path/to/binary1.patched   "BMI2GraphicsPatchInstaller.app/Contents/Resources/AppleIntelKBLGraphicsGLDriver"
   cp /path/to/mtl_driver.patched "BMI2GraphicsPatchInstaller.app/Contents/Resources/AppleIntelKBLGraphicsMTLDriver"
   ```
   (Right-click the app > Show Package Contents works too, if you'd rather
   drag them in via Finder.)

## Prerequisites (the app does NOT do these for you)

1. Boot to Recovery, run `csrutil disable` (full SIP off).
2. Still in Recovery, run `csrutil authenticated-root disable` (required --
   any modification under `/System` breaks the sealed volume's snapshot
   hash tree otherwise).
3. Reboot to the desktop. On this hardware that currently needs
   `-igfxvesa` in boot-args to get a usable screen, since the unpatched
   drivers are what's crashing WindowServer.

## Running it

Double-click `BMI2GraphicsPatchInstaller.app`. It will:
1. Check both patched files are present in its Resources folder.
2. Show what it's about to do and ask for confirmation.
3. Prompt for your admin password (standard macOS dialog).
4. Back up any existing files at the destination paths to
   `/Library/Application Support/BMI2GraphicsPatch/backup-<timestamp>/`.
5. Copy the two patched files into place (these paths currently only
   contain `libigdmd.dylib` -- there is no existing main executable to
   overwrite, so this creates a new file; see the note in
   `tools/bmi2-patch/README.md` about why that's fine).
6. `chmod 755` + `chown root:wheel` both files (matching OCLP's
   `fix_permissions()`).
7. Ad-hoc code-sign both (`codesign -f -s -`).
8. Reseal the boot snapshot (`bless --mount / --create-snapshot`).

Then remove `-igfxvesa` from boot-args and reboot.

If `bless --mount / --create-snapshot` fails on your macOS version, the
OCLP-equivalent fallback is mounting the actual sealed system volume
explicitly, typically `/System/Volumes/Update/mnt1`, and blessing that
instead -- if the simple form above errors, say so and I'll adjust the
script.

## Restoring the originals

The backup directory printed at the end of a successful install contains
`AppleIntelKBLGraphicsGLDriver.orig` and
`AppleIntelKBLGraphicsMTLDriver.orig` (only created if a file existed at
that path before -- on this system, where no file existed on disk only
`libigdmd.dylib` did, there's nothing to restore to; just delete the
patched file to go back to shared-cache-only). To restore an overwritten
file:
```
sudo cp "/Library/Application Support/BMI2GraphicsPatch/backup-<timestamp>/AppleIntelKBLGraphicsGLDriver.orig" \
  /System/Library/Extensions/AppleIntelKBLGraphicsGLDriver.bundle/Contents/MacOS/AppleIntelKBLGraphicsGLDriver
sudo chmod 755 /System/Library/Extensions/AppleIntelKBLGraphicsGLDriver.bundle/Contents/MacOS/AppleIntelKBLGraphicsGLDriver
sudo chown root:wheel /System/Library/Extensions/AppleIntelKBLGraphicsGLDriver.bundle/Contents/MacOS/AppleIntelKBLGraphicsGLDriver
sudo codesign -f -s - /System/Library/Extensions/AppleIntelKBLGraphicsGLDriver.bundle/Contents/MacOS/AppleIntelKBLGraphicsGLDriver
sudo bless --mount / --create-snapshot
```
(repeat for the MTL driver), then reboot.
