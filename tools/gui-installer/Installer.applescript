-- BMI2 Graphics Driver Patch Installer
--
-- Double-clickable installer for the BMI2-patched AppleIntelKBLGraphicsGLDriver
-- and AppleIntelKBLGraphicsMTLDriver binaries (see tools/bmi2-patch/README.md
-- for why these exist and how they were built/verified).
--
-- This script does NOT contain the patched binaries. Build this into an .app
-- with osacompile (see tools/gui-installer/README.md), then drop the two
-- patched files into the built app's Contents/Resources/ folder before
-- running it. See that README for full build and prerequisite steps.
--
-- On Big Sur and newer, the booted /System is a sealed, read-only APFS
-- snapshot -- csrutil disable / authenticated-root disable only lift the
-- signature checks, not the read-only mount itself. So this script mounts
-- the real underlying volume read-write at /System/Volumes/Update/mnt1
-- (the same approach OpenCore Legacy Patcher's root-volume patcher uses),
-- writes the patched files there, and blesses a fresh snapshot from that
-- mount -- not from "/".

property glDriverName : "AppleIntelKBLGraphicsGLDriver"
property mtlDriverName : "AppleIntelKBLGraphicsMTLDriver"

property glRelPath : "/System/Library/Extensions/AppleIntelKBLGraphicsGLDriver.bundle/Contents/MacOS/AppleIntelKBLGraphicsGLDriver"
property mtlRelPath : "/System/Library/Extensions/AppleIntelKBLGraphicsMTLDriver.bundle/Contents/MacOS/AppleIntelKBLGraphicsMTLDriver"

on run
	set resourcesPosix to my resourcesPath()
	if resourcesPosix is missing value then
		display dialog "Could not locate this app's Resources folder." buttons {"OK"} default button 1 with icon stop
		return
	end if

	set glSource to resourcesPosix & glDriverName
	set mtlSource to resourcesPosix & mtlDriverName

	if not (my fileExists(glSource)) or not (my fileExists(mtlSource)) then
		display dialog "Patched driver files not found." & return & return & ¬
			"Expected to find both of these inside the app bundle:" & return & ¬
			glSource & return & mtlSource & return & return & ¬
			"Copy the two patched files into Installer.app/Contents/Resources/ (named exactly as above, no extension) and try again." buttons {"OK"} default button 1 with icon stop
		return
	end if

	set confirmMsg to "This installs BMI2-patched Kaby Lake graphics drivers to:" & return & return & ¬
		"  AppleIntelKBLGraphicsGLDriver.bundle" & return & ¬
		"  AppleIntelKBLGraphicsMTLDriver.bundle" & return & return & ¬
		"Prerequisites -- this tool does NOT do these for you:" & return & ¬
		"  1. Booted to Recovery and ran: csrutil disable" & return & ¬
		"  2. Booted to Recovery and ran: csrutil authenticated-root disable" & return & ¬
		"  3. Rebooted to the desktop (likely needs -igfxvesa in boot-args to get there)" & return & return & ¬
		"The installer will mount the real system volume read-write, back up any existing files, copy the patched ones in, fix ownership/permissions, ad-hoc code-sign them, and reseal the boot snapshot. You'll be asked for your admin password. A reboot is required afterward." & return & return & ¬
		"Continue?"

	display dialog confirmMsg buttons {"Cancel", "Install"} default button 2 with icon caution
	if button returned of result is "Cancel" then return

	set backupDir to "/Library/Application Support/BMI2GraphicsPatch/backup-" & (my timestampString())

	set shellScript to my buildInstallScript(glSource, mtlSource, backupDir)

	try
		set installOutput to do shell script shellScript with administrator privileges
		display dialog "Install complete." & return & return & ¬
			"A backup of any original files (if present) was saved to:" & return & backupDir & return & return & ¬
			"Remove -igfxvesa from boot-args and reboot to test." buttons {"OK"} default button 1 with icon note
	on error errMsg number errNum
		if errNum is -128 then
			-- user cancelled the admin password prompt
			return
		end if
		display dialog "Install failed:" & return & return & errMsg buttons {"OK"} default button 1 with icon stop
	end try
end run

on resourcesPath()
	try
		set appPath to path to me as text
		return (POSIX path of appPath) & "Contents/Resources/"
	on error
		return missing value
	end try
end resourcesPath

on fileExists(posixPath)
	try
		posixPath as POSIX file as alias
		return true
	on error
		return false
	end try
end fileExists

on timestampString()
	set nowDate to current date
	set y to year of nowDate as integer
	set m to (month of nowDate as integer)
	set d to (day of nowDate as integer)
	set t to time of nowDate
	set hh to t div 3600
	set mm to (t mod 3600) div 60
	set ss to t mod 60
	return (y as text) & "-" & (my twoDigit(m)) & "-" & (my twoDigit(d)) & "-" & (my twoDigit(hh)) & (my twoDigit(mm)) & (my twoDigit(ss))
end timestampString

on twoDigit(n)
	if n < 10 then
		return "0" & (n as text)
	else
		return (n as text)
	end if
end twoDigit

on buildInstallScript(glSource, mtlSource, backupDir)
	-- Everything below runs as root, via "do shell script ... with administrator
	-- privileges". MOUNT is the real, writable root volume -- resolved and
	-- mounted at /System/Volumes/Update/mnt1 if not already mounted there,
	-- mirroring OpenCore Legacy Patcher's RootVolumeMount._mount_root_volume()
	-- for Big Sur and newer. Destination paths are $MOUNT + the bundle path,
	-- never a bare "/System/...", since the booted "/" is a read-only sealed
	-- snapshot regardless of csrutil settings.

	set scriptLines to {}
	set end of scriptLines to "set -e"
	set end of scriptLines to "MOUNT=/System/Volumes/Update/mnt1"
	set end of scriptLines to "if [ ! -f \"$MOUNT/System/Library/CoreServices/SystemVersion.plist\" ]; then"
	set end of scriptLines to "  mkdir -p \"$MOUNT\""
	set end of scriptLines to "  /usr/sbin/diskutil info -plist / > /tmp/.bmi2_rootinfo.plist"
	set end of scriptLines to "  ROOT_ID=$(/usr/libexec/PlistBuddy -c 'Print :DeviceIdentifier' /tmp/.bmi2_rootinfo.plist)"
	set end of scriptLines to "  IS_SNAP=$(/usr/libexec/PlistBuddy -c 'Print :APFSSnapshot' /tmp/.bmi2_rootinfo.plist 2>/dev/null || echo false)"
	set end of scriptLines to "  rm -f /tmp/.bmi2_rootinfo.plist"
	set end of scriptLines to "  if [ \"$IS_SNAP\" = \"true\" ]; then ROOT_ID=\"${ROOT_ID%??}\"; fi"
	set end of scriptLines to "  /sbin/mount -o nobrowse -t apfs \"/dev/$ROOT_ID\" \"$MOUNT\""
	set end of scriptLines to "fi"
	set end of scriptLines to "GLDEST=\"$MOUNT" & glRelPath & "\""
	set end of scriptLines to "MTLDEST=\"$MOUNT" & mtlRelPath & "\""
	set end of scriptLines to "mkdir -p " & quoted form of backupDir
	set end of scriptLines to "if [ -f \"$GLDEST\" ]; then cp -p \"$GLDEST\" " & quoted form of (backupDir & "/" & glDriverName & ".orig") & "; fi"
	set end of scriptLines to "if [ -f \"$MTLDEST\" ]; then cp -p \"$MTLDEST\" " & quoted form of (backupDir & "/" & mtlDriverName & ".orig") & "; fi"
	set end of scriptLines to "cp " & quoted form of glSource & " \"$GLDEST\""
	set end of scriptLines to "cp " & quoted form of mtlSource & " \"$MTLDEST\""
	set end of scriptLines to "chmod 755 \"$GLDEST\" \"$MTLDEST\""
	set end of scriptLines to "chown root:wheel \"$GLDEST\" \"$MTLDEST\""
	set end of scriptLines to "codesign -f -s - \"$GLDEST\""
	set end of scriptLines to "codesign -f -s - \"$MTLDEST\""
	set end of scriptLines to "/usr/sbin/bless --folder \"$MOUNT/System/Library/CoreServices\" --bootefi --create-snapshot"
	set end of scriptLines to "/sbin/umount \"$MOUNT\" || true"
	set end of scriptLines to "echo 'Done.'"

	set AppleScript's text item delimiters to linefeed
	set joined to scriptLines as text
	set AppleScript's text item delimiters to ""
	return joined
end buildInstallScript
