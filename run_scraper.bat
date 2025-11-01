@echo off
echo ============================================
echo Citadel Securities 新聞爬蟲 - 所有系列
echo ============================================
echo.

call venv\Scripts\activate.bat
python scraper.py --series all

echo.
echo ============================================
echo 完成！按任意鍵退出...
echo ============================================
pause > nul

