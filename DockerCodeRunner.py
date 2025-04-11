import docker
import tempfile
import tarfile
import io
import os
import shutil
import logging

class DockerCodeRunner:
    def __init__(self):
        self.client = docker.from_env()
        self.logger = logging.getLogger(__name__)

    def run(self, image_name, user_code, libraries, tests, script_parameters, cleanup=True):
        self.logger.info("Запуск контейнера с образом: %s", image_name)

        # Создаем временную директорию на хосте
        temp_dir = tempfile.mkdtemp()
        self.logger.debug("Создана временная директория: %s", temp_dir)

        # Путь, который будем монтировать в контейнер
        container_dir = '/mnt/app'

        try:
            # Сохраняем script.py
            script_path = os.path.join(temp_dir, "script.py")
            with open(script_path, "w") as f:
                script_code = self._generate_script(user_code, libraries, script_parameters)
                f.write(script_code)
            self.logger.debug("Сохранён script.py:\n%s", script_code)

            # Сохраняем test_script.py
            test_path = os.path.join(temp_dir, "test_script.py")
            with open(test_path, "w") as f:
                test_code = self._generate_tests(tests)
                f.write(test_code)
            self.logger.debug("Сохранён test_script.py:\n%s", test_code)

            # Запускаем контейнер
            container = self.client.containers.run(
                image=image_name,
                command="sleep infinity",
                detach=True,
                security_opt=["apparmor=docker_run_tests_profile"]
            )
            self.logger.info("Контейнер запущен: %s", container.id)

            # Проверяем, существует ли директория и создаём её при необходимости
            exec_result = container.exec_run("ls /mnt/app")
            if exec_result.exit_code != 0:
                self.logger.info("Директория /mnt/app не найдена, создаем её")
                container.exec_run("mkdir -p /mnt/app")

            # Копируем файлы из временной директории в контейнер
            self.logger.info("Копирование файлов в контейнер...")
            tar_data = self._create_tar_from_directory(temp_dir)
            self.client.containers.get(container.id).put_archive(container_dir, tar_data)
            
            try:
                # Установка библиотек, если они есть
                install_output = "No libraries to install"
                if libraries:
                    pip_cmd = f"pip install {' '.join(libraries)}"
                    self.logger.info("Установка библиотек: %s", pip_cmd)
                    exit_code, output = container.exec_run(pip_cmd)
                    install_output = output.decode().strip()
                    self.logger.debug("pip output:\n%s", install_output)
                    if exit_code != 0:
                        raise Exception(f"Ошибка установки библиотек:\n{install_output}")

                # Запуск тестов
                self.logger.info("Запуск тестов в контейнере...")
                exit_code, output = container.exec_run("env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover /mnt/app")
                test_output = output.decode().strip()
                self.logger.debug("Результаты тестов:\n%s", test_output)

                status = "success" if exit_code == 0 else "fail"
                return {
                    "status": status,
                    "install_output": install_output,
                    "test_output": test_output
                }

            finally:
                if cleanup:
                    self.logger.info("Остановка и удаление контейнера: %s", container.id)
                    container.kill()
                    container.remove()

        except Exception as e:
            self.logger.exception("Ошибка при выполнении кода:")
            raise

        finally:
            shutil.rmtree(temp_dir)
            self.logger.debug("Удалена временная директория: %s", temp_dir)

    def _create_tar_from_directory(self, src_dir):
        """Создаёт архив в формате tar из директории"""
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            tar.add(src_dir, arcname=".")
        tar_stream.seek(0)
        return tar_stream

    def _generate_script(self, user_code, libraries, script_params):
        imports = "\n".join(f"import {lib}" for lib in libraries)
        args = ", ".join(script_params)
        indented_code = "\n    ".join(user_code.splitlines())
        function = f"def script({args}):\n    {indented_code}"
        return f"{imports}\n\n{function}\n"

    def _generate_tests(self, tests):
        lines = [
            "import unittest",
            "from script import *",
            "",
            "class ScriptTestCase(unittest.TestCase):"
        ]

        for i, test in enumerate(tests):
            params = ", ".join(repr(p) for p in test["parameters"])
            expected = repr(test["results"][0])

            method = [
                f"    def test_case_{i}(self):",
                f"        result = script({params})",
                f"        self.assertEqual(result, {expected})",
            ]

            lines.extend(method)

        lines.append("")
        lines.append("if __name__ == '__main__':")
        lines.append("    unittest.main()")

        return "\n".join(lines)