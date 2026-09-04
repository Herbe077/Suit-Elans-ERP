"""
AUDITORÍA ESTÁTICA COMPLETA DE PROYECTO FASTAPI / PYTHON

Objetivo:
    Analizar la arquitectura, ORM, FastAPI, templates, seguridad,
    dependencias, calidad de código y consistencia estructural.

Uso recomendado:

    Desde la raíz del proyecto:
        python -m app.audit_project

    O:
        python app/audit_project.py

Opcional:
        python app/audit_project.py --root /ruta/proyecto
        python app/audit_project.py --output auditoria.txt

Importante:
    La auditoría es principalmente ESTÁTICA.
    No ejecuta operaciones de escritura contra la base de datos.
    La importación de FastAPI se utiliza solamente como comprobación
    adicional y puede fallar si faltan dependencias/configuración.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import traceback
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ============================================================================
# CONFIGURACIÓN
# ============================================================================

IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".idea",
    ".vscode",
    "node_modules",
    "venv",
    ".venv",
    "env",
    ".env",
    "dist",
    "build",
    "site-packages",
}

IGNORE_FILES = {
    ".DS_Store",
    "Thumbs.db",
}

TEXT_EXTENSIONS = {
    ".py",
    ".html",
    ".htm",
    ".js",
    ".ts",
    ".css",
    ".scss",
    ".jinja",
    ".jinja2",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".txt",
    ".md",
    ".sql",
}

PYTHON_EXTENSIONS = {".py"}
TEMPLATE_EXTENSIONS = {".html", ".htm", ".jinja", ".jinja2"}

SECRET_PATTERNS = [
    (
        "API_KEY",
        re.compile(
            r"""(?i)\b(api[_-]?key|apikey)\b\s*[:=]\s*["'][^"']{8,}["']"""
        ),
    ),
    (
        "SECRET_KEY",
        re.compile(
            r"""(?i)\b(secret[_-]?key|app[_-]?secret)\b\s*[:=]\s*["'][^"']{8,}["']"""
        ),
    ),
    (
        "PASSWORD",
        re.compile(
            r"""(?i)\b(password|passwd|pwd)\b\s*[:=]\s*["'][^"']{3,}["']"""
        ),
    ),
    (
        "DATABASE_URL",
        re.compile(
            r"""(?i)\b(database[_-]?url|db[_-]?url)\b\s*[:=]\s*["'][^"']+["']"""
        ),
    ),
    (
        "TOKEN",
        re.compile(
            r"""(?i)\b(access[_-]?token|auth[_-]?token|bearer[_-]?token)\b\s*[:=]\s*["'][^"']{8,}["']"""
        ),
    ),
]

RISK_PATTERNS = [
    (
        "DEBUG_TRUE",
        re.compile(r"""(?i)\b(debug)\b\s*[:=]\s*True"""),
        "DEBUG=True puede exponer información sensible en producción.",
    ),
    (
        "VERIFY_FALSE",
        re.compile(r"""(?i)\bverify\s*=\s*False\b"""),
        "verify=False deshabilita la verificación TLS.",
    ),
    (
        "ALLOW_ALL_CORS",
        re.compile(
            r"""(?i)allow_origins\s*=\s*\[\s*["']\*["']\s*\]"""
        ),
        "CORS permite cualquier origen.",
    ),
    (
        "SHELL_TRUE",
        re.compile(r"""(?i)\bshell\s*=\s*True\b"""),
        "shell=True puede introducir riesgo de command injection.",
    ),
    (
        "HARDCODED_SECRET",
        re.compile(
            r"""(?i)(password|secret|token|api[_-]?key)\s*=\s*["'][^"']{4,}["']"""
        ),
        "Posible secreto hardcodeado.",
    ),
]

TODO_PATTERN = re.compile(
    r"\b(TODO|FIXME|XXX|HACK|BUG)\b", re.IGNORECASE
)

