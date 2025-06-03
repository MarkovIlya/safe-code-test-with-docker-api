import docker
import tempfile
import tarfile
import io
import os
import shutil
import logging
import ast
import json
import textwrap
import base64


class DockerCodeRunner:
    def __init__(self):
        self.client = docker.from_env()
        self.logger = logging.getLogger(__name__)
        default_image = "python:3.11"
        try:
            self.client.images.get(default_image)
            logging.info(f"Базовый образ '{default_image}' уже загружен.")
        except docker.errors.ImageNotFound:
            logging.info(f"Образ '{default_image}' не найден. Загружаем...")
            self.client.images.pull(default_image)
        except Exception as e:
            logging.error(f"Ошибка при проверке/загрузке образа: {e}")

    def run(self, image_name, user_code, libraries, tests, script_name, script_parameters, timeout_ms=2000, cleanup=True):
        self.logger.info(f"Запуск контейнера с образом: {image_name}")
        self._validate_function(user_code, script_name, script_parameters)

        temp_dir = tempfile.mkdtemp()
        container_dir = '/mnt/app'
        self.logger.debug(f"Создана временная директория: {temp_dir}")

        try:

            try:

                # запуск контейнера
                container = self._start_container(image_name)
                # установка библиотек
                install_output = self._install_libraries(container, libraries)
                # получение разрешённых модулей внутри контейнера
                allowed_modules = self._get_installed_modules(container)

                func_name = self._extract_function_name(user_code)

                # генерация защищённого main.py на основе уже полученных allowed_modules
                main_path = os.path.join(temp_dir, "main.py")
                user_code_path = os.path.join(temp_dir, "user_code.py")
                tests_path = os.path.join(temp_dir, "test_script.py")
                self._write_file(main_path, self._generate_main(func_name, allowed_modules))
                self._write_file(user_code_path, self._generate_user_code(user_code))
                self._write_file(tests_path, self._generate_tests(tests=tests, timeout_sec=timeout_ms / 1000.0))

                # передача файлов внутри контейнера
                self._prepare_container(container, temp_dir, container_dir)

                # запуск тестов
                stdout, stderr, exit_code = self._run_tests(container)

                # интерпретация результатов тестов
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
        self.logger.debug(f"Начало обработки результатов тестов. Exit code: {exit_code}")
        
        # Обработка случаев, когда тесты не вернули JSON
        if not stdout:
            error_type = "EMPTY_OUTPUT" if not stderr else "RUNTIME_ERROR"
            error_msg = stderr if stderr else "Тесты не вернули результат (пустой вывод)"
            
            return {
                "status": "fail",
                "error": {
                    "type": error_type,
                    "message": error_msg
                },
                "install_output": install_output,
                "raw_output": stdout,
                "stderr": stderr
            }

        try:
            test_statuses = json.loads(stdout)
            
            # Проверка структуры результатов тестов
            if not isinstance(test_statuses, list):
                return {
                    "status": "fail",
                    "error": {
                        "type": "INVALID_TEST_STRUCTURE",
                        "message": f"Ожидался список тестов, получен {type(test_statuses)}"
                    },
                    "install_output": install_output,
                    "raw_output": stdout,
                    "stderr": stderr
                }
                
            # Добавляем обработку ошибок для каждого теста
            for test in test_statuses:
                if test.get("status") == "fail":
                    if "error" not in test or not isinstance(test["error"], dict):
                        test["error"] = {
                            "type": "TEST_FAILURE",
                            "message": test.get("error", "Тест не пройден")
                        }

            status = "success" if exit_code == 0 else "fail"
            
            return {
                "status": status,
                "install_output": install_output,
                "test_output": stdout,
                "test_statuses": test_statuses
            }

        except json.JSONDecodeError as e:
            error_msg = str(e)
            if "SECURITY_ERROR" in stderr:
                error_type = "SECURITY_VIOLATION"
                error_msg = stderr.split("SECURITY_ERROR:")[-1].strip()
            elif "IMPORT_ERROR" in stderr:
                error_type = "IMPORT_ERROR"
                error_msg = stderr.split("IMPORT_ERROR:")[-1].strip()
            elif "RUNTIME_ERROR" in stderr:
                error_type = "RUNTIME_ERROR"
                error_msg = stderr.split("RUNTIME_ERROR:")[-1].strip()
            else:
                error_type = "PARSE_ERROR"
                
            return {
                "status": "fail",
                "error": {
                    "type": error_type,
                    "message": error_msg
                },
                "install_output": install_output,
                "raw_output": stdout,
                "stderr": stderr
            }

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

    def _get_installed_modules(self, container) -> list[str]:
        self.logger.info("Получение разрешённых модулей внутри контейнера...")
        script = textwrap.dedent("""
            import json
            import importlib.metadata
            import sys

            modules = set()
            for dist in importlib.metadata.distributions():
                try:
                    top_level = dist.read_text("top_level.txt")
                    if top_level:
                        for line in top_level.strip().splitlines():
                            modules.add(line.strip())
                except Exception:
                    continue
            print(json.dumps(list(modules)))
        """)
        # Кодируем скрипт в base64
        encoded = base64.b64encode(script.encode()).decode()

        # Расшифровка и исполнение в контейнере
        cmd = f"python3 -c \"import base64; exec(base64.b64decode('{encoded}'))\""

        exit_code, output = container.exec_run(cmd)
        if exit_code != 0:
            raise RuntimeError(f"Не удалось получить список модулей внутри контейнера:\n{output.decode()}")
        return json.loads(output.decode().strip())

    def _generate_main(self, func_name: str, allowed_modules: list[str]) -> str:
        return textwrap.dedent(f"""
            import sys
            import json
            import traceback

            # 1. Устанавливаем аудит-хук ДО любых других операций
            last_security_error = None
            whitelist = {{
                'sys', 'json', 'builtins',  # Базовые модули
                *{allowed_modules!r}  # Разрешённые модули из конфига
            }}
            blacklist = {{
                'os', 'subprocess', 'socket', 'threading', 'multiprocessing',
                'ctypes', 'signal', 'shutil', 'sysconfig', 'requests', 'urllib',
                'inspect', 'compileall'
            }}

            def audit_hook(event: str, args: tuple):
                global last_security_error
                if event == 'import':
                    module = args[0].split('.')[0]
                    if module == "user_code":
                        return
                    if module in blacklist or module not in whitelist:
                        last_security_error = f'SECURITY_ERROR: Импорт модуля "{{module}}" запрещён!'
                        raise ImportError(last_security_error)
                elif event == 'compile':
                    last_security_error = 'SECURITY_ERROR: Динамическая генерация кода запрещена!'
                    raise RuntimeError(last_security_error)

            sys.addaudithook(audit_hook)

            # 2. Импортируем функцию из user_code.py
            try:
                from user_code import {func_name}
            except ImportError as e:
                error_type = "IMPORT_ERROR"
                error_msg = str(e)
                print({{
                    "type": error_type,
                    "message": error_msg,
                    "traceback": traceback.format_exc()
                }}, file=sys.stderr, flush=True)
                sys.exit(1)
            except Exception as e:
                error_type = "RUNTIME_ERROR"
                error_msg = str(e)
                print({{
                    "type": error_type,
                    "message": error_msg,
                    "traceback": traceback.format_exc()
                }}, file=sys.stderr, flush=True)
                sys.exit(1)

            # 3. Вызываем функцию участника
            if __name__ == "__main__":
                args = [json.loads(arg) for arg in sys.argv[1:]]
                try:
                    result = {func_name}(*args)
                    print(json.dumps(result))
                except Exception as e:
                    error_type = "RUNTIME_ERROR"
                    error_msg = str(e)
                    print({{
                        "type": error_type,
                        "message": error_msg,
                        "traceback": traceback.format_exc()
                    }}, file=sys.stderr, flush=True)
                    sys.exit(1)
        """)



    def _generate_user_code(self, user_code: str) -> str:
        return user_code.strip()


    def _extract_function_name(self, code: str) -> str:
        tree = ast.parse(code)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                return node.name
        raise Exception("Не удалось извлечь имя функции из кода")

    # Решить Assertion Error
    def _generate_tests(self, tests, timeout_sec=2):
        test_cases = []
        for idx, test in enumerate(tests):
            params_list = [json.dumps(p) for p in test["parameters"]]
            params = ", ".join(repr(p) for p in params_list)
            expected = str(test["results"][0])
            test_id = test.get("id", f"test_{idx + 1}")

            test_cases.append(f"""
    def test_case_{test_id}(self):
        import subprocess
        import traceback
        import json

        self._test_error = None
        self._test_error_type = None
        self._test_traceback = None

        try:
            command = ['python3', '/mnt/app/main.py'] + [{params}]
            proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            out, err = proc.communicate(timeout={timeout_sec})
            exit_code = proc.returncode
            stdout = out.decode().strip()
            stderr = err.decode().strip()

            # Обработка ошибок безопасности
            if exit_code == 42 or "SECURITY_ERROR" in stderr:
                self._test_error_type = "SECURITY_VIOLATION"
                self._test_error = stderr.split("SECURITY_ERROR:")[-1].strip()
                raise RuntimeError(self._test_error)

            # Обработка JSON ошибок из stderr
            if stderr and stderr.startswith('{{"type":'):
                try:
                    error_data = json.loads(stderr)
                    self._test_error_type = error_data.get("type", "UNKNOWN_ERROR")
                    self._test_error = error_data.get("message", "Неизвестная ошибка")
                    self._test_traceback = error_data.get("traceback", "")
                    raise RuntimeError(self._test_error)
                except json.JSONDecodeError:
                    pass

            # Проверка результата
            try:
                result = json.loads(stdout) if stdout else None
                self.assertEqual(result, {expected})
            except json.JSONDecodeError:
                self._test_error_type = "INVALID_OUTPUT"
                self._test_error = f"Некорректный JSON вывод: {{stdout}}"
                raise

        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            self._test_error_type = "TIMEOUT"
            self._test_error = f"Тест превысил лимит времени ({timeout_sec} сек)"
            self._test_traceback = err.decode().strip()
        except AssertionError as ae:
            self._test_error_type = "ASSERTION_ERROR"
            self._test_error = str(ae)
            self._test_traceback = traceback.format_exc()
        except Exception as e:
            if not self._test_error_type:
                self._test_error_type = "RUNTIME_ERROR"
                self._test_error = str(e)
                self._test_traceback = traceback.format_exc()
            
        if self._test_error:
            self.fail(self._test_error)
    """)

        return "\n".join([
            "import unittest",
            "import json",
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
            "    runner = unittest.TextTestRunner(resultclass=CustomTestResult)",
            "    result = runner.run(suite)",
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
            "        short_error = getattr(test, '_test_error', 'Unknown error')",
            "        tb = getattr(test, '_test_traceback', err)",
            "        output.append({",
            "            'id': test_id,",
            "            'name': test_method_name,",
            "            'status': 'fail',",
            "            'error': short_error,",
            "            'traceback': tb",
            "        })",
            "",
            "    print(json.dumps(output))"
        ])



