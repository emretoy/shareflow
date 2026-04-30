@echo off
echo ================================================
echo   ShareFlow Client - Windows Kurulum
echo ================================================
echo.

:: Python kontrolu
python --version >nul 2>&1
if errorlevel 1 (
    echo [HATA] Python bulunamadi! python.org'dan yukleyin.
    pause
    exit /b 1
)

:: Bagimliliklari kur
echo [1/2] Bagimliliklar kuruluyor...
pip install pynput pyperclip
echo.

echo [2/2] Kurulum tamamlandi!
echo.
echo Kullanim:
echo   python client.py 192.168.1.231
echo.
pause
