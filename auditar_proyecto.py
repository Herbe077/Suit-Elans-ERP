#!/usr/bin/env python3
"""
Auditoría estática de Suit Elans ERP.

Uso:
    python auditar_proyecto.py

Genera:
    informe.txt

IMPORTANTE:
- Este script NO modifica archivos del proyecto.
- No ejecuta migraciones.
- No modifica la base de datos.
- No ejecuta tests.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime


# ============================================================
# CONFIGURACIÓN
# ============================================================

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "informe.txt"

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".idea",
    ".vscode",
}

INCLUDED_EXTENSIONS = {
    ".py",
    ".html",
    ".sql",
    ".txt",
    ".md",
    ".yml",
    ".yaml",
    ".ini",
}

MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB


# ============================================================
# UTILIDADES
# ============================================================

def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def should_skip(path: Path) -> bool:
    return any(part in EXCLUDED_DIRS for part in path.parts)


def all_files():
    for path in ROOT.rglob("*"):
        if path.is_file() and not should_skip(path):
            yield path


def project_files():
    for path in all_files():
        if path.suffix.lower() in INCLUDED_EXTENSIONS:
            yield path


def python_files():
    for path in all_files():
        if path.suffix == ".py":
            yield path


def safe_read(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_FILE_SIZE:
            return f"[ARCHIVO OMITIDO: supera {MAX_FILE_SIZE // 1024 // 1024} MB]"
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"[ERROR LEYENDO ARCHIVO: {exc}]"


def line_count(path: Path) -> int:
    try:
        return len(safe_read(path).splitlines())
    except Exception:
        return 0


def add_section(report: list[str], title: str):
    report.append("")
    report.append("=" * 80)
    report.append(title)
    report.append("=" * 80)
    report.append("")


# ============================================================
# ÁRBOL DEL PROYECTO
# ============================================================

def build_tree(report: list[str]):
    add_section(report, "1. ÁRBOL DEL PROYECTO")

    def walk(directory: Path, prefix=""):
        try:
            entries = sorted(
                [
                    p for p in directory.iterdir()
                    if not should_skip(p)
                ],
                key=lambda p: (p.is_file(), p.name.lower())
            )
        except Exception:
            return

        for index, path in enumerate(entries):
            last = index == len(entries) - 1
            connector = "└── " if last else "├── "

            report.append(prefix + connector + path.name)

            if path.is_dir():
                extension = "    " if last else "│   "
                walk(path, prefix + extension)

    report.append(ROOT.name + "/")
    walk(ROOT)


# ============================================================
# ESTADÍSTICAS
# ============================================================

def project_statistics(report: list[str]):
    add_section(report, "2. ESTADÍSTICAS DEL PROYECTO")

    files = list(all_files())
    py = list(python_files())

    by_ext = defaultdict(int)
    for f in files:
        by_ext[f.suffix.lower() or "[sin extensión]"] += 1

    total_lines = sum(line_count(f) for f in py)

    report.append(f"Directorio raíz: {ROOT}")
    report.append(f"Archivos totales: {len(files)}")
    report.append(f"Archivos Python: {len(py)}")
    report.append(f"Líneas Python aproximadas: {total_lines}")
    report.append("")
    report.append("Archivos por extensión:")

    for ext, count in sorted(by_ext.items()):
        report.append(f"  {ext}: {count}")


# ============================================================
# ANÁLISIS PYTHON / AST
# ============================================================

def analyze_python_file(path: Path):
    source = safe_read(path)

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {
            "classes": [],
            "functions": [],
            "imports": [],
            "models": [],
            "routes": [],
            "syntax_error": str(exc),
        }

    classes = []
    functions = []
    imports = []
    models = []
    routes = []

    for node in ast.walk(tree):

        if isinstance(node, ast.ClassDef):
            classes.append(node.name)

            bases = []
            for base in node.bases:
                try:
                    bases.append(ast.unparse(base))
                except Exception:
                    bases.append("<?>")

            bases_text = ", ".join(bases)

            # Detectar modelos SQLAlchemy de manera heurística.
            if (
                "Base" in bases_text
                or "DeclarativeBase" in bases_text
                or "__tablename__" in source
            ):
                models.append(
                    f"{node.name}"
                    + (f" ({bases_text})" if bases_text else "")
                )

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)

        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.append(module)

        # Detectar decoradores de FastAPI.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                try:
                    text = ast.unparse(decorator)
                except Exception:
                    text = ""

                if any(
                    x in text
                    for x in [
                        ".get(",
                        ".post(",
                        ".put(",
                        ".patch(",
                        ".delete(",
                        ".api_route(",
                    ]
                ):
                    routes.append(
                        f"{node.name} -> {text}"
                    )

    return {
        "classes": classes,
        "functions": functions,
        "imports": imports,
        "models": models,
        "routes": routes,
        "syntax_error": None,
    }


def python_analysis(report: list[str]):
    add_section(report, "3. ANÁLISIS DE ARCHIVOS PYTHON")

    for path in sorted(python_files()):
        data = analyze_python_file(path)

        report.append(f"\n--- {rel(path)} ---")
        report.append(f"Líneas: {line_count(path)}")

        if data["syntax_error"]:
            report.append(
                f"ERROR DE SINTAXIS: {data['syntax_error']}"
            )
            continue

        if data["models"]:
            report.append("Modelos detectados:")
            for item in data["models"]:
                report.append(f"  - {item}")

        if data["routes"]:
            report.append("Rutas FastAPI detectadas:")
            for item in data["routes"]:
                report.append(f"  - {item}")

        if data["classes"]:
            report.append("Clases:")
            for item in data["classes"]:
                report.append(f"  - {item}")

        if data["functions"]:
            report.append(
                f"Funciones/métodos detectados: {len(data['functions'])}"
            )


# ============================================================
# ANÁLISIS DE MODELOS SQLALCHEMY
# ============================================================

def sqlalchemy_analysis(report: list[str]):
    add_section(report, "4. MODELOS SQLALCHEMY Y TABLAS")

    found = False

    for path in sorted(python_files()):
        source = safe_read(path)

        if (
            "Base" not in source
            and "Column(" not in source
            and "__tablename__" not in source
        ):
            continue

        tables = re.findall(
            r'__tablename__\s*=\s*["\']([^"\']+)["\']',
            source
        )

        foreign_keys = re.findall(
            r'ForeignKey\(\s*["\']([^"\']+)["\']',
            source
        )

        if tables or foreign_keys:
            found = True
            report.append(f"\n--- {rel(path)} ---")

            if tables:
                report.append("Tablas:")
                for table in tables:
                    report.append(f"  - {table}")

            if foreign_keys:
                report.append("Foreign Keys:")
                for fk in foreign_keys:
                    report.append(f"  - {fk}")

    if not found:
        report.append("No se detectaron modelos SQLAlchemy.")


# ============================================================
# FINANZAS
# ============================================================

FINANCE_TERMS = [
    "finanza",
    "contable",
    "contabilidad",
    "asiento",
    "cuenta",
    "debe",
    "haber",
    "igv",
    "deveng",
    "costo",
    "cif",
    "mpd",
    "mod",
    "rentabilidad",
    "balance",
    "resultado",
    "mayor",
    "diario",
    "cxp",
    "cxc",
]


def finance_analysis(report: list[str]):
    add_section(report, "5. AUDITORÍA ESPECÍFICA DE FINANZAS")

    matches = []

    for path in project_files():
        text = safe_read(path).lower()

        found_terms = [
            term for term in FINANCE_TERMS
            if term in text
        ]

        if found_terms:
            matches.append((path, found_terms))

    report.append("Archivos relacionados con Finanzas:")
    for path, terms in sorted(matches):
        report.append(
            f"  - {rel(path)}"
            f" | términos: {', '.join(sorted(set(terms)))}"
        )

    report.append("")
    report.append("Archivos esperados/relevantes:")

    expected = [
        "app/models/finanzas.py",
        "app/services/finanzas.py",
        "app/routers/finanzas.py",
        "app/templates/finanzas",
    ]

    for item in expected:
        path = ROOT / item
        report.append(
            f"  {'[OK]' if path.exists() else '[FALTA]'} {item}"
        )


# ============================================================
# RENDIMIENTO
# ============================================================

def rendimento_analysis(report: list[str]):
    add_section(report, "6. AUDITORÍA DE RENDIMIENTO Y ACOPLAMIENTO")

    rendimiento = ROOT / "app" / "modules" / "rendimiento"

    if not rendimiento.exists():
        report.append(
            "[ALERTA] No existe app/modules/rendimiento/"
        )
        return

    report.append(
        "Contenido de app/modules/rendimiento/:"
    )

    for path in sorted(rendimiento.rglob("*")):
        if path.is_file():
            report.append(
                f"  - {rel(path)}"
            )

    report.append("")
    report.append(
        "Referencias a rendimiento encontradas fuera del módulo:"
    )

    for path in project_files():
        if "app/modules/rendimiento" in rel(path):
            continue

        text = safe_read(path)

        if re.search(
            r"\brendimiento\b|\bRendimiento\b",
            text
        ):
            report.append(
                f"  - {rel(path)}"
            )

    report.append("")
    report.append(
        "IMPORTANTE: este análisis es estático."
    )
    report.append(
        "No determina por sí solo si el acoplamiento es correcto."
    )


# ============================================================
# DEPENDENCIAS / IMPORTS
# ============================================================

def dependency_analysis(report: list[str]):
    add_section(report, "7. DEPENDENCIAS ENTRE MÓDULOS")

    targets = [
        "finanzas",
        "rendimiento",
        "ventas",
        "inventario",
        "inventory",
        "produccion",
        "taller",
        "purchasing",
        "billing",
        "cash",
    ]

    dependencies = defaultdict(set)

    for path in python_files():
        data = analyze_python_file(path)

        for imported in data["imports"]:
            imported_lower = imported.lower()

            for target in targets:
                if target in imported_lower:
                    dependencies[rel(path)].add(imported)

    for source, imports in sorted(dependencies.items()):
        report.append(f"\n{source}")
        for imported in sorted(imports):
            report.append(f"  -> {imported}")


# ============================================================
# ALEMBIC
# ============================================================

def alembic_analysis(report: list[str]):
    add_section(report, "8. ALEMBIC / MIGRACIONES")

    versions = ROOT / "alembic" / "versions"

    if not versions.exists():
        report.append("[FALTA] alembic/versions/")
        return

    files = sorted(versions.glob("*.py"))

    report.append(
        f"Migraciones encontradas: {len(files)}"
    )

    for path in files:
        text = safe_read(path)

        revision = re.search(
            r"revision\s*=\s*[\"']([^\"']+)",
            text
        )

        down_revision = re.search(
            r"down_revision\s*=\s*[\"']([^\"']+)",
            text
        )

        report.append(
            f"\n- {path.name}"
        )

        report.append(
            f"  revision: "
            f"{revision.group(1) if revision else '[NO DETECTADA]'}"
        )

        report.append(
            f"  down_revision: "
            f"{down_revision.group(1) if down_revision else '[NO DETECTADA]'}"
        )

        if "finanza" in text.lower():
            report.append(
                "  [FINANZAS] La migración contiene referencias financieras."
            )


# ============================================================
# TESTS
# ============================================================

def tests_analysis(report: list[str]):
    add_section(report, "9. TESTS")

    tests_dir = ROOT / "tests"

    if not tests_dir.exists():
        report.append("[FALTA] Directorio tests/")
        return

    tests = sorted(tests_dir.rglob("test_*.py"))

    report.append(
        f"Archivos de tests: {len(tests)}"
    )

    keywords = [
        "finanza",
        "contab",
        "asiento",
        "costo",
        "mod",
        "mpd",
        "cif",
        "balance",
        "rendimiento",
        "venta",
        "compra",
    ]

    for path in tests:
        text = safe_read(path).lower()

        found = [
            word for word in keywords
            if word in text
        ]

        report.append(
            f"- {rel(path)}"
            + (
                f" | temas: {', '.join(sorted(set(found)))}"
                if found else ""
            )
        )


# ============================================================
# BÚSQUEDA DE MODELOS FINANCIEROS
# ============================================================

def financial_model_detection(report: list[str]):
    add_section(report, "10. DETECCIÓN DE ESTRUCTURAS CONTABLES")

    patterns = {
        "AsientoContable": r"\bAsientoContable\b",
        "LineaAsiento": r"\bLineaAsiento\b",
        "CuentaContable": r"\bCuentaContable\b",
        "PeriodoContable": r"\bPeriodoContable\b",
        "CentroCosto": r"\bCentroCosto\b",
        "GastoOperativo": r"\bGastoOperativo\b",
        "Debe": r"\bdebe\b",
        "Haber": r"\bhaber\b",
        "IGV": r"\bIGV\b|\bigv\b",
        "devengado": r"\bdeveng",
        "CIF": r"\bCIF\b",
        "MPD": r"\bMPD\b",
        "MOD": r"\bMOD\b",
        "costo de ventas": r"costo.{0,10}ventas",
        "balance": r"\bbalance\b",
        "estado de resultados": r"estado.{0,10}resultados",
    }

    for name, pattern in patterns.items():
        locations = []

        regex = re.compile(pattern, re.IGNORECASE)

        for path in project_files():
            text = safe_read(path)

            if regex.search(text):
                locations.append(rel(path))

        report.append(
            f"\n{name}:"
        )

        if locations:
            for location in locations:
                report.append(f"  - {location}")
        else:
            report.append("  [NO DETECTADO]")


# ============================================================
# ALERTAS HEURÍSTICAS
# ============================================================

def heuristic_alerts(report: list[str]):
    add_section(report, "11. ALERTAS HEURÍSTICAS")

    alerts = []

    finance_model = ROOT / "app" / "models" / "finanzas.py"
    finance_service = ROOT / "app" / "services" / "finanzas.py"

    if finance_model.exists():
        text = safe_read(finance_model)

        if (
            "AsientoContable" in text
            and "LineaAsiento" not in text
            and "LineaAsientoContable" not in text
        ):
            alerts.append(
                "[ALTA] Parece existir AsientoContable sin "
                "una entidad explícita de líneas de asiento."
            )

        if "codigo_cuenta" in text:
            alerts.append(
                "[MEDIA] Se detecta codigo_cuenta como posible "
                "identificador directo de cuenta. Verificar si existe "
                "CuentaContable con ForeignKey."
            )

        if "centro_costo" in text and "String" in text:
            alerts.append(
                "[MEDIA] centro_costo parece estar modelado como String. "
                "Verificar si debería existir entidad CentroCosto."
            )

    if not finance_service.exists():
        alerts.append(
            "[ALTA] No se detecta app/services/finanzas.py."
        )

    rendimiento = ROOT / "app" / "modules" / "rendimiento"

    if rendimiento.exists():
        rendimiento_files = {
            rel(p): safe_read(p)
            for p in rendimiento.rglob("*.py")
        }

        external_references = []

        for path in python_files():
            if rendimiento in path.parents:
                continue

            text = safe_read(path)

            if re.search(
                r"app\.modules\.rendimiento|modules\.rendimiento",
                text
            ):
                external_references.append(rel(path))

        if external_references:
            alerts.append(
                "[REVISAR] Existen imports directos hacia "
                "app.modules.rendimiento desde otros archivos:"
            )

            for item in external_references:
                alerts.append(f"    - {item}")

    if not alerts:
        alerts.append(
            "No se detectaron alertas heurísticas."
        )

    for alert in alerts:
        report.append(alert)


# ============================================================
# RESUMEN EJECUTIVO
# ============================================================

def executive_summary(report: list[str]):
    add_section(report, "12. RESUMEN EJECUTIVO")

    checks = []

    paths = {
        "Modelo Finanzas": ROOT / "app/models/finanzas.py",
        "Servicio Finanzas": ROOT / "app/services/finanzas.py",
        "Router Finanzas": ROOT / "app/routers/finanzas.py",
        "Templates Finanzas": ROOT / "app/templates/finanzas",
        "Módulo Rendimiento": ROOT / "app/modules/rendimiento",
        "Alembic": ROOT / "alembic",
        "Tests": ROOT / "tests",
    }

    for name, path in paths.items():
        checks.append(
            (name, path.exists())
        )

    for name, exists in checks:
        report.append(
            f"{'[OK]' if exists else '[FALTA]'} {name}"
        )

    report.append("")
    report.append(
        "Este informe NO certifica la corrección contable."
    )
    report.append(
        "Su objetivo es proporcionar evidencia estructural para una "
        "auditoría posterior del código."
    )


# ============================================================
# MAIN
# ============================================================

def main():
    report = []

    report.append("SUIT ELANS ERP — INFORME DE AUDITORÍA ESTÁTICA")
    report.append("=" * 80)
    report.append(
        f"Fecha de generación: "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    report.append(f"Proyecto: {ROOT}")
    report.append("")
    report.append(
        "Este documento fue generado automáticamente."
    )
    report.append(
        "No modifica el código ni la base de datos."
    )

    build_tree(report)
    project_statistics(report)
    python_analysis(report)
    sqlalchemy_analysis(report)
    finance_analysis(report)
    rendimento_analysis(report)
    dependency_analysis(report)
    alembic_analysis(report)
    tests_analysis(report)
    financial_model_detection(report)
    heuristic_alerts(report)
    executive_summary(report)

    OUTPUT.write_text(
        "\n".join(report),
        encoding="utf-8"
    )

    print("")
    print("=" * 70)
    print("AUDITORÍA COMPLETADA")
    print("=" * 70)
    print(f"Informe generado en:")
    print(OUTPUT)
    print("")
    print("No se modificó ningún archivo del proyecto.")


if __name__ == "__main__":
    main()