URL_PATTERN = re.compile(
    r"""(?:href|src|action)\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)

URL_FOR_PATTERN = re.compile(
    r"""url_for\s*\(\s*["']([^"']+)["']""",
    re.IGNORECASE,
)

ROUTE_DECORATOR_PATTERN = re.compile(
    r"""@\s*(?:router|app)\s*\.\s*(get|post|put|patch|delete|options|head)\s*\("""
)

SQL_PATTERN = re.compile(
    r"""(?i)\b(select|insert|update|delete|alter|create|drop)\b.*"""
)

IMPORT_PATTERN = re.compile(
    r"""^\s*(?:from\s+([A-Za-z0-9_\.]+)\s+import|import\s+([A-Za-z0-9_\.]+))"""
)


# ============================================================================
# DATOS
# ============================================================================

@dataclass
class Finding:
    severity: str
    category: str
    message: str
    file: str = ""
    line: int | None = None
    recommendation: str = ""

    def formatted(self) -> str:
        location = self.file
        if self.line:
            location += f":{self.line}"

        if location:
            location = f" [{location}]"

        rec = ""
        if self.recommendation:
            rec = f"\n      Recomendación: {self.recommendation}"

        return (
            f"[{self.severity}] {self.category}{location}\n"
            f"    {self.message}{rec}"
        )


@dataclass
class PythonFileInfo:
    path: Path
    relative: str
    lines: int = 0
    code_lines: int = 0
    blank_lines: int = 0
    comments: int = 0
    functions: int = 0
    classes: int = 0
    imports: list[str] = field(default_factory=list)
    imports_from: list[str] = field(default_factory=list)
    endpoints: list[dict[str, Any]] = field(default_factory=list)
    models: list[dict[str, Any]] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    routers: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    todos: list[tuple[int, str, str]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


@dataclass
class TemplateInfo:
    path: Path
    relative: str
    lines: int = 0
    links: list[tuple[str, int]] = field(default_factory=list)
    url_for: list[tuple[str, int]] = field(default_factory=list)
    includes: list[tuple[str, int]] = field(default_factory=list)
    extends: list[tuple[str, int]] = field(default_factory=list)
    forms: list[tuple[str, int]] = field(default_factory=list)
    scripts: list[tuple[str, int]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


# ============================================================================
# UTILIDADES
# ============================================================================

def print_section(title: str) -> None:
    print("\n" + "=" * 100)
    print(f" {title} ".center(100, "="))
    print("=" * 100)


def safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def is_ignored(path: Path) -> bool:
    return any(part in IGNORE_DIRS for part in path.parts)


def iter_files(root: Path):
    for current_root, dirs, files in os.walk(root):
        dirs[:] = [
            d for d in dirs
            if d not in IGNORE_DIRS
        ]

        current = Path(current_root)

        for filename in files:
            if filename in IGNORE_FILES:
                continue

            path = current / filename

            if any(part in IGNORE_DIRS for part in path.parts):
                continue

            yield path


def iter_python_files(root: Path):
    for path in iter_files(root):
        if path.suffix == ".py":
            yield path


def iter_templates(root: Path):
    for path in iter_files(root):
        if path.suffix.lower() in TEMPLATE_EXTENSIONS:
            yield path


def count_lines(text: str) -> tuple[int, int, int]:
    lines = text.splitlines()

    total = len(lines)
    blank = 0
    comments = 0
    code = 0

    for line in lines:
        stripped = line.strip()

        if not stripped:
            blank += 1
        elif stripped.startswith("#"):
            comments += 1
        else:
            code += 1

    return total, code, blank


def severity_score(findings: list[Finding]) -> int:
    weights = {
        "CRITICAL": 10,
        "HIGH": 7,
        "MEDIUM": 4,
        "LOW": 1,
        "INFO": 0,
    }

    return sum(weights.get(f.severity, 0) for f in findings)


# ============================================================================
# DETECCIÓN DE RAÍZ
# ============================================================================

def detect_project_root(explicit_root: str | None) -> Path:
    if explicit_root:
        return Path(explicit_root).resolve()

    script_dir = Path(__file__).resolve().parent

    candidates = [
        script_dir,
        script_dir.parent,
        Path.cwd().resolve(),
    ]

    for candidate in candidates:
        if (
            (candidate / "app").is_dir()
            or (candidate / "pyproject.toml").exists()
            or (candidate / "requirements.txt").exists()
        ):
            return candidate

    return Path.cwd().resolve()


# ============================================================================
# ESTRUCTURA
# ============================================================================

def audit_structure(root: Path) -> tuple[str, list[Finding]]:
    findings: list[Finding] = []

    files = list(iter_files(root))

    by_extension = Counter(
        p.suffix.lower() or "[sin extensión]"
        for p in files
    )

    directories = set()

    for path in files:
        try:
            directories.update(path.relative_to(root).parents)
        except Exception:
            pass

    python_files = [p for p in files if p.suffix == ".py"]
    template_files = [
        p for p in files
        if p.suffix.lower() in TEMPLATE_EXTENSIONS
    ]

    output = []

    output.append(f"Raíz detectada: {root}")
    output.append(f"Archivos analizados: {len(files)}")
    output.append(f"Directorios detectados: {len(directories)}")
    output.append(f"Archivos Python: {len(python_files)}")
    output.append(f"Templates: {len(template_files)}")
    output.append("")

    output.append("DISTRIBUCIÓN POR EXTENSIÓN:")
    for ext, count in by_extension.most_common():
        output.append(f"  {ext:<15} {count:>6}")

    output.append("")
    output.append("ÁRBOL DEL PROYECTO:")

    def tree(path: Path, prefix: str = "", depth: int = 0):
        if depth > 6:
            return

        try:
            children = sorted(
                [
                    p for p in path.iterdir()
                    if p.name not in IGNORE_DIRS
                    and p.name not in IGNORE_FILES
                ],
                key=lambda p: (p.is_file(), p.name.lower())
            )
        except Exception:
            return

        for child in children:
            marker = "📄" if child.is_file() else "📁"

            output.append(
                f"{prefix}{marker} {child.name}"
            )

            if child.is_dir():
                tree(
                    child,
                    prefix + "    ",
                    depth + 1,
                )

    tree(root)

    expected_dirs = [
        "app",
        "app/core",
        "app/models",
        "app/schemas",
        "app/routers",
        "app/services",
        "app/templates",
    ]

    for expected in expected_dirs:
        if not (root / expected).exists():
            findings.append(
                Finding(
                    "MEDIUM",
                    "ESTRUCTURA",
                    f"No existe el directorio esperado: {expected}",
                    recommendation=(
                        "Verificar si la ausencia es intencional o si "
                        "la arquitectura quedó incompleta."
                    ),
                )
            )

    return "\n".join(output), findings


# ============================================================================
# PYTHON / AST
# ============================================================================

def get_decorator_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id

    if isinstance(node, ast.Attribute):
        parent = get_decorator_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr

    if isinstance(node, ast.Call):
        return get_decorator_name(node.func)

    return ""


def get_string_value(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value

    return None


def extract_route_from_decorator(
    decorator: ast.AST,
) -> tuple[str, str] | None:
    if not isinstance(decorator, ast.Call):
        return None

    name = get_decorator_name(decorator.func)

    methods = {
        "get": "GET",
        "post": "POST",
        "put": "PUT",
        "patch": "PATCH",
        "delete": "DELETE",
        "options": "OPTIONS",
        "head": "HEAD",
    }

    method = methods.get(name.split(".")[-1])

    if not method:
        return None

    path = ""

    if decorator.args:
        value = get_string_value(decorator.args[0])
        if value:
            path = value

    return method, path


def extract_python_info(
    path: Path,
    root: Path,
) -> PythonFileInfo:
    relative = relative_path(path, root)
    text = safe_read(path)

    total, code, blank = count_lines(text)

    info = PythonFileInfo(
        path=path,
        relative=relative,
        lines=total,
        code_lines=code,
        blank_lines=blank,
    )

    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        info.findings.append(
            Finding(
                "HIGH",
                "PYTHON",
                f"Error de sintaxis: {exc.msg}",
                relative,
                exc.lineno,
                "Corregir el error de sintaxis antes de continuar.",
            )
        )
        return info

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            info.functions += 1

        elif isinstance(node, ast.AsyncFunctionDef):
            info.functions += 1

        elif isinstance(node, ast.ClassDef):
            info.classes += 1

        elif isinstance(node, ast.Import):
            for alias in node.names:
                info.imports.append(alias.name)

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                info.imports_from.append(node.module)

        elif isinstance(node, ast.Call):
            func_name = get_decorator_name(node.func)

            if func_name.endswith("Depends"):
                info.dependencies.append(
                    ast.unparse(node)
                    if hasattr(ast, "unparse")
                    else "Depends(...)"
                )

    # Decoradores / endpoints
    for node in ast.walk(tree):
        if not isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            continue

        for decorator in node.decorator_list:
            route = extract_route_from_decorator(decorator)

            if route:
                method, route_path = route

                info.endpoints.append(
                    {
                        "method": method,
                        "path": route_path,
                        "function": node.name,
                        "line": node.lineno,
                    }
                )

    # Modelos ORM aproximados
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue

        bases = []

        for base in node.bases:
            try:
                bases.append(
                    ast.unparse(base)
                    if hasattr(ast, "unparse")
                    else ""
                )
            except Exception:
                pass

        base_text = " ".join(bases)

        looks_like_model = (
            "Base" in base_text
            or "DeclarativeBase" in base_text
            or "SQLModel" in base_text
            or "__tablename__" in {
                getattr(n, "targets", [None])[0].id
                for n in node.body
                if isinstance(n, ast.Assign)
                and n.targets
                and isinstance(n.targets[0], ast.Name)
            }
        )

        if looks_like_model:
            model = {
                "name": node.name,
                "line": node.lineno,
                "bases": bases,
                "tablename": None,
                "columns": [],
                "relationships": [],
                "foreign_keys": [],
                "indexes": [],
                "constraints": [],
            }

            for child in node.body:
                if not isinstance(child, ast.Assign):
                    continue

                if len(child.targets) != 1:
                    continue

                target = child.targets[0]

                if not isinstance(target, ast.Name):
                    continue

                field_name = target.id

                try:
                    value = ast.unparse(child.value)
                except Exception:
                    value = ""

                if field_name == "__tablename__":
                    model["tablename"] = get_string_value(child.value)

                if (
                    "Column(" in value
                    or "mapped_column(" in value
                    or "Field(" in value
                ):
                    model["columns"].append(
                        {
                            "name": field_name,
                            "definition": value,
                            "line": child.lineno,
                        }
                    )

                    if "ForeignKey" in value:
                        model["foreign_keys"].append(
                            {
                                "name": field_name,
                                "definition": value,
                                "line": child.lineno,
                            }
                        )

                if "relationship(" in value:
                    model["relationships"].append(
                        {
                            "name": field_name,
                            "definition": value,
                            "line": child.lineno,
                        }
                    )

            info.models.append(model)

    # TODO/FIXME
    for lineno, line in enumerate(text.splitlines(), 1):
        match = TODO_PATTERN.search(line)

        if match:
            info.todos.append(
                (
                    lineno,
                    match.group(1).upper(),
                    line.strip(),
                )
            )

    # Problemas estáticos de Python
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()

        if stripped.startswith("print("):
            info.findings.append(
                Finding(
                    "LOW",
                    "CALIDAD",
                    "Uso de print() detectado.",
                    relative,
                    lineno,
                    "Usar logging estructurado en código de aplicación.",
                )
            )

        if re.search(r"\bexcept\s*:\s*$", stripped):
            info.findings.append(
                Finding(
                    "MEDIUM",
                    "CALIDAD",
                    "except: captura todas las excepciones sin especificarlas.",
                    relative,
                    lineno,
                    "Capturar excepciones específicas y registrar el error.",
                )
            )

        if re.search(
            r"\bexcept\s+Exception\s*:",
            stripped,
        ):
            info.findings.append(
                Finding(
                    "LOW",
                    "CALIDAD",
                    "except Exception detectado.",
                    relative,
                    lineno,
                    "Evitar capturas demasiado amplias cuando sea posible.",
                )
            )

        if "eval(" in stripped:
            info.findings.append(
                Finding(
                    "HIGH",
                    "SEGURIDAD",
                    "Uso de eval() detectado.",
                    relative,
                    lineno,
                    "Eliminar eval() o sustituirlo por parsing seguro.",
                )
            )

        if "exec(" in stripped:
            info.findings.append(
                Finding(
                    "HIGH",
                    "SEGURIDAD",
                    "Uso de exec() detectado.",
                    relative,
                    lineno,
                    "Eliminar exec() salvo que exista una justificación "
                    "muy controlada.",
                )
            )

        if re.search(
            r"\bsubprocess\.(run|Popen|call|check_output)\b",
            stripped,
        ):
            if "shell=True" in stripped:
                info.findings.append(
                    Finding(
                        "HIGH",
                        "SEGURIDAD",
                        "subprocess con shell=True.",
                        relative,
                        lineno,
                        "Evitar shell=True y pasar argumentos como lista.",
                    )
                )

    # Secretos
    for category, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            line = text[:match.start()].count("\n") + 1

            info.findings.append(
                Finding(
                    "CRITICAL",
                    "SEGURIDAD",
                    f"Posible secreto hardcodeado ({category}).",
                    relative,
                    line,
                    "Mover secretos a variables de entorno o gestor de secretos.",
                )
            )

    # Patrones de riesgo
    for category, pattern, message in RISK_PATTERNS:
        for match in pattern.finditer(text):
            line = text[:match.start()].count("\n") + 1

            severity = "HIGH"

            if category == "DEBUG_TRUE":
                severity = "MEDIUM"

            info.findings.append(
                Finding(
                    severity,
                    "SEGURIDAD",
                    message,
                    relative,
                    line,
                    "Revisar la configuración antes de desplegar a producción.",
                )
            )

    # SQL crudo
    for lineno, line in enumerate(text.splitlines(), 1):
        if (
            "text(" in line
            or "execute(" in line
            or "exec_driver_sql(" in line
        ) and SQL_PATTERN.search(line):
            info.findings.append(
                Finding(
                    "MEDIUM",
                    "BASE DE DATOS",
                    "Posible SQL escrito manualmente.",
                    relative,
                    lineno,
                    "Verificar parametrización y evitar concatenación de "
                    "valores proporcionados por usuarios.",
                )
            )

    return info


# ============================================================================
# TEMPLATES
# ============================================================================

def extract_template_info(
    path: Path,
    root: Path,
) -> TemplateInfo:
    relative = relative_path(path, root)
    text = safe_read(path)

    info = TemplateInfo(
        path=path,
        relative=relative,
        lines=len(text.splitlines()),
    )

    for lineno, line in enumerate(text.splitlines(), 1):
        for match in URL_PATTERN.finditer(line):
            info.links.append(
                (match.group(1), lineno)
            )

        for match in URL_FOR_PATTERN.finditer(line):
            info.url_for.append(
                (match.group(1), lineno)
            )

        include = re.search(
            r"""\{%\s*include\s+["']([^"']+)["']""",
            line,
        )

        if include:
            info.includes.append(
                (include.group(1), lineno)
            )

        extend = re.search(
            r"""\{%\s*extends\s+["']([^"']+)["']""",
            line,
        )

        if extend:
            info.extends.append(
                (extend.group(1), lineno)
            )

        if re.search(r"<form\b", line, re.IGNORECASE):
            info.forms.append(
                (line.strip(), lineno)
            )

        if re.search(r"<script\b", line, re.IGNORECASE):
            info.scripts.append(
                (line.strip(), lineno)
            )

    # Links relativos potencialmente problemáticos
    for link, lineno in info.links:
        if link.startswith(("http://", "https://", "#", "mailto:", "tel:")):
            continue

        if link.startswith("javascript:"):
            info.findings.append(
                Finding(
                    "MEDIUM",
                    "TEMPLATES",
                    "Uso de javascript: dentro de href.",
                    relative,
                    lineno,
                    "Preferir listeners JavaScript separados.",
                )
            )

    return info


# ============================================================================
# IMPORT GRAPH
# ============================================================================

def build_import_graph(
    python_infos: list[PythonFileInfo],
) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)

    known_modules = set()

    for info in python_infos:
        module = info.relative[:-3].replace(os.sep, ".")
        if module.endswith(".__init__"):
            module = module[:-9]

        known_modules.add(module)

    for info in python_infos:
        module = info.relative[:-3].replace(os.sep, ".")

        if module.endswith(".__init__"):
            module = module[:-9]

        imports = info.imports + info.imports_from

        for imported in imports:
            for known in known_modules:
                if (
                    imported == known
                    or imported.startswith(known + ".")
                    or known.startswith(imported + ".")
                ):
                    graph[module].add(known)

    return graph


def find_cycles(
    graph: dict[str, set[str]],
) -> list[list[str]]:
    cycles = []
    visited = set()
    stack = []
    active = set()

    def visit(node: str):
        if node in active:
            try:
                idx = stack.index(node)
                cycles.append(stack[idx:] + [node])
            except ValueError:
                pass
            return

        if node in visited:
            return

        visited.add(node)
        active.add(node)
        stack.append(node)

        for child in graph.get(node, set()):
            visit(child)

        stack.pop()
        active.remove(node)

    for node in graph:
        visit(node)

    # eliminar duplicados
    unique = []
    seen = set()

    for cycle in cycles:
        key = tuple(cycle)

        if key not in seen:
            seen.add(key)
            unique.append(cycle)

    return unique


# ============================================================================
# FASTAPI RUNTIME
# ============================================================================

def inspect_fastapi_runtime(
    root: Path,
) -> tuple[str, list[Finding]]:
    findings: list[Finding] = []

    output = []

    old_cwd = Path.cwd()

    try:
        os.chdir(root)

        root_str = str(root)

        if root_str not in sys.path:
            sys.path.insert(0, root_str)

        # Import deliberadamente tardío.
        from app.main import app  # type: ignore

        output.append("Importación dinámica de app.main: OK")
        output.append(
            f"Objeto FastAPI: {type(app).__name__}"
        )
        output.append(
            f"Total de rutas registradas: {len(app.routes)}"
        )
        output.append("")
        output.append("RUTAS REGISTRADAS:")

        for route in sorted(
            app.routes,
            key=lambda r: getattr(r, "path", ""),
        ):
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", None)
            name = getattr(route, "name", "")
            endpoint = getattr(route, "endpoint", None)

            methods_text = (
                ", ".join(sorted(methods))
                if methods
                else "INCLUDE/WS"
            )

            endpoint_name = (
                getattr(endpoint, "__name__", "")
                if endpoint
                else ""
            )

            output.append(
                f"  {methods_text:<20} "
                f"{path:<60} "
                f"{name:<30} "
                f"{endpoint_name}"
            )

        # Detección de rutas duplicadas
        route_map = defaultdict(list)

        for route in app.routes:
            path = getattr(route, "path", "")
            methods = tuple(
                sorted(
                    getattr(route, "methods", []) or []
                )
            )

            route_map[(methods, path)].append(route)

        duplicates = {
            key: value
            for key, value in route_map.items()
            if len(value) > 1
        }

        if duplicates:
            findings.append(
                Finding(
                    "HIGH",
                    "FASTAPI",
                    f"Se detectaron {len(duplicates)} rutas duplicadas.",
                    recommendation=(
                        "Eliminar endpoints duplicados o verificar "
                        "si existen montajes deliberados."
                    ),
                )
            )

        return "\n".join(output), findings

    except Exception as exc:
        output.append(
            "⚠️ No se pudo importar dinámicamente app.main."
        )
        output.append(
            f"Error: {type(exc).__name__}: {exc}"
        )
        output.append(
            "La auditoría estática continúa normalmente."
        )

        findings.append(
            Finding(
                "MEDIUM",
                "FASTAPI",
                f"No fue posible importar app.main: {exc}",
                recommendation=(
                    "Verificar dependencias, PYTHONPATH, configuración "
                    "y efectos secundarios durante el import."
                ),
            )
        )

        return "\n".join(output), findings

    finally:
        os.chdir(old_cwd)


# ============================================================================
# DEPENDENCIAS
# ============================================================================

def audit_dependencies(root: Path) -> tuple[str, list[Finding]]:
    findings = []
    output = []

    dependency_files = [
        "requirements.txt",
        "requirements-dev.txt",
        "pyproject.toml",
        "Pipfile",
        "Pipfile.lock",
        "poetry.lock",
        "uv.lock",
        "setup.py",
        "setup.cfg",
    ]

    found = []

    for name in dependency_files:
        path = root / name

        if path.exists():
            found.append(name)

    output.append(
        "Archivos de configuración/dependencias encontrados:"
    )

    if found:
        for item in found:
            output.append(f"  - {item}")
    else:
        output.append(
            "  ⚠️ No se detectó archivo estándar de dependencias."
        )

        findings.append(
            Finding(
                "MEDIUM",
                "DEPENDENCIAS",
                "No se detectó requirements.txt, pyproject.toml, "
                "Pipfile u otro archivo estándar.",
                recommendation=(
                    "Declarar las dependencias del proyecto de forma "
                    "reproducible."
                ),
            )
        )

    # requirements.txt
    req = root / "requirements.txt"

    if req.exists():
        lines = [
            line.strip()
            for line in safe_read(req).splitlines()
            if line.strip()
            and not line.strip().startswith("#")
        ]

        output.append("")
        output.append(
            f"Dependencias en requirements.txt: {len(lines)}"
        )

        for line in lines:
            output.append(f"  - {line}")

    return "\n".join(output), findings


# ============================================================================
# TEMPLATES Y NAVEGACIÓN
# ============================================================================

def audit_templates(
    root: Path,
    templates: list[TemplateInfo],
) -> tuple[str, list[Finding]]:
    findings = []
    output = []

    if not templates:
        findings.append(
            Finding(
                "MEDIUM",
                "TEMPLATES",
                "No se encontraron templates.",
                recommendation=(
                    "Verificar si la aplicación utiliza otro frontend "
                    "o si la ruta de templates es incorrecta."
                ),
            )
        )

        return (
            "No se encontraron templates.",
            findings,
        )

    all_links = defaultdict(list)
    all_url_for = defaultdict(list)

    for info in templates:
        for link, line in info.links:
            all_links[link].append(
                f"{info.relative}:{line}"
            )

        for endpoint, line in info.url_for:
            all_url_for[endpoint].append(
                f"{info.relative}:{line}"
            )

    output.append(
        f"Templates analizados: {len(templates)}"
    )

    output.append("")
    output.append(
        f"URLs/enlaces detectados: {len(all_links)}"
    )

    output.append(
        f"url_for detectados: {len(all_url_for)}"
    )

    output.append("")
    output.append("HERENCIA DE TEMPLATES:")

    for info in templates:
        if info.extends:
            for template, line in info.extends:
                output.append(
                    f"  {info.relative}:{line} -> extends {template}"
                )

    output.append("")
    output.append("INCLUDES:")

    for info in templates:
        for template, line in info.includes:
            output.append(
                f"  {info.relative}:{line} -> include {template}"
            )

    output.append("")
    output.append("URL_FOR:")

    for endpoint, locations in sorted(all_url_for.items()):
        output.append(
            f"  {endpoint}"
        )

        for location in locations[:10]:
            output.append(
                f"      └─ {location}"
            )

    output.append("")
    output.append("FORMULARIOS:")

    for info in templates:
        for form, line in info.forms:
            output.append(
                f"  {info.relative}:{line} -> {form[:120]}"
            )

    # Duplicidad de enlaces
    duplicates = {
        link: locations
        for link, locations in all_links.items()
        if len(locations) > 1
    }

    output.append("")
    output.append(
        f"Enlaces repetidos en múltiples templates: "
        f"{len(duplicates)}"
    )

    for link, locations in sorted(
        duplicates.items(),
        key=lambda item: len(item[1]),
        reverse=True,
    )[:30]:
        output.append(
            f"  {link} ({len(locations)} apariciones)"
        )

    return "\n".join(output), findings


# ============================================================================
# AUDITORÍA DE MODELOS
# ============================================================================

def audit_models(
    python_infos: list[PythonFileInfo],
) -> tuple[str, list[Finding]]:
    findings = []
    output = []

    models = []

    for info in python_infos:
        for model in info.models:
            model_copy = dict(model)
            model_copy["file"] = info.relative
            models.append(model_copy)

    output.append(
        f"Modelos ORM detectados: {len(models)}"
    )

    if not models:
        findings.append(
            Finding(
                "HIGH",
                "ORM",
                "No se detectaron modelos ORM mediante análisis AST.",
                recommendation=(
                    "Verificar si los modelos utilizan SQLAlchemy, "
                    "SQLModel, otro ORM o generación dinámica."
                ),
            )
        )

        return "\n".join(output), findings

    output.append("")

    table_names = Counter()

    for model in models:
        table = model.get("tablename")

        if table:
            table_names[table] += 1

        output.append(
            f"MODEL: {model['name']}"
        )
        output.append(
            f"  Archivo: {model['file']}"
        )
        output.append(
            f"  Línea: {model['line']}"
        )
        output.append(
            f"  Tabla: {table or '[no detectada]'}"
        )
        output.append(
            f"  Columnas: {len(model['columns'])}"
        )
        output.append(
            f"  Foreign Keys: {len(model['foreign_keys'])}"
        )
        output.append(
            f"  Relaciones: {len(model['relationships'])}"
        )

        for column in model["columns"]:
            output.append(
                f"      COLUMN {column['name']} "
                f"-> {column['definition']}"
            )

        for fk in model["foreign_keys"]:
            output.append(
                f"      FK {fk['name']} "
                f"-> {fk['definition']}"
            )

        for relation in model["relationships"]:
            output.append(
                f"      REL {relation['name']} "
                f"-> {relation['definition']}"
            )

        output.append("")

    duplicated_tables = {
        table: count
        for table, count in table_names.items()
        if count > 1
    }

    if duplicated_tables:
        findings.append(
            Finding(
                "HIGH",
                "ORM",
                "Se detectaron nombres de tabla duplicados: "
                + ", ".join(duplicated_tables),
                recommendation=(
                    "Verificar si existen modelos ORM duplicados "
                    "representando la misma entidad."
                ),
            )
        )

    # Modelos con muchas columnas
    for model in models:
        if len(model["columns"]) > 35:
            findings.append(
                Finding(
                    "MEDIUM",
                    "ORM",
                    f"Modelo {model['name']} tiene "
                    f"{len(model['columns'])} columnas.",
                    model["file"],
                    model["line"],
                    "Evaluar si el modelo concentra demasiadas "
                    "responsabilidades.",
                )
            )

    return "\n".join(output), findings


# ============================================================================
# ENDPOINTS ESTÁTICOS
# ============================================================================

def audit_static_routes(
    python_infos: list[PythonFileInfo],
) -> tuple[str, list[Finding]]:
    findings = []
    output = []

    routes = []

    for info in python_infos:
        for route in info.endpoints:
            route_copy = dict(route)
            route_copy["file"] = info.relative
            routes.append(route_copy)

    output.append(
        f"Endpoints detectados estáticamente: {len(routes)}"
    )

    methods = Counter(
        route["method"]
        for route in routes
    )

    output.append("")
    output.append("DISTRIBUCIÓN POR MÉTODO:")

    for method, count in sorted(methods.items()):
        output.append(
            f"  {method:<10} {count}"
        )

    output.append("")
    output.append("ENDPOINTS:")

    route_keys = Counter(
        (
            route["method"],
            route["path"],
        )
        for route in routes
    )

    for route in sorted(
        routes,
        key=lambda x: (x["path"], x["method"]),
    ):
        output.append(
            f"  {route['method']:<8} "
            f"{route['path']:<50} "
            f"{route['function']:<30} "
            f"{route['file']}:{route['line']}"
        )

    duplicates = {
        key: count
        for key, count in route_keys.items()
        if count > 1
    }

    if duplicates:
        findings.append(
            Finding(
                "HIGH",
                "FASTAPI",
                "Se detectaron posibles endpoints duplicados "
                f"estáticamente: {len(duplicates)}.",
                recommendation=(
                    "Revisar routers incluidos múltiples veces "
                    "o rutas declaradas duplicadamente."
                ),
            )
        )

    return "\n".join(output), findings


# ============================================================================
# CALIDAD Y MÉTRICAS
# ============================================================================

def audit_code_quality(
    python_infos: list[PythonFileInfo],
) -> tuple[str, list[Finding]]:
    findings = []
    output = []

    total_lines = sum(i.lines for i in python_infos)
    total_code = sum(i.code_lines for i in python_infos)
    total_blank = sum(i.blank_lines for i in python_infos)
    total_functions = sum(i.functions for i in python_infos)
    total_classes = sum(i.classes for i in python_infos)

    output.append(
        f"Archivos Python: {len(python_infos)}"
    )
    output.append(
        f"Líneas totales: {total_lines}"
    )
    output.append(
        f"Líneas de código aproximadas: {total_code}"
    )
    output.append(
        f"Líneas vacías: {total_blank}"
    )
    output.append(
        f"Funciones/métodos: {total_functions}"
    )
    output.append(
        f"Clases: {total_classes}"
    )

    if total_lines:
        comment_ratio = 0

        # Solo aproximación: los comentarios de AST no se cuentan aquí.
        comment_ratio = total_blank / total_lines

        output.append(
            f"Proporción de líneas vacías: "
            f"{comment_ratio:.1%}"
        )

    output.append("")
    output.append("ARCHIVOS MÁS GRANDES:")

    largest = sorted(
        python_infos,
        key=lambda x: x.lines,
        reverse=True,
    )[:20]

    for info in largest:
        output.append(
            f"  {info.lines:>6} líneas "
            f"{info.relative}"
        )

        if info.lines > 800:
            findings.append(
                Finding(
                    "MEDIUM",
                    "CALIDAD",
                    f"Archivo excesivamente grande: "
                    f"{info.lines} líneas.",
                    info.relative,
                    recommendation=(
                        "Evaluar separación por responsabilidad "
                        "y extracción de servicios/componentes."
                    ),
                )
            )

    output.append("")
    output.append("ARCHIVOS CON MAYOR NÚMERO DE FUNCIONES:")

    for info in sorted(
        python_infos,
        key=lambda x: x.functions,
        reverse=True,
    )[:20]:
        if info.functions:
            output.append(
                f"  {info.functions:>5} funciones "
                f"{info.relative}"
            )

    todos = []

    for info in python_infos:
        todos.extend(
            (info.relative, *item)
            for item in info.todos
        )

    output.append("")
    output.append(
        f"TODO/FIXME/BUG/HACK detectados: {len(todos)}"
    )

    for file, line, kind, text in todos[:100]:
        output.append(
            f"  {file}:{line} [{kind}] {text}"
        )

    return "\n".join(output), findings


# ============================================================================
# SEGURIDAD
# ============================================================================

def audit_security(
    python_infos: list[PythonFileInfo],
    root: Path,
) -> tuple[str, list[Finding]]:
    findings = []
    output = []

    for info in python_infos:
        findings.extend(info.findings)

    output.append(
        f"Hallazgos de seguridad/calidad detectados: "
        f"{len(findings)}"
    )

    severities = Counter(
        f.severity
        for f in findings
    )

    output.append("")
    output.append("SEVERIDAD:")

    for severity in [
        "CRITICAL",
        "HIGH",
        "MEDIUM",
        "LOW",
        "INFO",
    ]:
        output.append(
            f"  {severity:<10} {severities.get(severity, 0)}"
        )

    output.append("")
    output.append("HALLAZGOS:")

    for finding in findings:
        output.append(
            finding.formatted()
        )

    return "\n".join(output), findings


# ============================================================================
# ARQUITECTURA
# ============================================================================

def audit_architecture(
    root: Path,
    python_infos: list[PythonFileInfo],
) -> tuple[str, list[Finding]]:
    findings = []
    output = []

    folders = {
        "core": (root / "app/core").exists(),
        "models": (root / "app/models").exists(),
        "schemas": (root / "app/schemas").exists(),
        "routers": (root / "app/routers").exists(),
        "services": (root / "app/services").exists(),
        "templates": (root / "app/templates").exists(),
    }

    output.append("CAPAS DETECTADAS:")

    for name, exists in folders.items():
        output.append(
            f"  {'OK' if exists else 'MISSING':<8} {name}"
        )

    # Detectar imports desde routers hacia models.
    router_infos = [
        info
        for info in python_infos
        if "/routers/" in info.relative.replace("\\", "/")
    ]

    service_infos = [
        info
        for info in python_infos
        if "/services/" in info.relative.replace("\\", "/")
    ]

    model_infos = [
        info
        for info in python_infos
        if "/models/" in info.relative.replace("\\", "/")
    ]

    direct_model_imports = 0

    for info in router_infos:
        imports = info.imports + info.imports_from

        if any(
            ".models" in imported
            or imported.startswith("app.models")
            for imported in imports
        ):
            direct_model_imports += 1

    if direct_model_imports:
        findings.append(
            Finding(
                "MEDIUM",
                "ARQUITECTURA",
                f"{direct_model_imports} routers importan modelos directamente.",
                recommendation=(
                    "Preferir que el router delegue la lógica de negocio "
                    "al service layer cuando corresponda."
                ),
            )
        )

    output.append("")
    output.append(
        f"Routers detectados: {len(router_infos)}"
    )
    output.append(
        f"Services detectados: {len(service_infos)}"
    )
    output.append(
        f"Archivos bajo models/: {len(model_infos)}"
    )

    # Nombres potencialmente duplicados por dominio.
    basename_map = defaultdict(list)

    for info in python_infos:
        basename_map[
            Path(info.relative).stem.lower()
        ].append(info.relative)

    duplicates = {
        name: paths
        for name, paths in basename_map.items()
        if len(paths) > 1
    }

    output.append("")
    output.append(
        f"Nombres de archivo repetidos: {len(duplicates)}"
    )

    for name, paths in sorted(duplicates.items()):
        output.append(
            f"  {name}:"
        )

        for path in paths:
            output.append(
                f"      - {path}"
            )

    if duplicates:
        findings.append(
            Finding(
                "LOW",
                "ARQUITECTURA",
                "Existen archivos con el mismo nombre en diferentes "
                "directorios.",
                recommendation=(
                    "Revisar si representan dominios distintos o si "
                    "existe duplicación conceptual."
                ),
            )
        )

    return "\n".join(output), findings


# ============================================================================
# IMPORTACIONES Y CICLOS
# ============================================================================

def audit_imports(
    python_infos: list[PythonFileInfo],
) -> tuple[str, list[Finding]]:
    findings = []
    output = []

    graph = build_import_graph(python_infos)
    cycles = find_cycles(graph)

    internal_edges = sum(
        len(children)
        for children in graph.values()
    )

    output.append(
        f"Módulos internos con imports: {len(graph)}"
    )
    output.append(
        f"Dependencias internas detectadas: {internal_edges}"
    )
    output.append(
        f"Ciclos de importación potenciales: {len(cycles)}"
    )

    if cycles:
        output.append("")
        output.append("CICLOS:")

        for cycle in cycles[:50]:
            output.append(
                "  " + " -> ".join(cycle)
            )

            findings.append(
                Finding(
                    "HIGH",
                    "ARQUITECTURA",
                    "Posible dependencia circular: "
                    + " -> ".join(cycle),
                    recommendation=(
                        "Extraer contratos/interfaces compartidos, "
                        "reorganizar módulos o invertir la dependencia."
                    ),
                )
            )

    return "\n".join(output), findings


# ============================================================================
# CONFIGURACIÓN
# ============================================================================

def audit_configuration(root: Path) -> tuple[str, list[Finding]]:
    findings = []
    output = []

    config_files = [
        ".env",
        ".env.example",
        ".env.local",
        ".env.production",
        "alembic.ini",
        "docker-compose.yml",
        "docker-compose.yaml",
        "Dockerfile",
        "Makefile",
        "pyproject.toml",
    ]

    output.append("CONFIGURACIÓN DETECTADA:")

    for filename in config_files:
        path = root / filename

        if path.exists():
            output.append(
                f"  OK  {filename}"
            )

    env = root / ".env"

    if env.exists():
        findings.append(
            Finding(
                "HIGH",
                "CONFIGURACIÓN",
                "Existe un archivo .env dentro del proyecto.",
                str(env.relative_to(root)),
                recommendation=(
                    "Asegurar que .env esté excluido del control de "
                    "versiones y que los secretos no se distribuyan."
                ),
            )
        )

    gitignore = root / ".gitignore"

    if gitignore.exists():
        content = safe_read(gitignore)

        if ".env" not in content:
            findings.append(
                Finding(
                    "HIGH",
                    "CONFIGURACIÓN",
                    ".env no aparece explícitamente en .gitignore.",
                    ".gitignore",
                    recommendation=(
                        "Agregar .env y otros archivos sensibles al "
                        "control de exclusiones."
                    ),
                )
            )

    else:
        findings.append(
            Finding(
                "MEDIUM",
                "CONFIGURACIÓN",
                "No existe .gitignore.",
                recommendation=(
                    "Crear .gitignore para evitar subir secretos, "
                    "entornos virtuales, caches y artefactos."
                ),
            )
        )

    return "\n".join(output), findings


# ============================================================================
# RESUMEN EJECUTIVO
# ============================================================================

def build_executive_summary(
    findings: list[Finding],
    python_infos: list[PythonFileInfo],
    models_count: int,
    routes_count: int,
    templates_count: int,
) -> str:
    counts = Counter(
        f.severity
        for f in findings
    )

    score = severity_score(findings)

    if counts["CRITICAL"]:
        status = "CRÍTICO"
    elif counts["HIGH"] >= 5:
        status = "ALTO RIESGO"
    elif counts["HIGH"]:
        status = "REQUIERE ATENCIÓN"
    elif counts["MEDIUM"]:
        status = "RIESGO MODERADO"
    else:
        status = "SIN HALLAZGOS CRÍTICOS"

    lines = []

    lines.append(
        f"ESTADO GENERAL: {status}"
    )

    lines.append(
        f"Índice heurístico de riesgo: {score}"
    )

    lines.append("")
    lines.append("MÉTRICAS PRINCIPALES:")
    lines.append(
        f"  Python:       {len(python_infos)} archivos"
    )
    lines.append(
        f"  Modelos ORM:  {models_count}"
    )
    lines.append(
        f"  Endpoints:    {routes_count}"
    )
    lines.append(
        f"  Templates:    {templates_count}"
    )

    lines.append("")
    lines.append("HALLAZGOS:")
    lines.append(
        f"  CRITICAL: {counts['CRITICAL']}"
    )
    lines.append(
        f"  HIGH:     {counts['HIGH']}"
    )
    lines.append(
        f"  MEDIUM:   {counts['MEDIUM']}"
    )
    lines.append(
        f"  LOW:      {counts['LOW']}"
    )

    if counts["CRITICAL"]:
        lines.append("")
        lines.append(
            "Prioridad inmediata: resolver los hallazgos CRITICAL."
        )

    elif counts["HIGH"]:
        lines.append("")
        lines.append(
            "Prioridad: resolver los hallazgos HIGH antes de "
            "considerar el sistema listo para producción."
        )

    return "\n".join(lines)


# ============================================================================
# REPORTE JSON
# ============================================================================

def serialize_findings(
    findings: list[Finding],
) -> list[dict[str, Any]]:
    return [
        {
            "severity": f.severity,
            "category": f.category,
            "message": f.message,
            "file": f.file,
            "line": f.line,
            "recommendation": f.recommendation,
        }
        for f in findings
    ]


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Auditoría estática completa de proyecto FastAPI."
    )

    parser.add_argument(
        "--root",
        help="Raíz explícita del proyecto.",
    )

    parser.add_argument(
        "--output",
        default="auditoria_proyecto.txt",
        help="Archivo TXT de salida.",
    )

    parser.add_argument(
        "--json",
        default="auditoria_proyecto.json",
        help="Archivo JSON de salida.",
    )

    args = parser.parse_args()

    root = detect_project_root(args.root)

    if not root.exists():
        print(
            f"ERROR: La raíz del proyecto no existe: {root}"
        )
        sys.exit(1)

    all_findings: list[Finding] = []

    print_section(
        "AUDITORÍA COMPLETA DEL PROYECTO"
    )

    print(f"Raíz: {root}")

    # ------------------------------------------------------------------
    # 1. ESTRUCTURA
    # ------------------------------------------------------------------

    print_section(
        "1. ESTRUCTURA DEL PROYECTO"
    )

    structure_text, findings = audit_structure(root)

    print(structure_text)
    all_findings.extend(findings)

    # ------------------------------------------------------------------
    # 2. PYTHON
    # ------------------------------------------------------------------

    print_section(
        "2. ANÁLISIS ESTÁTICO DE PYTHON"
    )

    python_infos = [
        extract_python_info(path, root)
        for path in iter_python_files(root)
    ]

    print(
        f"Archivos Python analizados: "
        f"{len(python_infos)}"
    )

    all_findings.extend(
        finding
        for info in python_infos
        for finding in info.findings
    )

    # ------------------------------------------------------------------
    # 3. MODELOS
    # ------------------------------------------------------------------

    print_section(
        "3. MAPA ORM / BASE DE DATOS"
    )

    models_text, findings = audit_models(
        python_infos
    )

    print(models_text)
    all_findings.extend(findings)

    models_count = sum(
        len(info.models)
        for info in python_infos
    )

    # ------------------------------------------------------------------
    # 4. FASTAPI ESTÁTICO
    # ------------------------------------------------------------------

    print_section(
        "4. ENDPOINTS FASTAPI - ANÁLISIS ESTÁTICO"
    )

    routes_text, findings = audit_static_routes(
        python_infos
    )

    print(routes_text)
    all_findings.extend(findings)

    routes_count = sum(
        len(info.endpoints)
        for info in python_infos
    )

    # ------------------------------------------------------------------
    # 5. FASTAPI RUNTIME
    # ------------------------------------------------------------------

    print_section(
        "5. FASTAPI - INSPECCIÓN DINÁMICA"
    )

    runtime_text, findings = inspect_fastapi_runtime(
        root
    )

    print(runtime_text)
    all_findings.extend(findings)

    # ------------------------------------------------------------------
    # 6. TEMPLATES
    # ------------------------------------------------------------------

    print_section(
        "6. TEMPLATES / UI / NAVEGACIÓN"
    )

    template_infos = [
        extract_template_info(path, root)
        for path in iter_templates(root)
    ]

    templates_text, findings = audit_templates(
        root,
        template_infos,
    )

    print(templates_text)
    all_findings.extend(findings)

    templates_count = len(template_infos)

    # ------------------------------------------------------------------
    # 7. CALIDAD
    # ------------------------------------------------------------------

    print_section(
        "7. CALIDAD Y MÉTRICAS DE CÓDIGO"
    )

    quality_text, findings = audit_code_quality(
        python_infos
    )

    print(quality_text)
    all_findings.extend(findings)

    # ------------------------------------------------------------------
    # 8. SEGURIDAD
    # ------------------------------------------------------------------

    print_section(
        "8. SEGURIDAD"
    )

    security_text, findings = audit_security(
        python_infos,
        root,
    )

    print(security_text)

    # Evitar duplicar findings porque audit_security
    # ya recoge los findings de Python.
    # Solo agregamos los que sean propios.
    security_findings_set = {
        (
            f.severity,
            f.category,
            f.message,
            f.file,
            f.line,
        )
        for f in all_findings
    }

    for finding in findings:
        key = (
            finding.severity,
            finding.category,
            finding.message,
            finding.file,
            finding.line,
        )

        if key not in security_findings_set:
            all_findings.append(finding)

    # ------------------------------------------------------------------
    # 9. ARQUITECTURA
    # ------------------------------------------------------------------

    print_section(
        "9. ARQUITECTURA Y SEPARACIÓN DE RESPONSABILIDADES"
    )

    architecture_text, findings = audit_architecture(
        root,
        python_infos,
    )

    print(architecture_text)
    all_findings.extend(findings)

    # ------------------------------------------------------------------
    # 10. IMPORTS
    # ------------------------------------------------------------------

    print_section(
        "10. DEPENDENCIAS INTERNAS / IMPORTS"
    )

    imports_text, findings = audit_imports(
        python_infos
    )

    print(imports_text)
    all_findings.extend(findings)

    # ------------------------------------------------------------------
    # 11. DEPENDENCIAS
    # ------------------------------------------------------------------

    print_section(
        "11. DEPENDENCIAS DEL PROYECTO"
    )

    dependencies_text, findings = audit_dependencies(
        root
    )

    print(dependencies_text)
    all_findings.extend(findings)

    # ------------------------------------------------------------------
    # 12. CONFIGURACIÓN
    # ------------------------------------------------------------------

    print_section(
        "12. CONFIGURACIÓN / DESPLIEGUE"
    )

    configuration_text, findings = audit_configuration(
        root
    )

    print(configuration_text)
    all_findings.extend(findings)

    # ------------------------------------------------------------------
    # DEDUPLICACIÓN DE HALLAZGOS
    # ------------------------------------------------------------------

    unique_findings = []

    seen = set()

    for finding in all_findings:
        key = (
            finding.severity,
            finding.category,
            finding.message,
            finding.file,
            finding.line,
        )

        if key not in seen:
            seen.add(key)
            unique_findings.append(finding)

    all_findings = unique_findings

    # ------------------------------------------------------------------
    # RESUMEN
    # ------------------------------------------------------------------

    print_section(
        "13. RESUMEN EJECUTIVO"
    )

    summary = build_executive_summary(
        all_findings,
        python_infos,
        models_count,
        routes_count,
        templates_count,
    )

    print(summary)

    # ------------------------------------------------------------------
    # TOP PROBLEMAS
    # ------------------------------------------------------------------

    print_section(
        "14. TOP HALLAZGOS POR PRIORIDAD"
    )

    priority_order = {
        "CRITICAL": 0,
        "HIGH": 1,
        "MEDIUM": 2,
        "LOW": 3,
        "INFO": 4,
    }

    sorted_findings = sorted(
        all_findings,
        key=lambda f: (
            priority_order.get(f.severity, 99),
            f.category,
            f.file,
            f.line or 0,
        ),
    )

    for index, finding in enumerate(
        sorted_findings[:50],
        1,
    ):
        print(
            f"{index:>3}. "
            f"[{finding.severity}] "
            f"{finding.category} - "
            f"{finding.message}"
        )

        if finding.file:
            print(
                f"     {finding.file}"
                + (
                    f":{finding.line}"
                    if finding.line
                    else ""
                )
            )

    # ------------------------------------------------------------------
    # REPORTE TXT
    # ------------------------------------------------------------------

    report_path = (
        root / args.output
        if not Path(args.output).is_absolute()
        else Path(args.output)
    )

    json_path = (
        root / args.json
        if not Path(args.json).is_absolute()
        else Path(args.json)
    )

    report_sections = []

    report_sections.append(
        "=" * 100
        + "\n"
        + "AUDITORÍA COMPLETA DEL PROYECTO".center(100)
        + "\n"
        + "=" * 100
    )

    report_sections.append(
        "\nROOT\n"
        + str(root)
    )

    report_sections.append(
        "\n\n1. ESTRUCTURA\n"
        + structure_text
    )

    report_sections.append(
        "\n\n2. ANÁLISIS PYTHON\n"
        + "\n".join(
            [
                f"{info.relative}: "
                f"{info.lines} líneas, "
                f"{info.functions} funciones, "
                f"{info.classes} clases"
                for info in python_infos
            ]
        )
    )

    report_sections.append(
        "\n\n3. ORM / MODELOS\n"
        + models_text
    )

    report_sections.append(
        "\n\n4. FASTAPI ESTÁTICO\n"
        + routes_text
    )

    report_sections.append(
        "\n\n5. FASTAPI DINÁMICO\n"
        + runtime_text
    )

    report_sections.append(
        "\n\n6. TEMPLATES\n"
        + templates_text
    )

    report_sections.append(
        "\n\n7. CALIDAD\n"
        + quality_text
    )

    report_sections.append(
        "\n\n8. SEGURIDAD\n"
        + security_text
    )

    report_sections.append(
        "\n\n9. ARQUITECTURA\n"
        + architecture_text
    )

    report_sections.append(
        "\n\n10. IMPORTS\n"
        + imports_text
    )

    report_sections.append(
        "\n\n11. DEPENDENCIAS\n"
        + dependencies_text
    )

    report_sections.append(
        "\n\n12. CONFIGURACIÓN\n"
        + configuration_text
    )

    report_sections.append(
        "\n\n13. RESUMEN EJECUTIVO\n"
        + summary
    )

    report_sections.append(
        "\n\n14. HALLAZGOS COMPLETOS\n"
        + "\n\n".join(
            finding.formatted()
            for finding in sorted_findings
        )
    )

    report_text = "\n".join(report_sections)

    report_path.write_text(
        report_text,
        encoding="utf-8",
    )

    # ------------------------------------------------------------------
    # JSON
    # ------------------------------------------------------------------

    json_data = {
        "project_root": str(root),
        "metrics": {
            "python_files": len(python_infos),
            "models": models_count,
            "static_routes": routes_count,
            "templates": templates_count,
        },
        "findings": serialize_findings(
            all_findings
        ),
        "summary": {
            "critical": sum(
                1
                for f in all_findings
                if f.severity == "CRITICAL"
            ),
            "high": sum(
                1
                for f in all_findings
                if f.severity == "HIGH"
            ),
            "medium": sum(
                1
                for f in all_findings
                if f.severity == "MEDIUM"
            ),
            "low": sum(
                1
                for f in all_findings
                if f.severity == "LOW"
            ),
            "risk_score": severity_score(
                all_findings
            ),
        },
    }

    json_path.write_text(
        json.dumps(
            json_data,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print_section(
        "ARCHIVOS GENERADOS"
    )

    print(
        f"Reporte TXT : {report_path}"
    )

    print(
        f"Reporte JSON: {json_path}"
    )

    print("")
    print(
        "Auditoría finalizada."
    )


if __name__ == "__main__":
    main()
