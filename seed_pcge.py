#!/usr/bin/env python3
"""Compat: redirige al seeder único del Plan Operativo (seed_pcge_nuevo.py).

El plan anterior (PCGE_ANALITICAS/seed_pcge_basico) fue desactivado.
Uso: .venv/bin/python seed_pcge.py
"""
from seed_pcge_nuevo import main

if __name__ == "__main__":
    raise SystemExit(main())
