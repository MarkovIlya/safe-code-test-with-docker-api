pip install -r requirements.txt

Запустить main.py

Запуститься сервер Flask

Сделать запрос на открытый порт по типу:
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
