"""Project-only secret bootstrap and Compose wrapper. Never prints secret values."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROJECT = os.environ.get("APPTECH_PROJECT", "apptech-academic-sync")
if not re.fullmatch(r"apptech-academic-sync(?:-[a-z0-9-]+)?", PROJECT):
    raise SystemExit("Refusing to manage a project outside the assessment namespace")
DIRECTORY = Path(os.environ.get("APPTECH_SECRETS_DIR", str(ROOT / ".secrets"))).resolve()
if DIRECTORY.parent != ROOT or not DIRECTORY.name.startswith(".secrets"):
    raise SystemExit("Secret directory must be a .secrets* directory inside this project")
os.environ["APPTECH_SECRETS_DIR"] = str(DIRECTORY)
COMMAND = [
    "docker", "compose", "-p", PROJECT,
    "-f", str(ROOT / "docker-compose.yml"),
    "-f", str(ROOT / "compose.hardened.yml"),
]


def run(arguments, *, payload=None, capture=False):
    result = subprocess.run(
        COMMAND + arguments, cwd=ROOT, input=payload,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if result.returncode:
        # Captured SQL diagnostics can echo a statement. Keep them off the terminal.
        raise SystemExit(f"Assessment command failed ({result.returncode}); captured diagnostics suppressed")
    return result.stdout


def prepare_secrets():
    if DIRECTORY.is_symlink():
        raise SystemExit("Refusing a symlink secret directory")
    DIRECTORY.mkdir(mode=0o700, exist_ok=True)
    DIRECTORY.chmod(0o700)
    for name in ["db_password", "django_secret_key"]:
        path = DIRECTORY / name
        if path.is_symlink():
            raise SystemExit("Refusing a symlink secret file")
        if not path.exists():
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(secrets.token_urlsafe(64) + "\n")
        if not path.read_text().strip():
            raise SystemExit("Refusing an empty secret file")
        # The 0700 host directory protects these files. Readable file mounts let
        # container UID 10001 read them without relying on Compose uid remapping.
        path.chmod(0o444)


def verify_baseline(state):
    baseline = state / "baseline.json"
    if not baseline.exists():
        return
    saved = json.loads(baseline.read_text())
    tables = {
        "checkout_student", "checkout_course", "checkout_enrollment",
        "checkout_erpsynclog", "mock_erp_mockerpstudent", "mock_erp_mockerpinvoice",
    }
    if not set(saved).issubset(tables):
        raise SystemExit("Unexpected preservation baseline tables")
    for table, rows in saved.items():
        sql = f"SELECT coalesce(json_agg(t ORDER BY id),'[]'::json) FROM {table} t;"
        raw = run(["exec", "-T", "db", "psql", "-U", "apptech", "-d", "apptech", "-Atc", sql], capture=True)
        current = {row["id"]: row for row in json.loads(raw)}
        if any(current.get(row["id"]) != row for row in rows or []):
            raise SystemExit("Preservation baseline differs; credential change not attempted")
    print("Existing assessment records match the private preservation baseline", flush=True)


def configure_database():
    state = ROOT / ".state" / PROJECT
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    (ROOT / ".state").chmod(0o700)
    state.chmod(0o700)
    password = (DIRECTORY / "db_password").read_text().strip()
    digest = hashlib.sha256(password.encode()).hexdigest()
    marker = state / "credential.sha256"
    if marker.exists() and marker.read_text() == digest:
        return
    run(["stop", "web", "worker", "beat"])
    run(["up", "-d", "--wait", "--no-deps", "db"])
    verify_baseline(state)
    backup = state / "before-secret-rotation.dump"
    if not backup.exists():
        data = run(["exec", "-T", "db", "pg_dump", "-U", "apptech", "-d", "apptech", "-Fc"], capture=True)
        with backup.open("xb") as stream:
            stream.write(data)
        backup.chmod(0o600)
    # The password goes only through stdin, never argv/environment/terminal.
    # Disable statement/error-statement logging for this one local admin session.
    quoted = password.replace("'", "''")
    sql = (
        "SET log_statement='none'; SET log_min_duration_statement=-1; "
        "SET log_min_error_statement='panic'; "
        f"ALTER ROLE apptech PASSWORD '{quoted}';\n"
    )
    run(
        ["exec", "-T", "-e", "PGOPTIONS=-c log_statement=none -c log_min_duration_statement=-1 -c log_min_error_statement=panic",
         "db", "psql", "-q", "-U", "apptech", "-d", "apptech", "-v", "ON_ERROR_STOP=1"],
        payload=sql.encode(), capture=True,
    )
    marker.write_text(digest)
    marker.chmod(0o600)
    print("Project database credential configured; data and private backup preserved", flush=True)


if __name__ == "__main__":
    os.umask(0o077)
    arguments = sys.argv[1:] or ["up", "--build", "-d", "--wait"]
    if arguments[0] == "up":
        prepare_secrets()
        run(["build", "web", "worker", "beat"])
        configure_database()
        arguments = [argument for argument in arguments if argument != "--build"]
    run(arguments)
