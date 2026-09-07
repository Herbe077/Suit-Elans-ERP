"""Script reset_db.py: limpia operativas, preserva users, reinicia IDs."""
import os
import sqlite3
import subprocess
import sys

REPO = "/home/herbias/Projects/Suit-Elans-ERP"
SCRIPT = [sys.executable, "scripts/reset_db.py"]


def _seed(path):
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
    con.execute("CREATE TABLE clients (id INTEGER PRIMARY KEY, nombre TEXT)")
    con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, folio TEXT)")
    con.execute("CREATE TABLE alembic_version (version_num TEXT)")
    con.execute("INSERT INTO users (email) VALUES ('a@x.com'), ('b@x.com')")
    con.execute("INSERT INTO clients (nombre) VALUES ('C1'), ('C2'), ('C3')")
    con.execute("INSERT INTO orders (folio) VALUES ('SE-1'), ('SE-2')")
    con.execute("INSERT INTO alembic_version VALUES ('h1')")
    con.commit()
    con.close()


def test_reset_preserva_users_y_reinicia_ids(tmp_path):
    db = str(tmp_path / "reset_test.db")
    _seed(db)
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db}")
    r = subprocess.run(SCRIPT + ["--yes"], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr or r.stdout
    assert "users intactos" in r.stdout
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM alembic_version").fetchone()[0] == 1
    con.execute("INSERT INTO clients (nombre) VALUES ('N')")
    assert con.execute("SELECT id FROM clients").fetchone()[0] == 1  # identity reiniciado
    con.close()


def test_reset_dry_run_no_borra(tmp_path):
    db = str(tmp_path / "reset_dry.db")
    _seed(db)
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db}")
    r = subprocess.run(SCRIPT + ["--dry-run"], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "[dry-run]" in r.stdout
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == 3
    con.close()


def test_reset_bloquea_prod_sin_flag(tmp_path):
    db = str(tmp_path / "reset_prod.db")
    _seed(db)
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db}", ENV="production")
    r = subprocess.run(SCRIPT + ["--yes"], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 3 and "PRODUCCI" in r.stdout.upper()
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == 3
    con.close()
