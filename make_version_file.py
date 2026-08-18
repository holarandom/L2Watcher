# make_version_file.py
"""
Генерирует файл метаданных для .exe (издатель, название, версия, копирайт).

Зачем это нужно. У собранного .exe все поля свойств файла были ПУСТЫЕ:
ни издателя, ни названия продукта, ни версии. Для machine-learning движка
Windows Defender это самостоятельный минус — нормальные программы так себя
не ведут, а безымянный упакованный .exe, который сам себя прописывает в
автозапуск и читает чужие окна, набирает подозрительность по всем статьям.
Именно так и появился вердикт Trojan:Win32/Wacatac.C!ml.

Заполненные метаданные не делают файл «доверенным», но убирают один из
факторов и, что важнее, показывают человеку в свойствах файла и в
Диспетчере задач, что это за программа и чья она.

Версия берётся ИЗ version.py — единый источник правды, дублировать нельзя.
Запускается автоматически из build.bat перед сборкой.
"""
import os
import sys

from version import APP_VERSION, APP_NAME

OUTPUT = "file_version_info.txt"

AUTHOR = "holarandom"
REPO = "https://github.com/holarandom/L2Watcher"


def _version_tuple(v: str):
    """'1.1.0' -> (1, 1, 0, 0). Windows требует ровно четыре числа."""
    parts = []
    for chunk in str(v).split("-")[0].split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            break
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def build_text() -> str:
    vt = _version_tuple(APP_VERSION)
    vs = ".".join(str(x) for x in vt)

    # Кодовая страница 040904B0 = английский (US) + Unicode. Стандартная
    # комбинация, которую понимают все инструменты Windows.
    return f"""# Сгенерировано make_version_file.py — руками не править.
# Версия берётся из version.py (APP_VERSION = {APP_VERSION}).
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={vt},
    prodvers={vt},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '040904B0',
          [
            StringStruct('CompanyName', '{AUTHOR}'),
            StringStruct('FileDescription', '{APP_NAME} - Lineage II window monitor'),
            StringStruct('FileVersion', '{vs}'),
            StringStruct('InternalName', 'L2Watcher'),
            StringStruct('LegalCopyright', 'Copyright (c) 2026 {AUTHOR}. MIT License.'),
            StringStruct('OriginalFilename', 'L2Watcher.exe'),
            StringStruct('ProductName', '{APP_NAME}'),
            StringStruct('ProductVersion', '{vs}'),
            StringStruct('Comments', 'Open source: {REPO}'),
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, OUTPUT)
    text = build_text()
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"{OUTPUT} -> версия {APP_VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
