# 🐳 Docker Code Runner API

Это API-сервер на Flask, который принимает JSON с кодом на Python и тестами, запускает всё в изолированном Docker-контейнере и возвращает результат выполнения.

## 📦 Возможности

- Запуск пользовательского кода в контейнере с Python
- Автоматическая установка нужных библиотек
- Генерация функции `script(...)` с параметрами
- Генерация `unittest` тестов и их выполнение
- Безопасное изолированное выполнение

---

## 🚀 Как запустить

1. Клонируй репозиторий:

```bash
git clone git@github.com:MarkovIlya/safe-code-test-with-docker-api.git
```

2. Установи зависимости:

```bash
pip install -r requirements.txt
```

3. Запусти API:

```bash
python main.py или python3 main.py
```

## Пример запроса

```json
POST /run
Content-Type: application/json
{
  "language": "python",
  "code": "return a + b",
  "libraries": ["pandas", "flask"],
  "script_parameters": ["a", "b"],
  "tests": [
    {
      "parameters": [1, 2],
      "results": [3]
    },
    {
      "parameters": [3, 5],
      "results": [8]
    }
  ]
}
```

## Ответ API

```json
{
    "install_output": "Collecting pandas\n  Downloading pandas-2.2.3-cp39-cp39-manylinux_2_17_x86_64.manylinux2014_x86_64.whl (13.1 MB)\n     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 13.1/13.1 MB 11.0 MB/s eta 0:00:00\nCollecting flask\n  Downloading flask-3.1.0-py3-none-any.whl (102 kB)\n     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 103.0/103.0 kB 6.2 MB/s eta 0:00:00\nCollecting tzdata>=2022.7\n  Downloading tzdata-2025.2-py2.py3-none-any.whl (347 kB)\n     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 347.8/347.8 kB 9.3 MB/s eta 0:00:00\nCollecting pytz>=2020.1\n  Downloading pytz-2025.2-py2.py3-none-any.whl (509 kB)\n     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 509.2/509.2 kB 9.8 MB/s eta 0:00:00\nCollecting python-dateutil>=2.8.2\n  Downloading python_dateutil-2.9.0.post0-py2.py3-none-any.whl (229 kB)\n     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 229.9/229.9 kB 8.9 MB/s eta 0:00:00\nCollecting numpy>=1.22.4\n  Downloading numpy-2.0.2-cp39-cp39-manylinux_2_17_x86_64.manylinux2014_x86_64.whl (19.5 MB)\n     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 19.5/19.5 MB 10.3 MB/s eta 0:00:00\nCollecting Werkzeug>=3.1\n  Downloading werkzeug-3.1.3-py3-none-any.whl (224 kB)\n     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 224.5/224.5 kB 8.4 MB/s eta 0:00:00\nCollecting Jinja2>=3.1.2\n  Downloading jinja2-3.1.6-py3-none-any.whl (134 kB)\n     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 134.9/134.9 kB 6.7 MB/s eta 0:00:00\nCollecting importlib-metadata>=3.6\n  Downloading importlib_metadata-8.6.1-py3-none-any.whl (26 kB)\nCollecting itsdangerous>=2.2\n  Downloading itsdangerous-2.2.0-py3-none-any.whl (16 kB)\nCollecting click>=8.1.3\n  Downloading click-8.1.8-py3-none-any.whl (98 kB)\n     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 98.2/98.2 kB 7.0 MB/s eta 0:00:00\nCollecting blinker>=1.9\n  Downloading blinker-1.9.0-py3-none-any.whl (8.5 kB)\nCollecting zipp>=3.20\n  Downloading zipp-3.21.0-py3-none-any.whl (9.6 kB)\nCollecting MarkupSafe>=2.0\n  Downloading MarkupSafe-3.0.2-cp39-cp39-manylinux_2_17_x86_64.manylinux2014_x86_64.whl (20 kB)\nCollecting six>=1.5\n  Downloading six-1.17.0-py2.py3-none-any.whl (11 kB)\nInstalling collected packages: pytz, zipp, tzdata, six, numpy, MarkupSafe, itsdangerous, click, blinker, Werkzeug, python-dateutil, Jinja2, importlib-metadata, pandas, flask\nSuccessfully installed Jinja2-3.1.6 MarkupSafe-3.0.2 Werkzeug-3.1.3 blinker-1.9.0 click-8.1.8 flask-3.1.0 importlib-metadata-8.6.1 itsdangerous-2.2.0 numpy-2.0.2 pandas-2.2.3 python-dateutil-2.9.0.post0 pytz-2025.2 six-1.17.0 tzdata-2025.2 zipp-3.21.0\nWARNING: Running pip as the 'root' user can result in broken permissions and conflicting behaviour with the system package manager. It is recommended to use a virtual environment instead: https://pip.pypa.io/warnings/venv\n\n[notice] A new release of pip is available: 23.0.1 -> 25.0.1\n[notice] To update, run: pip install --upgrade pip",
    "status": "success",
    "test_output": "..\n----------------------------------------------------------------------\nRan 2 tests in 0.000s\n\nOK"
}
```

## Требования

- Python 3.8+
- Docker установлен и работает
- Права на запуск контейнеров (добавь себя в группу docker, если на Linux)

## Структура проекта

```bash
.
├── app.py              # Flask API
├── DockerCodeRunner.py # Основная логика запуска кода в Docker
├── requirements.txt
└── README.md
```

## Безопасность

Код исполняется в изолированном контейнере, и удаляется после выполнения. Однако всё равно не стоит запускать произвольные входные данные в продакшене без дополнительных проверок.

