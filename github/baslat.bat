@echo off
chcp 65001 > nul
title Çelik İmalat, Montaj, Boya ve Sevkiyat Takip Sistemi MES
color 0B
cls
echo ===============================================================================
echo        🏗️  ÇELİK İMALAT, MONTAJ, BOYA VE SEVKİYAT TAKİP SİSTEMİ (MES)
echo ===============================================================================
echo.
echo   Sunucu ve Veritabanı Başlatılıyor...
echo.
echo   -> Bilgisayardan Giriş : http://localhost:5000
echo   -> Telefon / Tablet    : http://192.168.1.X:5000  (Tarayıcıdan giriniz)
echo.
echo   (Evden / Dışarıdan erişim için menüdeki '11. Mobil / Ağ Rehberi'ne bakınız)
echo ===============================================================================
echo.

start "" http://localhost:5000
python app.py

pause
