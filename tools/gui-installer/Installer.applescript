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

property glDriverName : "AppleIntelKBLGraphicsGLDriver"
property mtlDriverName : "AppleIntelKBLGraphicsMTLDriver"

property glDestDir : "/System/Library/Extensions/AppleIntelKBLGraphicsGLDriver.bundle/Contents/MacOS"
property mtlDestDir : "/System/Library/Extensions/AppleIntelKBLGraphicsMTLDriver.bundle/Contents/MacOS"

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
		"The installer will back up any existing files, copy the patched ones in, fix ownership/permissions, ad-hoc code-sign them, and reseal the boot snapshot. You'll be asked for your admin password. A reboot is required afterward." & return & return & ¬
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
	set glDest to glDestDir & "/" & glDriverName
	set mtlDest to mtlDestDir & "/" & mtlDriverName

	set scriptLines to {}
	set end of scriptLines to "set -e"
	set end of scriptLines to "mkdir -p " & quoted form of backupDir
	set end of scriptLines to "if [ -f " & quoted form of glDest & " ]; then cp -p " & quoted form of glDest & " " & quoted form of (backupDir & "/" & glDriverName & ".orig") & "; fi"
	set end of scriptLines to "if [ -f " & quoted form of mtlDest & " ]; then cp -p " & quoted form of mtlDest & " " & quoted form of (backupDir & "/" & mtlDriverName & ".orig") & "; fi"
	set end of scriptLines to "cp " & quoted form of glSource & " " & quoted form of glDest
	set end of scriptLines to "cp " & quoted form of mtlSource & " " & quoted form of mtlDest
	set end of scriptLines to "chmod 755 " & quoted form of glDest & " " & quoted form of mtlDest
	set end of scriptLines to "chown root:wheel " & quoted form of glDest & " " & quoted form of mtlDest
	set end of scriptLines to "codesign -f -s - " & quoted form of glDest
	set end of scriptLines to "codesign -f -s - " & quoted form of mtlDest
	set end of scriptLines to "bless --mount / --create-snapshot"

	set AppleScript's text item delimiters to linefeed
	set joined to scriptLines as text
	set AppleScript's text item delimiters to ""
	return joined
end buildInstallScript
