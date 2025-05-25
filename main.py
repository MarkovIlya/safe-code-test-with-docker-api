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
        
        image_name = data.get("docker_image", "python:3.11")

        try:
            runner.client.images.get(image_name)
        except docker.errors.ImageNotFound:
            return jsonify({"error": f"Docker image '{image_name}' not found"}), 404

        # Запуск в отдельном потоке
        future = executor.submit(
            runner.run,
            image_name=image_name,
            user_code=data["code"],
            libraries=data["libraries"],
            tests=data["tests"],
            script_name=data["script_name"],
            script_parameters=data["script_parameters"],
            # cleanup=False # расскомментить, если хотите чтобы контейнеры не удалялись
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

        dockerfile_content = f"""
        FROM python:3.11
        RUN pip install {' '.join(libraries)}
        WORKDIR /mnt/app
        """

        temp_dir = tempfile.mkdtemp()
        dockerfile_path = os.path.join(temp_dir, "Dockerfile")

        with open(dockerfile_path, "w") as f:
            f.write(dockerfile_content)

        logging.info("Сборка Docker образа %s с библиотеками: %s", image_name, libraries)
        image, logs = runner.client.images.build(
            path=temp_dir, 
            tag=image_name,
            rm=True,          # удаляет промежуточные контейнеры
            forcerm=True      # удаляет даже при ошибках
        )

        log_output = "\n".join(line.get("stream", "").strip() for line in logs if "stream" in line)
        logging.info("Docker build logs:\n%s", log_output)

        shutil.rmtree(temp_dir)
        return jsonify({"status": "success", "image_name": image_name})

    except Exception as e:
        logging.exception("Ошибка при сборке Docker-образа:")
        return jsonify({"status": "fail", "error": str(e)}), 500

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
