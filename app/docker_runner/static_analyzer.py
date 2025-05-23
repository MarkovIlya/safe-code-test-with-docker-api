import ast

FORBIDDEN_BUILTINS = {"eval", "exec", "compile", "open", "__import__"}
FORBIDDEN_MODULES = {"socket", "subprocess", "os", "sys"}

def analyze_code(code):
    issues = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"SyntaxError: {e}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_BUILTINS:
                issues.append(f"Использование запрещенных встроенных функций: {func.id}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split('.')[0] in FORBIDDEN_MODULES:
                    issues.append(f"Использование запрещённого модуля: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split('.')[0] in FORBIDDEN_MODULES:
                issues.append(f"Использование запрещённого модуля: {node.module}")
    return issues
