"""Cálculos SAM / eficiencia / costos de taller."""


def eficiencia(sam_estimado: float, sam_real: float) -> float:
    """% eficiencia: >100% = más rápido que estándar."""
    if not sam_real or sam_real <= 0:
        return 0.0
    return round(sam_estimado / sam_real * 100, 1)


def costo_mano_obra(minutos: float, costo_por_minuto: float) -> float:
    return round(minutos * costo_por_minuto, 2)


def avance_garment(sam_estimado_total: float, sam_completado: float) -> float:
    if not sam_estimado_total:
        return 0.0
    return round(min(100.0, sam_completado / sam_estimado_total * 100), 1)


def sam_total_operaciones(operaciones: list) -> float:
    return round(sum(o.sam_minutos for o in operaciones), 1)
