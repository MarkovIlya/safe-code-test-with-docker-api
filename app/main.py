import logging
import os
from flask import Flask, request, jsonify
from docker_runner.DockerCodeRunner import DockerCodeRunner
from concurrent.futures import ThreadPoolExecutor

# ─── Настройка логирования ─────────────────────────────────────────────
if not os.path.exists("logs"):
    os.makedirs("logs")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/api.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

# ─── Flask App и Executor ─────────────────────────────────────────────────────────
app = Flask(__name__)
runner = DockerCodeRunner()
executor = ThreadPoolExecutor(max_workers=8)  # Пул для параллельных задач

@app.route("/run", methods=["POST"])
def run_code():
    try:
        data = request.get_json()
        logging.info("Получен запрос: %s", data)

        # Проверка обязательных ключей
        required_fields = ["language", "code", "libraries", "script_name", "script_parameters", "tests"]
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400

        # Проверка структуры tests
        for i, test in enumerate(data["tests"]):
            if not isinstance(test, dict) or "parameters" not in test or "results" not in test:
                return jsonify({"error": f"Invalid test format at index {i}"}), 400
            
        language = data.get("language")
        if language != "python":
            logging.warning("Неподдерживаемый язык: %s", language)
            return jsonify({"error": "Only Python is supported"}), 400

        logging.info("Запуск DockerCodeRunner с библиотеками: %s", data["libraries"])
        
        # Запуск в отдельном потоке
        future = executor.submit(
            runner.run,
            image_name="python:3.11",
            user_code=data["code"],
            libraries=data["libraries"],
            tests=data["tests"],
            script_name=data["script_name"],
            script_parameters=data["script_parameters"]
        )

        result = future.result()

        logging.info("Результат выполнения: %s", result["status"])

        return jsonify(result)

    except Exception as e:
        logging.exception("Ошибка при обработке запроса:")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    logging.info("Запуск Flask API...")
    app.run(debug=True)
