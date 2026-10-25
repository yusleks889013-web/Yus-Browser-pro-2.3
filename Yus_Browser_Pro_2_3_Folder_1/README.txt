ВНИМАНИЕ: это пока тестовая версия. Возможны зависания и задержки, а некоторые функции временами могут работать некорректно или быть недоступны. Браузер всё ещё находится в разработке.

Yus Browser Pro 2.3 — clean rebuild
===================================

FIRST SETUP
1. Extract this whole folder to a permanent location.
2. Run INSTALL_DEPENDENCIES.bat once. This installs PySide6 and Qt WebEngine.
3. Run SETUP_NO_CONSOLE_SHORTCUT.vbs to create a desktop shortcut.
4. Launch with the desktop shortcut. It uses pythonw and does not show CMD.

OPTIONAL: BUILD A STANDALONE EXE
Run BUILD_STANDALONE_APP.bat. It builds a windowed app in dist\Yus Browser Pro 2.3.
After the build, the desktop shortcut targets the EXE directly. The EXE does not need a separate Python installation, but the dist folder must remain intact.

DIAGNOSTICS
Run START_YUS_BROWSER.bat to see errors in a console window if the hidden launch fails.

Features retained from the project: tabs, search/address bar, bookmarks/history/downloads,
download progress notification, screenshot editor, purple interface, sound settings, and live
internet speedometer. Network-dependent features require internet access.


Создатель и разработчик: Юсуф, 14 лет.
Проект создан Юсуфом параллельно с ChatGPT с использованием Python.
