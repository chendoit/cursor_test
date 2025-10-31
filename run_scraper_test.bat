@echo off
echo ============================================
echo Citadel Securities 新聞爬蟲 - 測試模式
echo ============================================
echo.
echo 測試模式說明：
echo - 即使文章已抓取也會重新抓取
echo - 不會更新 MongoDB 記錄
echo.

call venv\Scripts\activate.bat
python scraper.py --test

echo.
echo ============================================
echo 完成！按任意鍵退出...
echo ============================================
pause > nul

