#!/bin/sh
# Build and ad-hoc sign the on-device tools for iOS 26 / HakiOS -> research/sweetnet/
# (sweetnetcfg, cscheck, hcitest, bluetool-stub, sweetreboot, sweetdur = UFS durability test, kernel 0221; set TOOLS="hcitest" to build a subset; sweetreboot = reboot-to-fastboot, kernel 0137)
# iOS 26: also research/sweetnet/ios26/{sweetuicache,sweetfp.dylib} (arm64e + pacstrip --sign)
set -eu
here=$(cd "$(dirname "$0")" && pwd)
out=$here/../../research/sweetnet
mkdir -p "$out"
TARGET_VER=${TARGET_VER:-26.0}
IOS26=${IOS26:-1}
IOS18=${IOS18:-1}
for t in ${TOOLS:-sweetnetcfg cscheck hcitest bluetool-stub sweetreboot sweetdur}; do
	ents=$here/entitlements.plist
	case $t in
	cscheck) frameworks="-framework CoreFoundation -framework Security" ;;
	hcitest|sweetreboot|sweetdur) frameworks="" ;;
	bluetool-stub) frameworks=""; ents=$here/bluetool.entitlements ;;
	*) frameworks="-framework CoreFoundation" ;;
	esac
	xcrun -sdk iphoneos clang -arch arm64e -mios-version-min=$TARGET_VER -O2 -Wall \
		$frameworks "$here/$t.c" -o "$out/$t"
	codesign -s - -f --entitlements "$ents" "$out/$t"
	ls -l "$out/$t"
done
# LaunchServices register tool for sideloaded apps (tools/sweet-install-ipa.sh)
xcrun -sdk iphoneos clang -arch arm64e -mios-version-min=$TARGET_VER -O2 -fobjc-arc \
	-framework Foundation "$here/sweetuicache.m" -o "$out/sweetuicache"
codesign -s - -f --entitlements "$here/uicache.entitlements" "$out/sweetuicache"

# iOS 26 / HakiOS (Renegade): arm64e builds that the kernel without pointer authentication runs after
# `renegade strip --sign` (pacstrip). Output: research/sweetnet/ios26/ and research/sweetnet/ios18/
if [ "$IOS26" = 1 ] || [ "$IOS18" = 1 ]; then
	out26=$out/ios26
	out18=$out/ios18
	pacstrip=$here/../renegade/pacstrip.py
	mkdir -p "$out26" "$out18"
	# sweetprefs: set/read a user default through cfprefsd (installed in /usr/local/bin by root_tweaks18.base())
	xcrun -sdk iphoneos clang -arch arm64e -mios-version-min=$TARGET_VER -O2 -Wall -framework CoreFoundation \
		"$here/sweetprefs.c" -o "$out26/sweetprefs"
	codesign -s - -f --entitlements "$here/entitlements.plist" "$out26/sweetprefs"
	python3 -I "$pacstrip" --sign "$out26/sweetprefs"
	cp -f "$out26/sweetprefs" "$out18/sweetprefs" 2>/dev/null || true
	# sweetdur: UFS/HFS durability test (kernel patch 0221)
	xcrun -sdk iphoneos clang -arch arm64e -mios-version-min=$TARGET_VER -O2 -Wall "$here/sweetdur.c" -o "$out26/sweetdur"
	codesign -s - -f --entitlements "$here/entitlements.plist" "$out26/sweetdur"
	python3 -I "$pacstrip" --sign "$out26/sweetdur"
	cp -f "$out26/sweetdur" "$out18/sweetdur" 2>/dev/null || true
	# sweetuicache: LaunchServices registration, run by the first-boot job (root_tweaks18.firstboot)
	xcrun -sdk iphoneos clang -arch arm64e -mios-version-min=$TARGET_VER -O2 -fobjc-arc \
		-framework Foundation "$here/sweetuicache.m" -o "$out26/sweetuicache"
	codesign -s - -f --entitlements "$here/uicache.entitlements" "$out26/sweetuicache"
	python3 -I "$pacstrip" --sign "$out26/sweetuicache"
	cp -f "$out26/sweetuicache" "$out18/sweetuicache" 2>/dev/null || true
	# sweetfp.dylib: NSURLFileProtectionKey shim, linked into PosterBoard (root_tweaks18.posterboard)
	xcrun -sdk iphoneos clang -arch arm64e -mios-version-min=$TARGET_VER -O2 -fobjc-arc -dynamiclib \
		-install_name /usr/local/lib/sweetfp.dylib -framework Foundation \
		"$here/sweetfp.m" -o "$out26/sweetfp.dylib"
	codesign -s - -f "$out26/sweetfp.dylib"
	python3 -I "$pacstrip" --sign "$out26/sweetfp.dylib"
	cp -f "$out26/sweetfp.dylib" "$out18/sweetfp.dylib" 2>/dev/null || true
	ls -l "$out26"
fi

