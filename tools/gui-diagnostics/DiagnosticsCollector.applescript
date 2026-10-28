-- EFI/Boot Diagnostics Collector
--
-- Double-clickable tool that gathers the logs and files normally needed to
-- debug an OpenCore/Hackintosh boot or graphics issue on this machine --
-- boot log, a graphics/Wi-Fi/Bluetooth-filtered log, crash/panic reports,
-- NVRAM (including boot-args), system_profiler output, ioreg and kextstat
-- dumps, and (best-effort) the live EFI partition's config.plist and
-- OpenCore log -- into one timestamped, zipped folder on the Desktop, so
-- it can be shared back without typing a dozen commands by hand each time.
--
-- Collection is best-effort: one missing/failed item does not stop the
-- rest from being collected. The completion dialog reports what succeeded.

property folderPrefix : "BMI2Diagnostics"

on run
	set confirmMsg to "This collects diagnostic logs useful for debugging boot/graphics/EFI issues on this Mac:" & return & return & ¬
		"  - Last boot's log (full, and filtered for graphics/Wi-Fi/Bluetooth)" & return & ¬
		"  - Recent crash/panic reports" & return & ¬
		"  - NVRAM (including boot-args)" & return & ¬
		"  - system_profiler hardware/display/Wi-Fi/Bluetooth info" & return & ¬
		"  - ioreg and kextstat dumps" & return & ¬
		"  - The live EFI partition's config.plist and OpenCore log, if present" & return & return & ¬
		"Everything is written to a single timestamped, zipped folder on your Desktop. You'll be asked for your admin password (most of this needs it to read system-level logs). Nothing is sent anywhere -- you choose when/how to share the result." & return & return & ¬
		"Continue?"

	display dialog confirmMsg buttons {"Cancel", "Collect"} default button 2 with icon note
	if button returned of result is "Cancel" then return

	set ts to my timestampString()
	set folderName to folderPrefix & "-" & ts
	set desktopPosix to (POSIX path of (path to desktop folder))
	set outDir to desktopPosix & folderName

	set shellScript to my buildCollectScript(outDir, folderName, desktopPosix)

	try
		set collectOutput to do shell script shellScript with administrator privileges
		set zipPath to desktopPosix & folderName & ".zip"
		display dialog "Collection complete." & return & return & collectOutput & return & return & ¬
			"Saved to:" & return & zipPath buttons {"OK", "Reveal in Finder"} default button 2 with icon note
		if button returned of result is "Reveal in Finder" then
			do shell script "open -R " & quoted form of zipPath
		end if
	on error errMsg number errNum
		if errNum is -128 then
			return
		end if
		display dialog "Collection failed:" & return & return & errMsg buttons {"OK"} default button 1 with icon stop
	end try
end run

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

