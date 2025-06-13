import logging
import os
import tempfile
import shutil
import docker
from flask import Flask, request, jsonify
from docker_runner.DockerCodeRunner import DockerCodeRunner
from docker_runner.static_analyzer import analyze_code
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

        logging.info("Запуск статического анализа для кода участника...")
        
        # Статический анализ перед запуском
        issues = analyze_code(data["code"])
        if issues:
            logging.warning("Статический анализ не прошёл: %s", issues)
            return jsonify({
                "status": "error",
                "message": "Статический анализ не прошёл",
                "issues": issues
            }), 400

        logging.info("Запуск DockerCodeRunner с библиотеками: %s", data["libraries"])
        
        image_name = data.get("docker_image", "python:3.11") # Пуллить если нет при старте

        timeout_ms = data.get("timeout_ms", 2000)  # По умолчанию 2000 мс (2 сек)

        # Запуск в отдельном потоке
        future = executor.submit(
            runner.run,
            image_name=image_name,
            user_code=data["code"],
            libraries=data["libraries"],
            timeout_ms=timeout_ms,
            tests=data["tests"],
            script_name=data["script_name"],
            script_parameters=data["script_parameters"],
            # cleanup=False # раскомментить, если хотите чтобы контейнеры не удалялись
        )

        result = future.result()

        logging.info("Результат выполнения: %s", result["status"])

        return jsonify(result)

    except Exception as e:
        logging.exception("Ошибка при обработке запроса:")
        return jsonify({"error": str(e)}), 500

@app.route("/image/build", methods=["POST"])
def build_docker_image():
    try:
        data = request.get_json()
        image_name = data.get("image_name")
        libraries = data.get("libraries", [])

        if not image_name:
            return jsonify({"error": "image_name is required"}), 400

        pip_libraries = ' '.join(libraries)

        temp_dir = tempfile.mkdtemp()

        # Сохраняем Python-скрипт генерации allowed_modules.json
        generator_script_path = os.path.join(temp_dir, "generate_allowed_modules.py")
        with open(generator_script_path, "w", encoding="utf-8") as f:
            f.write(DockerCodeRunner().generate_allowed_modules_script(libraries))

        # Создаём Dockerfile
        dockerfile_content = f"""
FROM python:3.11

RUN pip install {pip_libraries}
COPY generate_allowed_modules.py /generate_allowed_modules.py
RUN python3 /generate_allowed_modules.py

RUN cat /allowed_modules.json

WORKDIR /mnt/app
"""

        dockerfile_path = os.path.join(temp_dir, "Dockerfile")
        with open(dockerfile_path, "w", encoding="utf-8") as f:
            f.write(dockerfile_content)

        # Сборка Docker-образа
        logging.info("Сборка Docker образа %s с библиотеками: %s", image_name, libraries)
        image, logs = runner.client.images.build(
            path=temp_dir,
            tag=image_name,
            rm=True,
            forcerm=True
        )

        log_output = "\n".join(line.get("stream", "").strip() for line in logs if "stream" in line)
        logging.info("Docker build logs:\n%s", log_output)

        shutil.rmtree(temp_dir)
        return jsonify({"status": "success", "image_name": image_name})

    except Exception as e:
        logging.exception("Ошибка при сборке Docker-образа:")
        return jsonify({"status": "fail", "error": str(e)}), 500


@app.route("/image/list", methods=["GET"])
def list_images():
    images = runner.client.images.list()
    return jsonify([img.tags[0] for img in images if img.tags])

@app.route("/image/remove", methods=["POST"])
def remove_docker_image():
    try:
        data = request.get_json()
        if not data or "image_name" not in data:
            return jsonify({"error": "Missing 'image_name' in request"}), 400

        image_name = data["image_name"]
        logging.info("Запрос на удаление Docker-образа: %s", image_name)

        client = runner.client

        # Остановка и удаление контейнеров, использующих образ
        containers = client.containers.list(all=True, filters={"ancestor": image_name})
        for container in containers:
            logging.info("Остановка и удаление контейнера: %s", container.id)
            container.stop()
            container.remove()

        # Удаление самого образа
        client.images.remove(image=image_name, force=True)
        logging.info("Образ успешно удалён: %s", image_name)

        # Удаление всех dangling-образов
        dangling_images = client.images.list(filters={"dangling": True})
        for img in dangling_images:
            try:
                client.images.remove(img.id, force=True)
                logging.info("Удалён dangling-образ: %s", img.id)
            except Exception as e:
                logging.warning("Не удалось удалить dangling-образ %s: %s", img.id, str(e))

        return jsonify({"status": "success", "message": f"Image '{image_name}' removed successfully"})

    except docker.errors.ImageNotFound:
        logging.warning("Образ не найден: %s", image_name)
        return jsonify({"error": f"Image '{image_name}' not found"}), 404

    except Exception as e:
        logging.exception("Ошибка при удалении Docker-образа:")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    logging.info("Запуск Flask API...")
    app.run(debug=True)