# First-boot wallpaper/icon style and the mobile-domain runner (root_tweaks18.firstboot, iOS 26, arm64e + pacstrip)
if [ "$IOS26" = 1 ] || [ "$IOS18" = 1 ]; then
	out26=$out/ios26
	out18=$out/ios18
	pacstrip=$here/../renegade/pacstrip.py
	b26() { # name source entitlements [extra clang args]
		n=$1; src=$2; ents=$3; shift 3
		xcrun -sdk iphoneos clang -arch arm64e -mios-version-min=$TARGET_VER -O2 -fobjc-arc -framework Foundation "$@" \
			"$here/$src" -o "$out26/$n"
		codesign -s - -f --entitlements "$here/$ents" "$out26/$n"
		python3 -I "$pacstrip" --sign "$out26/$n"
		cp -f "$out26/$n" "$out18/$n" 2>/dev/null || true
	}
	# sweetmetrics: Bergamota live metrics (docs/BERGAMOTA_METRICS.md), plain C, root_tweaks18.sweetmetrics
	xcrun -sdk iphoneos clang -arch arm64e -mios-version-min=$TARGET_VER -O2 -Wall -framework CoreFoundation -framework IOKit \
		"$here/sweetmetrics.c" -o "$out26/sweetmetrics"
	codesign -s - -f --entitlements "$here/entitlements.plist" "$out26/sweetmetrics"
	python3 -I "$pacstrip" --sign "$out26/sweetmetrics"
	cp -f "$out26/sweetmetrics" "$out18/sweetmetrics" 2>/dev/null || true
	b26 sweetposterset sweetposterset.m posterset.entitlements
	b26 sweeticonstyle sweeticonstyle.m iconstyle.entitlements -framework UIKit
	b26 sweetopen sweetopen.m uicache.entitlements
	# remote testing over SSH: screenshot of the panel (PNG) and synthetic touch/buttons
	b26 sweetshot sweetshot.m touch.entitlements -framework CoreGraphics -framework ImageIO -framework IOSurface \
		-framework QuartzCore
	# a CPU profile of one process over SSH (running threads only, frame-pointer stacks, aggregated on the phone)
	xcrun -sdk iphoneos clang -arch arm64e -mios-version-min=$TARGET_VER -O2 -Wall "$here/sweetsample.c" -o "$out26/sweetsample"
	codesign -s - -f --entitlements "$here/sample.entitlements" "$out26/sweetsample"
	python3 -I "$pacstrip" --sign "$out26/sweetsample"
	cp -f "$out26/sweetsample" "$out18/sweetsample" 2>/dev/null || true
	# the unified log over SSH (no `log` tool on iOS)
	b26 sweetlog sweetlog.m log.entitlements -framework OSLog
	xcrun -sdk iphoneos clang -arch arm64e -mios-version-min=$TARGET_VER -O2 -Wall -framework CoreFoundation -framework IOKit \
		"$here/sweettouch.c" -o "$out26/sweettouch"
	codesign -s - -f --entitlements "$here/touch.entitlements" "$out26/sweettouch"
	python3 -I "$pacstrip" --sign "$out26/sweettouch"
	cp -f "$out26/sweettouch" "$out18/sweettouch" 2>/dev/null || true
	# SweetRun.app: runs /private/var/tmp/sweetrun.sh as mobile in the user's launchd domain (an app started by FrontBoard)
	app=$out26/SweetRun.app
	rm -rf "$app"; mkdir -p "$app"
	cp "$here/sweetrun-app/Info.plist" "$app/Info.plist"
	xcrun -sdk iphoneos clang -arch arm64e -mios-version-min=$TARGET_VER -O2 -fobjc-arc -framework UIKit -framework Foundation \
		"$here/sweetrun-app/sweetrun.m" -o "$app/SweetRun"
	codesign -s - -f --entitlements "$here/sweetrun-app/run.entitlements" "$app"
	python3 -I "$pacstrip" --sign "$app/SweetRun"
	codesign -s - -f --entitlements "$here/sweetrun-app/run.entitlements" "$app"      # seal the bundle again
	mkdir -p "$out18"
	cp -rf "$app" "$out18/SweetRun.app" 2>/dev/null || true
	ls -l "$out26"
fi

# sweetmetrics (Bergamota live metrics, docs/BERGAMOTA_METRICS.md): iOS 26, arm64e, then `renegade strip --sign`
# (pacstrip) so the kernel without pointer authentication runs it. Output: research/sweetnet/ios26/sweetmetrics
if [ "$IOS26" = 1 ] || [ "$IOS18" = 1 ]; then
	out26=$out/ios26
	mkdir -p "$out26"
	xcrun -sdk iphoneos clang -arch arm64e -mios-version-min=$TARGET_VER -O2 -Wall \
		-framework CoreFoundation -framework IOKit "$here/sweetmetrics.c" -o "$out26/sweetmetrics"
	codesign -s - -f --entitlements "$here/entitlements.plist" "$out26/sweetmetrics"
	"$here/../renegade/renegade" strip --sign "$out26/sweetmetrics"
	ls -l "$out26/sweetmetrics"
fi
