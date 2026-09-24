@echo off
chcp 65001 >nul
REM 打包为单文件 exe 的脚本（Windows）
REM 需要先安装 pyinstaller: pip install pyinstaller

SET SCRIPT=gui.py
SET NAME=booster_gui

echo 正在安装打包所需的 Python 依赖（来自 requirements.txt）...
pip install -r requirements.txt

echo 检测 fake_useragent 的 data 目录 (用于包含到可执行文件中)...
set FAKE_DATA=
for /f "delims=" %%p in ('python -c "import fake_useragent,os;print(os.path.join(os.path.dirname(fake_useragent.__file__),'data'))"') do set FAKE_DATA=%%p

if defined FAKE_DATA (
  echo 找到 fake_useragent data: %FAKE_DATA%
  if exist "fake_useragent\data" (
	echo 删除项目中的现有 fake_useragent\data ...
	rmdir /s /q "fake_useragent\data"
  )
  echo 复制 data 到项目目录 fake_useragent\data ...
  xcopy /E /I /Y "%FAKE_DATA%" "fake_useragent\data" >nul
  set ADD_ARG=--add-data "fake_useragent\\data;fake_useragent\\data"
) else (
  echo 未检测到 fake_useragent data，将不额外包含 data 目录。
  set ADD_ARG=
)

echo 正在使用 PyInstaller 打包 %SCRIPT% ...
pyinstaller --onefile --name %NAME% %ADD_ARG% --add-data "fetch_and_boost.py;." --add-data "booster.py;." %SCRIPT%

echo 打包完成。生成的可执行文件位于 dist\%NAME%.exe
echo 注意: 运行时需要相同的 Python 依赖（requests, urllib3, charset_normalizer, fake_useragent 等）。
pause