on buildCollectScript(outDir, folderName, desktopPosix)
	-- No "set -e": collection is best-effort, one failed item should not
	-- stop the rest. Every risky command is individually guarded.
	set scriptLines to {}
	set end of scriptLines to "OUTDIR=" & quoted form of outDir
	set end of scriptLines to "mkdir -p \"$OUTDIR/panics\""
	set end of scriptLines to "STATUS=\"\""

	-- Full boot log
	set end of scriptLines to "if log show --last boot --style syslog > \"$OUTDIR/boot.log\" 2>\"$OUTDIR/boot.log.stderr\"; then STATUS=\"$STATUS\\n  - boot.log: OK\"; else STATUS=\"$STATUS\\n  - boot.log: FAILED (see boot.log.stderr)\"; fi"

	-- Filtered graphics/Wi-Fi/Bluetooth log
	set end of scriptLines to "if log show --last boot --predicate 'eventMessage CONTAINS[c] \"igfx\" OR eventMessage CONTAINS[c] \"BTCoex\" OR eventMessage CONTAINS[c] \"itlwm\" OR eventMessage CONTAINS[c] \"BigSurface\" OR sender CONTAINS[c] \"AppleIntelKBL\" OR sender CONTAINS[c] \"WindowServer\"' --style syslog > \"$OUTDIR/graphics_wifi_bt.log\" 2>\"$OUTDIR/graphics_wifi_bt.log.stderr\"; then STATUS=\"$STATUS\\n  - graphics_wifi_bt.log: OK\"; else STATUS=\"$STATUS\\n  - graphics_wifi_bt.log: FAILED\"; fi"

	-- Recent crash/panic reports (system + current console user, last 14 days)
	set end of scriptLines to "CONSOLE_USER=$(stat -f%Su /dev/console 2>/dev/null)"
	set end of scriptLines to "PANIC_COUNT=0"
	set end of scriptLines to "for SRC in /Library/Logs/DiagnosticReports /Library/Logs/DiagnosticReports/Retired \"/Users/$CONSOLE_USER/Library/Logs/DiagnosticReports\"; do"
	set end of scriptLines to "  if [ -d \"$SRC\" ]; then"
	set end of scriptLines to "    find \"$SRC\" -maxdepth 1 -type f \\( -name '*.ips' -o -name '*.panic' -o -name '*.crash' \\) -mtime -14 -exec cp {} \"$OUTDIR/panics/\" \\; 2>/dev/null"
	set end of scriptLines to "  fi"
	set end of scriptLines to "done"
	set end of scriptLines to "PANIC_COUNT=$(find \"$OUTDIR/panics\" -type f 2>/dev/null | wc -l | tr -d ' ')"
	set end of scriptLines to "STATUS=\"$STATUS\\n  - panics/: $PANIC_COUNT file(s) from the last 14 days\""

	-- NVRAM (full dump + explicit boot-args)
	set end of scriptLines to "if nvram -p > \"$OUTDIR/nvram.txt\" 2>/dev/null; then STATUS=\"$STATUS\\n  - nvram.txt: OK\"; else STATUS=\"$STATUS\\n  - nvram.txt: FAILED\"; fi"
	set end of scriptLines to "nvram 'boot-args' > \"$OUTDIR/boot-args.txt\" 2>/dev/null"

	-- system_profiler
	set end of scriptLines to "if system_profiler SPHardwareDataType SPSoftwareDataType SPDisplaysDataType SPBluetoothDataType SPAirPortDataType SPNVMeDataType SPStorageDataType > \"$OUTDIR/system_profile.txt\" 2>\"$OUTDIR/system_profile.txt.stderr\"; then STATUS=\"$STATUS\\n  - system_profile.txt: OK\"; else STATUS=\"$STATUS\\n  - system_profile.txt: FAILED\"; fi"

	-- ioreg and kextstat
	set end of scriptLines to "if ioreg -l -w0 > \"$OUTDIR/ioreg_full.txt\" 2>/dev/null; then STATUS=\"$STATUS\\n  - ioreg_full.txt: OK\"; else STATUS=\"$STATUS\\n  - ioreg_full.txt: FAILED\"; fi"
	set end of scriptLines to "if kextstat > \"$OUTDIR/kextstat.txt\" 2>/dev/null; then STATUS=\"$STATUS\\n  - kextstat.txt: OK\"; else STATUS=\"$STATUS\\n  - kextstat.txt: FAILED\"; fi"

	-- Live EFI partition: config.plist + OpenCore log, best-effort
	set end of scriptLines to "diskutil info -plist / > /tmp/.bmi2diag_rootinfo.plist 2>/dev/null"
	set end of scriptLines to "BOOT_WHOLE=$(/usr/libexec/PlistBuddy -c 'Print :ParentWholeDisk' /tmp/.bmi2diag_rootinfo.plist 2>/dev/null)"
	set end of scriptLines to "rm -f /tmp/.bmi2diag_rootinfo.plist"
	set end of scriptLines to "EFI_MOUNTED_HERE=0"
	set end of scriptLines to "if [ -n \"$BOOT_WHOLE\" ]; then"
	set end of scriptLines to "  EFI_ID=\"${BOOT_WHOLE}s1\""
	set end of scriptLines to "  mkdir -p /tmp/.bmi2diag_efimount"
	set end of scriptLines to "  if ! mount | grep -q /tmp/.bmi2diag_efimount; then"
	set end of scriptLines to "    if diskutil mount -mountPoint /tmp/.bmi2diag_efimount \"$EFI_ID\" >/dev/null 2>&1; then EFI_MOUNTED_HERE=1; fi"
	set end of scriptLines to "  fi"
	set end of scriptLines to "  if [ -d /tmp/.bmi2diag_efimount/EFI/OC ]; then"
	set end of scriptLines to "    cp /tmp/.bmi2diag_efimount/EFI/OC/config.plist \"$OUTDIR/EFI_config.plist\" 2>/dev/null"
	set end of scriptLines to "    cp /tmp/.bmi2diag_efimount/EFI/OC/opencore-*.txt \"$OUTDIR/\" 2>/dev/null"
	set end of scriptLines to "    OC_LOG_COUNT=$(find \"$OUTDIR\" -maxdepth 1 -name 'opencore-*.txt' 2>/dev/null | wc -l | tr -d ' ')"
	set end of scriptLines to "    STATUS=\"$STATUS\\n  - EFI_config.plist + $OC_LOG_COUNT OpenCore log(s): OK\""
	set end of scriptLines to "  else"
	set end of scriptLines to "    STATUS=\"$STATUS\\n  - EFI partition: could not read EFI/OC (not mounted, wrong partition, or OpenCore debug logging to disk is off)\""
	set end of scriptLines to "  fi"
	set end of scriptLines to "  if [ \"$EFI_MOUNTED_HERE\" = \"1\" ]; then diskutil umount /tmp/.bmi2diag_efimount >/dev/null 2>&1; fi"
	set end of scriptLines to "fi"

	-- Zip it up
	set end of scriptLines to "cd " & quoted form of desktopPosix
	set end of scriptLines to "/usr/bin/zip -r -q " & quoted form of (folderName & ".zip") & " " & quoted form of folderName
	set end of scriptLines to "echo -e \"$STATUS\""

	set AppleScript's text item delimiters to linefeed
	set joined to scriptLines as text
	set AppleScript's text item delimiters to ""
	return joined
end buildCollectScript
