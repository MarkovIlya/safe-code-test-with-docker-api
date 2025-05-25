# 🐳 Docker Code Runner API

Это API-сервер на Flask, который принимает JSON с кодом на Python и тестами, запускает всё в изолированном Docker-контейнере и возвращает результат выполнения.

## 📦 Возможности

- Запуск пользовательского кода в контейнере с Python
- Автоматическая установка нужных библиотек
- Генерация функции `script(...)` с параметрами
- Генерация `unittest` тестов и их выполнение
- Безопасное изолированное выполнение
- Создание и удаление собственных образов
- Запуск пользовательского кода под собственным образом с предустановленными библиотеками

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

3. Запусти API в корне проекта:

```bash
gunicorn main:app -c gunicorn.conf.py
```

## Пример запроса без собственного образа (по-умолчанию используется python:3.11)

```json
POST /run
Content-Type: application/json
{
  "language": "python",
  "code": "def test(a, b):\n\treturn a + b",
  "libraries": [],
  "script_name": "test",
  "script_parameters": ["a", "b"],
  "tests": [
    {
      "id": 1,
      "name": "First",
      "parameters": [1, 2],
      "results": [4]
    },
    {
      "id": 2,
      "name": "Second",
      "parameters": [3, 5],
      "results": [8]
    }
  ]
}
```

## Ответ API

```json
{
    "install_output": "No libraries to install",
    "status": "success",
    "test_output": "[{\"id\": \"2\", \"name\": \"test_case_2\", \"status\": \"success\"}, {\"id\": \"1\", \"name\": \"test_case_1\", \"status\": \"fail\", \"error\": \"Traceback (most recent call last):\\n  File \\\"/mnt/app/test_script.py\\\", line 9, in test_case_1\\n    self.assertEqual(result, 4)\\nAssertionError: 3 != 4\\n\"}]",
    "test_statuses": [
        {
            "id": "2",
            "name": "test_case_2",
            "status": "success"
        },
        {
            "error": "Traceback (most recent call last):\n  File \"/mnt/app/test_script.py\", line 9, in test_case_1\n    self.assertEqual(result, 4)\nAssertionError: 3 != 4\n",
            "id": "1",
            "name": "test_case_1",
            "status": "fail"
        }
    ]
}
```

## Пример запроса создания собственного образа

```json
POST /image/build
Content-Type: application/json
{
  "image_name": "olympiad-image-ml",
  "libraries": ["numpy", "pandas", "scikit-learn"]
}
```

## Ответ API

```json
{
    "image_name": "olympiad-image-ml",
    "status": "success"
}
```

## Пример запроса с собственным образом

```json
POST /run
Content-Type: application/json
{
  "language": "python",
  "docker_image": "olympiad-image-ml",
  "libraries": [],
  "script_name": "test",
  "script_parameters": ["matrix"],
  "code": "import numpy as np\n\ndef test(matrix):\n    \"\"\"\n    Проверяет, является ли матрица симметричной по главной диагонали.\n    \"\"\"\n    try:\n        arr = np.array(matrix)\n        # Проверка на квадратную матрицу\n        if arr.shape[0] != arr.shape[1]:\n            return False\n\n        # Проверка симметрии: A == A.T\n        symmetric = np.array_equal(arr, arr.T)\n        return symmetric\n    except Exception as e:\n        # В случае ошибки (например, если некорректный формат) возвращаем False\n        return False",
  "tests": [
    {
      "id": 1,
      "parameters": [[[1, 2], [2, 1]]],
      "results": [true]
    },
    {
      "id": 2,
      "parameters": [[[1, 0], [2, 1]]],
      "results": [false]
    }
  ]
}
```

## Ответ API

```json
{
    "install_output": "No libraries to install",
    "status": "success",
    "test_output": "[{\"id\": \"1\", \"name\": \"test_case_1\", \"status\": \"success\"}, {\"id\": \"2\", \"name\": \"test_case_2\", \"status\": \"success\"}]",
    "test_statuses": [
        {
            "id": "1",
            "name": "test_case_1",
            "status": "success"
        },
        {
            "id": "2",
            "name": "test_case_2",
            "status": "success"
        }
    ]
}
```

## Пример запроса удаления собственного образа

```json
POST /image/remove
Content-Type: application/json
{
    "image_name": "olympiad-image-ml"
}
```

## Ответ API

```json
{
    "message": "Image 'olympiad-image-ml' removed successfully",
    "status": "success"
}
```

## Требования

- Python 3.8+
- Docker установлен и работает
- Права на запуск контейнеров (добавь себя в группу docker, если на Linux)
- Чтобы использовать AppArmor нужно разворачивать проект на полноценном дистрибутиве Linux (либо виртуальная машина, либо на хосте) P.S. WSL2 не подойдет
- Если хотите использовать на Windows, то удалите строчку security_opt=["apparmor=docker_run_tests_profile"] в DockerCodeRunner.py

## Структура проекта

```bash
.
├── app armor profile
    └── docker_run_tests_profile # Профиль AppArmor
├── docker_runner
  ├── static_analyzer.py       # Статический анализатор кода участника 
  └── DockerCodeRunner.py      # Основная логика запуска кода в Docker
├── .gitignore 
├── gunicorn.conf.py             # Конфиг gunicorn
├── main.py                  # Flask API
├── README.md
└── requirements.txt


```

## Безопасность

Код исполняется в изолированном контейнере, и удаляется после выполнения (по-умолчанию). Однако всё равно не стоит запускать произвольные входные данные в продакшене без дополнительных проверок.

## Активация профиля AppArmor (для Linux)

Чтобы активировать профиль, который лежит в папке /app armor profile, нужно:
1. Добавить файл docker_run_tests_profile в папку на хосте /etc/apparmor.d/
2. Активировать профиль:

```bash
sudo apparmor_parser -r /etc/apparmor.d/docker_run_tests_profile
```

3. Проверить, что профиль активировался:

```bash
sudo aa-status | grep docker_run_tests_profile
```
Должно вывести название профиля.
