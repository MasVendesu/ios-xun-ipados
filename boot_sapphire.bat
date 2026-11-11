@echo off
setlocal EnableDelayedExpansion

echo ======================================================================
echo           HakiOS (iOS on Redmi Note 13 4G sapphire) - Boot Loader
echo ======================================================================
echo.

set "FASTBOOT_BIN=C:\Users\hungtv\platform-tools\fastboot.exe"
if not exist "%FASTBOOT_BIN%" (
    where fastboot >nul 2>nul
    if %errorlevel% equ 0 (
        set "FASTBOOT_BIN=fastboot"
    ) else (
        echo [!] ERROR: fastboot.exe not found!
        echo Please ensure Android platform-tools is installed.
        pause
        exit /b 1
    )
)

set "BOOT_IMG=%~dp0out\enxerto-boot.img"
if not exist "%BOOT_IMG%" (
    echo [!] ERROR: Boot image not found at:
    echo     %BOOT_IMG%
    pause
    exit /b 1
)

echo [*] Fastboot binary : %FASTBOOT_BIN%
echo [*] Boot image      : %BOOT_IMG%
echo.
echo [*] Waiting for Redmi Note 13 4G in Fastboot mode...
echo     (Hold Power + Volume Down if not already in Fastboot mode)
echo.

:wait_device
for /f "tokens=1" %%d in ('"%FASTBOOT_BIN%" devices 2^>nul') do (
    set "DEV_SERIAL=%%d"
    goto device_found
)
timeout /t 2 /nobreak >nul
goto wait_device

:device_found
echo [✔] Device detected: %DEV_SERIAL%
echo.
echo [*] Booting HakiOS XNU 12377 (iOS 26 ARM64E)...
echo.
"%FASTBOOT_BIN%" boot "%BOOT_IMG%"
if %errorlevel% neq 0 (
    echo.
    echo [!] Boot failed with error code %errorlevel%.
    pause
    exit /b %errorlevel%
)

echo.
echo ======================================================================
echo [✔] Boot command sent successfully!
echo ======================================================================
echo.
echo What to observe on your phone screen:
echo   1. Enxerto bootloader:
echo      - INFLATE progress bar (decompressing kernel + sapphire.adt)
echo      - CPU handoff from Silver core CPU0 to Gold core CPU4 (0x100)
echo      - JUMP STARTING IOS
echo   2. XNU Darwin Kernel:
echo      - Screen clears to AMOLED black
echo      - Darwin console banner / early boot logs
echo.
echo If the screen displays any panic logs or hangs, please take a photo!
echo.
pause
