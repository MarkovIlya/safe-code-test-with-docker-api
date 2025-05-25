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
        self.logger.info(f"Запуск контейнера с образом: {image_name}")
        self._validate_function(user_code, script_name, script_parameters)

        temp_dir = tempfile.mkdtemp()
        container_dir = '/mnt/app'
        self.logger.debug(f"Создана временная директория: {temp_dir}")

        try:
            self._write_file(os.path.join(temp_dir, "script.py"), self._generate_script(user_code, libraries))
            self._write_file(os.path.join(temp_dir, "test_script.py"), self._generate_tests(tests, script_name))

            container = self._start_container(image_name)
            self._prepare_container(container, temp_dir, container_dir)

            try:
                install_output = self._install_libraries(container, libraries)
                stdout, stderr, exit_code = self._run_tests(container)

                return self._parse_test_results(stdout, stderr, exit_code, install_output)

            finally:
                if cleanup:
                    self._cleanup_container(container)

        except Exception as e:
            self.logger.exception("Ошибка при выполнении кода:")
            raise

        finally:
            shutil.rmtree(temp_dir)
            self.logger.debug(f"Удалена временная директория: {temp_dir}")

    def _write_file(self, path, content):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        self.logger.debug(f"Сохранён файл {path}:\n{content}")

    def _start_container(self, image_name):
        container = self.client.containers.run(
            image=image_name,
            command="sleep infinity",
            detach=True
        )
        self.logger.info(f"Контейнер запущен: {container.id}")
        return container

    def _prepare_container(self, container, host_dir, container_dir):
        # Создание директории, если не существует
        container.exec_run(f"mkdir -p {container_dir}")
        self.logger.info("Копирование файлов в контейнер...")
        tar_data = self._create_tar_from_directory(host_dir)
        container.put_archive(container_dir, tar_data)

    def _install_libraries(self, container, libraries):
        if not libraries:
            return "No libraries to install"

        pip_cmd = f"pip install {' '.join(libraries)}"
        self.logger.info(f"Установка библиотек: {pip_cmd}")
        exit_code, output = container.exec_run(pip_cmd)

        decoded_output = output.decode().strip()
        self.logger.debug(f"pip output:\n{decoded_output}")

        if exit_code != 0:
            raise Exception(f"Ошибка установки библиотек:\n{decoded_output}")
        return decoded_output

    def _run_tests(self, container):
        self.logger.info("Запуск тестов в контейнере...")
        exit_code, output = container.exec_run("python3 /mnt/app/test_script.py", demux=True)
        stdout, stderr = (output[0] or b"").decode().strip(), (output[1] or b"").decode().strip()

        self.logger.debug(f"STDOUT тестов:\n{stdout}")
        self.logger.debug(f"STDERR тестов:\n{stderr}")

        return stdout, stderr, exit_code

    def _parse_test_results(self, stdout, stderr, exit_code, install_output):
        if not stdout:
            self.logger.error("Ошибка: stdout тестов пустой!")
            return {"status": "fail", "error": "Вывод тестов пустой", "raw_output": stdout, "stderr": stderr}

        try:
            test_statuses = json.loads(stdout)
            if not isinstance(test_statuses, list) or not all(
                isinstance(t, dict) and {"id", "name", "status"}.issubset(t) for t in test_statuses
            ):
                raise ValueError("Неверный формат данных тестов")

            return {
                "status": "success" if exit_code == 0 else "fail",
                "install_output": install_output,
                "test_output": stdout,
                "test_statuses": test_statuses
            }

        except json.JSONDecodeError as e:
            self.logger.exception("Ошибка при разборе JSON:")
            return {"status": "fail", "error": f"Невозможно разобрать JSON: {e}", "raw_output": stdout}

        except ValueError as e:
            self.logger.exception("Ошибка в структуре результатов тестов:")
            return {"status": "fail", "error": str(e), "raw_output": stdout}

    def _cleanup_container(self, container):
        self.logger.info(f"Остановка и удаление контейнера: {container.id}")
        container.kill()
        container.remove()

    def _create_tar_from_directory(self, src_dir):
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            tar.add(src_dir, arcname=".")
        tar_stream.seek(0)
        return tar_stream

    def _validate_function(self, code, function_name, required_params):
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            raise Exception(f"Синтаксическая ошибка в коде:\n{e}")

        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == function_name:
                actual_params = [arg.arg for arg in node.args.args]
                missing = [p for p in required_params if p not in actual_params]
                if missing:
                    raise Exception(
                        f"Функция '{function_name}' не содержит необходимые параметры: {', '.join(missing)}"
                    )
                return

        raise Exception(f"Функция с именем '{function_name}' не найдена в коде участника.")

    def _generate_script(self, user_code, libraries):
        imports = "\n".join(f"import {lib}" for lib in libraries)
        return f"{imports}\n\n{user_code.strip()}\n"

    def _generate_tests(self, tests, script_name):
        test_cases = []
        for idx, test in enumerate(tests):
            params = ", ".join(repr(p) for p in test["parameters"])
            expected = repr(test["results"][0])
            test_id = test.get("id", f"test_{idx + 1}")
            test_cases.append(f"""
    def test_case_{test_id}(self):
        sys.stdout = open(os.devnull, 'w')
        result = {script_name}({params})
        self.assertEqual(result, {expected})
        sys.stdout = sys.__stdout__
""")

        return "\n".join([
            "import unittest",
            "import json",
            "import sys",
            "import os",
            "from script import *",
            "",
            "class ScriptTestCase(unittest.TestCase):",
            *test_cases,
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
            "        output.append({'id': test_id, 'name': test_method_name, 'status': 'success'})",
            "",
            "    for test, err in result.failures + result.errors:",
            "        test_method_name = test._testMethodName",
            "        test_id = test_method_name.split('_')[-1]",
            "        output.append({'id': test_id, 'name': test_method_name, 'status': 'fail', 'error': err})",
            "",
            "    print(json.dumps(output))"
        ])
