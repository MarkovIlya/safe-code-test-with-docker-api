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
            with open(script_path, "w", encoding="utf-8") as f:
                script_code = self._generate_script(user_code, libraries)
                f.write(script_code)
            self.logger.debug("Сохранён script.py:\n%s", script_code)

            test_path = os.path.join(temp_dir, "test_script.py")
            with open(test_path, "w", encoding="utf-8") as f:
                test_code = self._generate_tests(tests, script_name)
                f.write(test_code)
            self.logger.debug("Сохранён test_script.py:\n%s", test_code)

            container = self.client.containers.run(
                image=image_name,
                command="sleep infinity",
                detach=True,
                # security_opt=["apparmor=docker_run_tests_profile"]
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
                exit_code, output = container.exec_run("python3 /mnt/app/test_script.py", demux=True)
                stdout, stderr = output

                stdout = (stdout or b"").decode().strip()
                stderr = (stderr or b"").decode().strip()

                self.logger.debug("STDOUT тестов:\n%s", stdout)
                self.logger.debug("STDERR тестов:\n%s", stderr)

                if not stdout:
                    self.logger.error("Ошибка: stdout тестов пустой!")
                    return {
                        "status": "fail",
                        "error": "Вывод тестов пустой",
                        "raw_output": stdout,
                        "stderr": stderr
                    }

                try:
                    # Пробуем разобрать вывод как JSON
                    test_statuses = json.loads(stdout)

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
                        "test_output": stdout,
                        "test_statuses": test_statuses
                    }

                except json.JSONDecodeError as e:
                    self.logger.exception("Ошибка при разборе JSON из вывода тестов:")
                    return {
                        "status": "fail",
                        "error": f"Невозможно разобрать JSON: {str(e)}",
                        "raw_output": stdout
                    }

                except ValueError as e:
                    self.logger.exception("Ошибка в структуре результатов тестов:")
                    return {
                        "status": "fail",
                        "error": str(e),
                        "raw_output": stdout
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
            "import sys",
            "import os",
            "from script import *",
            "",
            "class ScriptTestCase(unittest.TestCase):"
        ]

        for idx, test in enumerate(tests):
            params = ", ".join(repr(p) for p in test["parameters"])
            expected = repr(test["results"][0])
            test_id = test.get("id", f"test_{idx+1}")

            lines.extend([
                f"    def test_case_{test_id}(self):",
                "        sys.stdout = open(os.devnull, 'w')",
                f"        result = {script_name}({params})",
                f"        self.assertEqual(result, {expected})",
                "        sys.stdout = sys.__stdout__",
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
            "    with open(os.devnull, 'w') as devnull:",
            "        runner = unittest.TextTestRunner(resultclass=CustomTestResult, stream=devnull)",
            "        result = runner.run(suite)",
            "",
            "    output = []",
            "    for test in result.successes:",
            "        test_method_name = test._testMethodName",
            "        test_id = test_method_name.split('_')[-1]",
            "        test_name = f'test_case_{test_id}'",
            "        output.append({'id': test_id, 'name': test_name, 'status': 'success'})",
            "",
            "    for test, err in result.failures + result.errors:",
            "        test_method_name = test._testMethodName",
            "        test_id = test_method_name.split('_')[-1]",
            "        test_name = f'test_case_{test_id}'",
            "        output.append({'id': test_id, 'name': test_name, 'status': 'fail', 'error': err})",
            "",
            "    print(json.dumps(output))"
        ])

        return "\n".join(lines)












