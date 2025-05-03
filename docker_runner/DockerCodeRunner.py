import docker
import tempfile
import tarfile
import io
import os
import shutil
import logging
import ast
import json

class DockerCodeRunner:
    def __init__(self):
        self.client = docker.from_env()
        self.logger = logging.getLogger(__name__)

    def run(self, image_name, user_code, libraries, tests, script_name, script_parameters, cleanup=True):
        self.logger.info("Запуск контейнера с образом: %s", image_name)

        self._validate_function(user_code, script_name, script_parameters)

        temp_dir = tempfile.mkdtemp()
        self.logger.debug("Создана временная директория: %s", temp_dir)

        container_dir = '/mnt/app'

        try:
            script_path = os.path.join(temp_dir, "script.py")
            with open(script_path, "w") as f:
                script_code = self._generate_script(user_code, libraries)
                f.write(script_code)
            self.logger.debug("Сохранён script.py:\n%s", script_code)

            test_path = os.path.join(temp_dir, "test_script.py")
            with open(test_path, "w") as f:
                test_code = self._generate_tests(tests, script_name)
                f.write(test_code)
            self.logger.debug("Сохранён test_script.py:\n%s", test_code)

            container = self.client.containers.run(
                image=image_name,
                command="sleep infinity",
                detach=True,
                security_opt=["apparmor=docker_run_tests_profile"]
            )
            self.logger.info("Контейнер запущен: %s", container.id)

            exec_result = container.exec_run("ls /mnt/app")
            if exec_result.exit_code != 0:
                container.exec_run("mkdir -p /mnt/app")

            self.logger.info("Копирование файлов в контейнер...")
            tar_data = self._create_tar_from_directory(temp_dir)
            self.client.containers.get(container.id).put_archive(container_dir, tar_data)

            try:
                install_output = "No libraries to install"
                if libraries:
                    pip_cmd = f"pip install {' '.join(libraries)}"
                    self.logger.info("Установка библиотек: %s", pip_cmd)
                    exit_code, output = container.exec_run(pip_cmd)
                    install_output = output.decode().strip()
                    self.logger.debug("pip output:\n%s", install_output)
                    if exit_code != 0:
                        raise Exception(f"Ошибка установки библиотек:\n{install_output}")

                self.logger.info("Запуск тестов в контейнере...")
                exit_code, output = container.exec_run("python3 /mnt/app/test_script.py")
                test_output = output.decode().strip()
                self.logger.debug("Результаты тестов:\n%s", test_output)

                if not test_output:
                    self.logger.error("Ошибка: вывод тестов пустой!")
                    return {
                        "status": "fail",
                        "error": "Вывод тестов пустой",
                        "raw_output": test_output
                    }

                try:
                    # Пробуем разобрать вывод как JSON
                    test_statuses = json.loads(test_output)

                    # Проверяем, что JSON содержит список словарей с полями 'id', 'name', 'status'
                    if not isinstance(test_statuses, list) or not all(
                        isinstance(t, dict) and "id" in t and "name" in t and "status" in t for t in test_statuses
                    ):
                        raise ValueError("Неверный формат данных тестов")

                    # Формируем статус, в зависимости от кода выхода контейнера
                    status = "success" if exit_code == 0 else "fail"

                    # Возвращаем результат с дополнительными полями 'id' и 'name'
                    return {
                        "status": status,
                        "install_output": install_output,
                        "test_output": test_output,
                        "test_statuses": test_statuses
                    }

                except json.JSONDecodeError as e:
                    self.logger.exception("Ошибка при разборе JSON из вывода тестов:")
                    return {
                        "status": "fail",
                        "error": f"Невозможно разобрать JSON: {str(e)}",
                        "raw_output": test_output
                    }

                except ValueError as e:
                    self.logger.exception("Ошибка в структуре результатов тестов:")
                    return {
                        "status": "fail",
                        "error": str(e),
                        "raw_output": test_output
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
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            tar.add(src_dir, arcname=".")
        tar_stream.seek(0)
        return tar_stream

    def _validate_function(self, code: str, function_name: str, required_params: list):
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            raise Exception(f"Синтаксическая ошибка в коде:\n{str(e)}")

        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == function_name:
                actual_params = [arg.arg for arg in node.args.args]
                missing = [p for p in required_params if p not in actual_params]
                if missing:
                    raise Exception(f"Функция '{function_name}' не содержит необходимые параметры: {', '.join(missing)}")
                return

        raise Exception(f"Функция с именем '{function_name}' не найдена в коде участника.")

    def _generate_script(self, user_code, libraries):
        imports = "\n".join(f"import {lib}" for lib in libraries)
        return f"{imports}\n\n{user_code.strip()}\n"

    def _generate_tests(self, tests, script_name):
        lines = [
            "import unittest",
            "import json",
            "import traceback",
            "from script import *",
            "",
            "class ScriptTestCase(unittest.TestCase):"
        ]

        for test in tests:
            params = ", ".join(repr(p) for p in test["parameters"])
            expected = repr(test["results"][0])

            # Используем id и name из JSON для каждого теста
            test_id = test.get("id")  # Получаем id теста
            test_name = test.get("name", f"test_case_{test_id}")  # Используем name, если оно передано

            lines.extend([
                f"    def test_case_{test_id}(self):",  # Название теста, включая id
                f"        result = {script_name}({params})",
                f"        self.assertEqual(result, {expected})"
            ])

        lines.extend([
            "",
            "class CustomTestResult(unittest.TextTestResult):",
            "    def __init__(self, *args, **kwargs):",
            "        super().__init__(*args, **kwargs)",
            "        self.successes = []",
            "",
            "    def addSuccess(self, test):",
            "        super().addSuccess(test)",
            "        self.successes.append(test)",
            "",
            "if __name__ == '__main__':",
            "    suite = unittest.TestLoader().loadTestsFromTestCase(ScriptTestCase)",
            "    runner = unittest.TextTestRunner(resultclass=CustomTestResult, stream=open('/dev/null', 'w'))",
            "    result = runner.run(suite)",
            "    output = []",
            "    for test in result.successes:",
            "        try:",
            "            # Проверяем формат имени метода и безопасно извлекаем id и name",
            "            test_method_parts = test._testMethodName.split('_')",
            "            test_id = test_method_parts[2]  # Получаем id из имени метода",
            "            test_name = test_method_parts[3] if len(test_method_parts) > 3 else f'test_case_{test_id}'  # Получаем name, если он есть",
            "            output.append({'id': test_id, 'name': test_name, 'status': 'success'})",
            "        except IndexError as e:",
            "            output.append({'status': 'error', 'error': f'Ошибка при извлечении id или name: {str(e)}'})",
            "    for test, err in result.failures + result.errors:",
            "        try:",
            "            test_method_parts = test._testMethodName.split('_')",
            "            test_id = test_method_parts[2]  # Получаем id из имени метода",
            "            test_name = test_method_parts[3] if len(test_method_parts) > 3 else f'test_case_{test_id}'",
            "            output.append({'id': test_id, 'name': test_name, 'status': 'fail', 'error': err})",
            "        except IndexError as e:",
            "            output.append({'status': 'error', 'error': f'Ошибка при извлечении id или name: {str(e)}'})",
            "    print(json.dumps(output))"
        ])

        return "\n".join(lines)










