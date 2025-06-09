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
import subprocess

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
                container = self._start_container(image_name)

                # Устанавливаем библиотеки (если переданы)
                install_output = self._install_libraries(container, libraries)

                # Пытаемся загрузить allowed_modules.json из образа
                try:
                    allowed_modules = self._load_allowed_modules_from_image(image_name)
                except Exception as e:
                    self.logger.warning(f"Не удалось загрузить allowed_modules.json из образа: {e}")
                    self.logger.info("Генерируем allowed_modules.json внутри контейнера...")

                    # 1. Создаём скрипт генерации
                    generator_code = self.generate_allowed_modules_script(libraries)
                    generator_path = os.path.join(temp_dir, "generate_allowed_modules.py")
                    self._write_file(generator_path, generator_code)

                    # 2. Копируем в контейнер
                    self._prepare_container(container, temp_dir, container_dir)

                    # 3. Запускаем скрипт внутри контейнера
                    stdout, stderr, exit_code = self._exec_in_container(container, ["python3", "generate_allowed_modules.py"])
                    if exit_code != 0:
                        raise RuntimeError(f"Ошибка при генерации allowed_modules.json: {stderr}")

                    allowed_modules = self._load_allowed_modules_from_container(container.id)

                self.logger.info("Разрешённые модули внутри контейнера: %s", allowed_modules)

                # Генерируем файлы main.py и test_script.py
                main_path = os.path.join(temp_dir, "main.py")
                tests_path = os.path.join(temp_dir, "test_script.py")
                func_name = self._extract_function_name(user_code)

                self._write_file(main_path, self._generate_main(user_code, func_name, allowed_modules))
                self._write_file(tests_path, self._generate_tests(tests=tests, timeout_sec=timeout_ms / 1000.0))

                # Копируем все файлы (ещё раз, чтобы обновить main.py и тесты)
                self._prepare_container(container, temp_dir, container_dir)

                # Запускаем тесты
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


    def _exec_in_container(self, container, command, working_dir="/mnt/app"):
        """
        Выполняет команду внутри контейнера и возвращает stdout, stderr, exit_code.
        """
        exec_result = self.client.api.exec_create(
            container.id,
            cmd=command,
            workdir=working_dir
        )
        output = self.client.api.exec_start(exec_result["Id"], demux=True)
        exit_code = self.client.api.exec_inspect(exec_result["Id"])["ExitCode"]

        stdout, stderr = output
        stdout = stdout.decode("utf-8") if stdout else ""
        stderr = stderr.decode("utf-8") if stderr else ""

        self.logger.debug(f"Выполнена команда в контейнере: {' '.join(command)}")
        self.logger.debug(f"stdout: {stdout}")
        self.logger.debug(f"stderr: {stderr}")
        self.logger.debug(f"exit_code: {exit_code}")

        return stdout, stderr, exit_code


    def generate_allowed_modules_script(self, libraries):
        return f'''\
import json
import sys
import pkgutil
import importlib
import importlib.metadata
import subprocess
import site
import os

allowed = set()

# Встроенные модули
allowed |= set(sys.builtin_module_names)

# pip list
try:
    output = subprocess.check_output(["pip", "list", "--format=json"], text=True)
    installed = json.loads(output)
    package_names = [pkg["name"] for pkg in installed]
    for pkg in package_names:
        try:
            module = importlib.import_module(pkg)
            allowed.add(pkg.lower())
            if hasattr(module, "__path__"):
                for _, name, _ in pkgutil.walk_packages(module.__path__, module.__name__ + "."):
                    allowed.add(name.split(".")[0])
        except Exception:
            continue
except Exception:
    pass

# top_level.txt
try:
    for dist in importlib.metadata.distributions():
        try:
            top_level = dist.read_text('top_level.txt')
            if top_level:
                for name in top_level.strip().splitlines():
                    allowed.add(name.strip())
        except Exception:
            continue
except Exception:
    pass

# sys.path scan
for finder, name, _ in pkgutil.iter_modules():
    allowed.add(name)

# site-packages scan
for sp in site.getsitepackages():
    try:
        for entry in os.listdir(sp):
            path = os.path.join(sp, entry)
            if os.path.isdir(path) and entry.isidentifier():
                allowed.add(entry)
            elif entry.endswith(".py") and entry[:-3].isidentifier():
                allowed.add(entry[:-3])
    except Exception:
        continue

# Импортируем корневые модули из переданных библиотек
requested_modules = {libraries!r}
for lib in requested_modules:
    root = lib.split('.')[0]
    try:
        importlib.import_module(root)
        allowed.add(root)
    except Exception:
        pass

# Удалим пустые значения
allowed = {{x for x in allowed if x}}

# Сохраняем
with open("/allowed_modules.json", "w", encoding="utf-8") as f:
    json.dump(sorted(allowed), f, ensure_ascii=False)
'''

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
    
    def _extract_function_name(self, code):
        tree = ast.parse(code)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                return node.name
        raise Exception("Не удалось извлечь имя функции из кода")

    def _run_tests(self, container):
        self.logger.info("Запуск тестов в контейнере...")
        exit_code, output = container.exec_run("python3 /mnt/app/test_script.py", demux=True)
        stdout, stderr = (output[0] or b"").decode().strip(), (output[1] or b"").decode().strip()
        self.logger.debug(f"STDOUT тестов:\n{stdout}")
        self.logger.debug(f"STDERR тестов:\n{stderr}")
        return stdout, stderr, exit_code

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

    def _load_allowed_modules_from_image(self, image_name):
        """
        Извлекает /allowed_modules.json из образа и возвращает список модулей.
        """
        container = self.client.containers.create(image=image_name, command="cat /allowed_modules.json")
        try:
            container.start()
            output = container.logs(stdout=True, stderr=False)
            return json.loads(output.decode("utf-8"))
        finally:
            container.remove(force=True)

    def _get_installed_modules(self, image_name):
        """
        Возвращает список всех установленных модулей внутри образа.
        """
        command = (
            "python3 -c \"import pkgutil, json; "
            "print(json.dumps(sorted(set([m.name for m in pkgutil.iter_modules()]))) )\""
        )
        output = self.client.containers.run(
            image=image_name,
            command=command,
            remove=True
        )
        return json.loads(output.decode())
    
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
    

    def _load_allowed_modules_from_container(self, container_id):
        command = ["docker", "exec", container_id, "cat", "/allowed_modules.json"]
        output = subprocess.check_output(command)
        if not output.strip():
            raise RuntimeError("Файл allowed_modules.json отсутствует или пуст в контейнере")
        return json.loads(output.decode("utf-8"))


    def _generate_main(self, user_code, func_name, allowed_modules):
        return textwrap.dedent(f"""
import sys
import json
import traceback
import io
import builtins

# Загрузка разрешённых модулей из Docker-образа
try:
    with open('/allowed_modules.json', 'r') as f:
        raw_modules = json.load(f)
except Exception as e:
    print(json.dumps({{
        "type": "SECURITY_VIOLATION",
        "message": "Не удалось загрузить /allowed_modules.json",
        "traceback": str(e)
    }}), file=sys.stderr)
    sys.exit(42)

# Функция извлечения имени модуля
def extract_module_name(name):
    return name.split('>=')[0].split('==')[0].split('<=')[0].strip()

# Модули, разрешённые по умолчанию и через зависимые библиотеки
ALLOWED_MODULES = set(map(extract_module_name, raw_modules))

# Явно разрешённые (например, из /run запроса)
EXTRA_ALLOWED = set({allowed_modules!r})

# Финальный whitelist
WHITELIST = {{
    'sys', 'json', 'builtins', "org", "ctypes"
}} | ALLOWED_MODULES | EXTRA_ALLOWED

# Явно запрещённые модули
BLACKLIST = {{
    'os', 'subprocess', 'socket', 'threading', 'multiprocessing',
    'signal', 'shutil', 'sysconfig', 'requests', 'urllib',
    'inspect', 'compileall'
}} 

# Проверка, является ли фрейм доверенным (например, из разрешённой библиотеки)
def is_trusted_module(frame):
    module_name = frame.f_globals.get('__name__')
    if module_name:
        root = module_name.split('.')[0]
        if root in WHITELIST:
            return True

    file_path = frame.f_globals.get('__file__')
    if file_path and any(path in file_path for path in ('/site-packages/', '/usr/local/lib/python', '/lib/python')):
        return True

    return False

# Аудит-хук для контроля событий безопасности
def audit_hook(event, args):
    if event in ('compile', 'exec'):
        trusted = False
        for i in range(10):
            try:
                frame = sys._getframe(i)
                if is_trusted_module(frame):
                    trusted = True
                    break
            except Exception:
                continue

        if not trusted:
            print(json.dumps({{
                "type": "SECURITY_VIOLATION",
                "message": f"Динамическая генерация кода запрещена ({{event}})",
                "traceback": ""
            }}), file=sys.stderr, flush=True)
            sys.exit(42)

    elif event == 'import':
        module = args[0].split('.')[0]
        if module not in WHITELIST or module in BLACKLIST:
            # 🔍 Отладка запрещённого импорта
            print(f"[DEBUG] Запрещённый импорт модуля: '{{module}}'", file=sys.stderr, flush=True)
            traceback.print_stack(file=sys.stderr)
            print(json.dumps({{
                "type": "SECURITY_VIOLATION",
                "message": f"Импорт модуля '{{module}}' запрещён",
                "traceback": ""
            }}), file=sys.stderr, flush=True)
            sys.exit(42)

# Установка хука
sys.addaudithook(audit_hook)

# Блокировка встроенных опасных функций (будет применена позже)
class SecurityViolation(Exception):
    pass

def block_builtin(name):
    def wrapper(*args, **kwargs):
        raise SecurityViolation(f"Использование {{name}} запрещено")
    return wrapper

# Пробежаться по BLACKLIST и удалить каждый модуль из sys.modules(), а после удалить sys
# Закешировать sys.exit

# Вставка пользовательского кода
{user_code.strip()}

# Вызов тестируемой функции
if __name__ == "__main__":
    try:
        args = [json.loads(arg) for arg in sys.argv[1:]]

        # Перехват stdout
        stdout_backup = sys.stdout
        fake_stdout = io.StringIO()
        sys.stdout = fake_stdout

        try:
            func = globals().get("{func_name}")
            if not callable(func):
                raise ValueError("Функция '{func_name}' не найдена")
            result = func(*args)
        finally:
            sys.stdout = stdout_backup

        if result is None:
            raise ValueError("Функция вернула None. Убедитесь, что используется оператор return.")

        try:
            json_output = json.dumps(result)
            print(f"[DEBUG] Результат перед JSON: {{result!r}}", file=sys.stderr, flush=True)
            print(f"[DEBUG] JSON результат: {{json_output}}", file=sys.stderr, flush=True)
            print(json_output)
        except Exception:
            raise ValueError(f"Результат не сериализуем в JSON: {{result!r}}")

    except SecurityViolation as sv:
        print(json.dumps({{
            "type": "SECURITY_VIOLATION",
            "message": str(sv),
            "traceback": ""
        }}), file=sys.stderr, flush=True)
        sys.exit(42)

    except Exception as e:
        print(json.dumps({{
            "type": "RUNTIME_ERROR",
            "message": str(e),
            "traceback": traceback.format_exc()
        }}), file=sys.stderr, flush=True)
        sys.exit(1)
""")

    def _generate_tests(self, tests, timeout_sec=2):
        test_cases = []
        for idx, test in enumerate(tests):
            params_list = [json.dumps(p) for p in test["parameters"]]
            params = ", ".join(repr(p) for p in params_list)
            expected = str(test["results"][0])
            test_id = test.get("id", f"{idx + 1}")

            test_cases.append(
                f"    def test_case_{test_id}(self):\n"
                f"        self._run_test_case({params}, expected={expected}, timeout={timeout_sec}, test_id='{test_id}')"
            )

        return "\n".join([
            "import unittest",
            "import json",
            "import subprocess",
            "import traceback",
            "import sys",
            "",
            "class ScriptTestCase(unittest.TestCase):",
            "    def _run_test_case(self, *args, expected, timeout, test_id):",
            "        self._test_error = None",
            "        self._test_error_type = None",
            "        self._test_traceback = None",
            "        try:",
            "            command = ['python3', '/mnt/app/main.py'] + list(args)", # Создать списки whitelist и blacklist
            "            proc = subprocess.Popen(",
            "                command,",
            "                stdout=subprocess.PIPE,",
            "                stderr=subprocess.PIPE",
            "            )",
            "",
            "            out, err = proc.communicate(timeout=timeout)",
            "            exit_code = proc.returncode",
            "            stdout = out.decode().strip()",
            "            stderr = err.decode().strip()",
            "",
            "            print(f'[DEBUG] STDOUT: {stdout}', file=sys.stderr , flush=True)",
            "            print(f'[DEBUG] STDERR: {stderr}', file=sys.stderr , flush=True)",
            "            print(f'[DEBUG] EXIT CODE: {exit_code}', file=sys.stderr , flush=True)",
            "",
            "            if exit_code == 42:",
            "                self._test_error_type = 'SECURITY_VIOLATION'",
            "                try:",
            "                    first_json_line = next((line for line in stderr.splitlines() if line.strip().startswith('{')), None)",
            "                    if first_json_line:",
            "                        error_data = json.loads(first_json_line)",
            "                        self._test_error = error_data.get('message', 'Нарушение политики безопасности')",
            "                        self._test_traceback = error_data.get('traceback', '')",
            "                    else:",
            "                        self._test_error = 'Нарушение политики безопасности'",
            "                except Exception:",
            "                    self._test_error = 'Нарушение политики безопасности'",
            "                raise RuntimeError(self._test_error)",
            "",
            "            if exit_code == 2:",
            "                self._test_error_type = 'MAIN_NOT_FOUND'",
            "                self._test_error = 'main.py не найден или не запускается'",
            "                self._test_traceback = stderr",
            "                raise RuntimeError(self._test_error)",
            "",
            "            if exit_code != 0:",
            "                self._test_error_type = 'NON_ZERO_EXIT'",
            "                self._test_error = f'Код выхода: {exit_code}. STDERR: {stderr}'",
            "                self._test_traceback = f'Вывод STDOUT: {stdout}\\nВывод STDERR: {stderr}'",
            "                raise RuntimeError(self._test_error)",
            "",
            "            try:",
            "                first_json_line = next((line for line in stderr.splitlines() if line.strip().startswith('{')), None)",
            "                if first_json_line:",
            "                    error_data = json.loads(first_json_line)",
            "                    self._test_error_type = error_data.get('type', 'UNKNOWN_ERROR')",
            "                    self._test_error = error_data.get('message', 'Неизвестная ошибка')",
            "                    self._test_traceback = error_data.get('traceback', '')",
            "                    raise RuntimeError(self._test_error)",
            "            except Exception:",
            "                pass",
            "",
            "            try:",
            "                stdout_lines = stdout.splitlines()",
            "                last_line = stdout_lines[-1] if stdout_lines else ''",
            "                print(f'[DEBUG] RAW STDOUT: {stdout!r}', file=sys.stderr, flush=True)",
            "                result = json.loads(last_line)",
            "                self.assertEqual(result, expected)",
            "            except json.JSONDecodeError:",
            "                self._test_error_type = 'INVALID_OUTPUT'",
            "                self._test_error = f'Некорректный JSON вывод: {stdout}'",
            "                raise",
            "",
            "        except subprocess.TimeoutExpired:",
            "            proc.kill()",
            "            out, err = proc.communicate()",
            "            self._test_error_type = 'TIMEOUT'",
            "            self._test_error = f'Тест превысил лимит времени ({timeout} сек)'",
            "            self._test_traceback = err.decode().strip()",
            "        except AssertionError as ae:",
            "            self._test_error_type = 'ASSERTION_ERROR'",
            "            self._test_error = str(ae)",
            "            self._test_traceback = traceback.format_exc()",
            "        except Exception as e:",
            "            if not self._test_error_type:",
            "                self._test_error_type = 'RUNTIME_ERROR'",
            "                self._test_error = str(e)",
            "                self._test_traceback = traceback.format_exc()",
            "",
            "        if self._test_error:",
            "            self.fail(self._test_error)",
            "",
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
            "    print(json.dumps(output), file=sys.stdout, flush=True)"
        ])
