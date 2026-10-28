# GUI installer for the BMI2-patched graphics drivers

A double-clickable `.app` that installs the patched
`AppleIntelKBLGraphicsGLDriver` / `AppleIntelKBLGraphicsMTLDriver` binaries
built by `tools/bmi2-patch/`, instead of doing it by hand in Terminal. It
asks for your admin password through the normal macOS authentication
dialog (via `do shell script ... with administrator privileges`) -- no
Terminal needed to run it.

**Status: built but not run on real hardware yet**, same as the patcher
itself -- I have no macOS machine to test this on. The shell commands it
runs are modeled on OpenCore Legacy Patcher's proven root-volume patching
approach for this same class of driver bundle (`sys_patch/utilities/files.py`'s
`install_new_file()` / `fix_permissions()`, and `sys_patch/mount/mount.py`'s
`RootVolumeMount` for mounting the real system volume read-write). Please
report back whether it works.

An earlier version of this script wrote directly to `/System/Library/...`
and failed with "Read-Only File System" -- fixed by mounting the real
volume read-write first (see "Why it mounts..." below) instead of writing
to the booted, sealed snapshot at `/`.

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
4. Mount the real system volume read-write at
   `/System/Volumes/Update/mnt1` (skipped if already mounted there).
5. Back up any existing files at the destination paths, under that mount,
   to `/Library/Application Support/BMI2GraphicsPatch/backup-<timestamp>/`.
6. Copy the two patched files into place there (these paths currently only
   contain `libigdmd.dylib` -- there is no existing main executable to
   overwrite, so this creates a new file; see the note in
   `tools/bmi2-patch/README.md` about why that's fine).
7. `chmod 755` + `chown root:wheel` both files (matching OCLP's
   `fix_permissions()`).
8. Ad-hoc code-sign both (`codesign -f -s -`).
9. Reseal the boot snapshot from that mount (`bless --folder
   ".../System/Library/CoreServices" --bootefi --create-snapshot`).
10. Unmount `/System/Volumes/Update/mnt1`.

Then remove `-igfxvesa` from boot-args and reboot.

### Why it mounts `/System/Volumes/Update/mnt1` instead of writing to `/`

On Big Sur and newer (Monterey included), the booted `/System` is a sealed,
read-only APFS snapshot. `csrutil disable` and `csrutil authenticated-root
disable` only lift the *signature* checks on that content -- neither makes
the booted snapshot itself writable. Writing straight to
`/System/Library/...` fails with "Read-Only File System" even with both of
those set (this is exactly what happened the first time this installer was
run). The fix -- what OCLP's root-volume patcher does, see
`sys_patch/mount/mount.py`'s `RootVolumeMount._mount_root_volume()` -- is to
mount the real underlying volume read-write at a separate mountpoint,
`/System/Volumes/Update/mnt1`, write the files there, then bless a new
snapshot from that mount (`--folder .../CoreServices --bootefi
--create-snapshot`, never `--mount /`). The installer now does this
automatically; you don't need to do anything differently.

## Restoring the originals

The backup directory printed at the end of a successful install contains
`AppleIntelKBLGraphicsGLDriver.orig` and
`AppleIntelKBLGraphicsMTLDriver.orig` (only created if a file existed at
that path before -- on this system, where no file existed on disk only
`libigdmd.dylib` did, there's nothing to restore to; just delete the
patched file to go back to shared-cache-only). To restore an overwritten
file, mount the real volume read-write the same way the installer does,
then write to it (not to `/`):
```
sudo mkdir -p /System/Volumes/Update/mnt1
ROOT_ID=$(diskutil info -plist / | /usr/libexec/PlistBuddy -c 'Print :DeviceIdentifier' /dev/stdin)
# if `diskutil info -plist /` also prints APFSSnapshot = true, strip the
# trailing 2 characters from $ROOT_ID first (e.g. disk1s1s1 -> disk1s1)
sudo mount -o nobrowse -t apfs "/dev/$ROOT_ID" /System/Volumes/Update/mnt1

DEST=/System/Volumes/Update/mnt1/System/Library/Extensions/AppleIntelKBLGraphicsGLDriver.bundle/Contents/MacOS/AppleIntelKBLGraphicsGLDriver
sudo cp "/Library/Application Support/BMI2GraphicsPatch/backup-<timestamp>/AppleIntelKBLGraphicsGLDriver.orig" "$DEST"
sudo chmod 755 "$DEST"
sudo chown root:wheel "$DEST"
sudo codesign -f -s - "$DEST"
sudo bless --folder /System/Volumes/Update/mnt1/System/Library/CoreServices --bootefi --create-snapshot
sudo umount /System/Volumes/Update/mnt1
```
(repeat the `DEST=...` / `cp` / `chmod` / `chown` / `codesign` lines for
the MTL driver before the `bless`/`umount` at the end), then reboot.
