"""Small, dependency-free backend for the attendance demo."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("ATTENDANCE_DB", BASE_DIR / ".attendance-data" / "attendance.sqlite3"))
SESSION_HOURS = 12
PBKDF2_ROUNDS = 310_000


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_time(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat(timespec="seconds").replace("+00:00", "Z")


@contextmanager
def get_db():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"{salt.hex()}:{digest.hex()}"


def password_matches(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split(":", 1)
        actual = password_hash(password, bytes.fromhex(salt_hex)).split(":", 1)[1]
        return hmac.compare_digest(actual, digest_hex)
    except (ValueError, TypeError):
        return False


def initialize_database() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('staff', 'management')),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                work_date TEXT NOT NULL,
                check_in TEXT,
                check_out TEXT,
                UNIQUE(user_id, work_date)
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL
            );
            """
        )
        admin_email = os.environ.get("ATTENDANCE_ADMIN_EMAIL", "").strip().lower()
        admin_password = os.environ.get("ATTENDANCE_ADMIN_PASSWORD", "")
        if admin_email and admin_password:
            db.execute(
                "INSERT INTO users(name, email, password_hash, role, created_at) VALUES (?, ?, ?, 'management', ?) "
                "ON CONFLICT(email) DO UPDATE SET name=excluded.name, password_hash=excluded.password_hash, role='management'",
                ("Management", admin_email, password_hash(admin_password), iso_time()),
            )
        db.execute("DELETE FROM sessions WHERE expires_at <= ?", (iso_time(),))


class AttendanceHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def log_message(self, format: str, *args) -> None:
        # Keep request logs, but never print request bodies or credentials.
        super().log_message(format, *args)

    def send_json(self, status: int, payload: dict, extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Send request data as JSON.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid request length.") from exc
        if length < 1 or length > 16_384:
            raise ValueError("Request body is missing or too large.")
        try:
            value = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Invalid JSON.") from exc
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object.")
        return value

    def session_user(self) -> sqlite3.Row | None:
        cookie = self.headers.get("Cookie", "")
        token = next((part.strip()[8:] for part in cookie.split(";") if part.strip().startswith("session=")), None)
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with get_db() as db:
            return db.execute(
                "SELECT users.id, users.name, users.email, users.role FROM sessions "
                "JOIN users ON users.id=sessions.user_id WHERE sessions.token_hash=? AND sessions.expires_at>?",
                (token_hash, iso_time()),
            ).fetchone()

    def require_user(self, role: str | None = None) -> sqlite3.Row | None:
        user = self.session_user()
        if not user or (role and user["role"] != role):
            self.send_json(401 if not user else 403, {"error": "Please sign in with an account that has access."})
            return None
        return user

    def is_owner(self, user: sqlite3.Row) -> bool:
        owner_email = os.environ.get("ATTENDANCE_ADMIN_EMAIL", "").strip().lower()
        return bool(owner_email) and user["email"].lower() == owner_email

    def require_owner(self) -> sqlite3.Row | None:
        user = self.require_user("management")
        if user and not self.is_owner(user):
            self.send_json(403, {"error": "Only the configured account owner can manage management accounts."})
            return None
        return user

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            try:
                with get_db() as db:
                    db.execute("SELECT 1")
                self.send_json(200, {"status": "ok"})
            except sqlite3.Error:
                self.send_json(503, {"status": "unavailable"})
            return
        if path == "/api/me":
            user = self.session_user()
            if not user:
                self.send_json(401, {"error": "Not signed in."})
                return
            self.send_json(200, {"user": {**dict(user), "isOwner": self.is_owner(user)}})
            return
        if path == "/api/attendance/today":
            user = self.require_user("staff")
            if not user:
                return
            self.send_staff_attendance(user)
            return
        if path == "/api/management/attendance":
            user = self.require_user("management")
            if not user:
                return
            self.send_management_attendance()
            return
        if path == "/api/management/users":
            if not self.require_owner():
                return
            owner_email = os.environ.get("ATTENDANCE_ADMIN_EMAIL", "").strip().lower()
            with get_db() as db:
                users = db.execute(
                    "SELECT id, name, email FROM users WHERE role='management' AND email<>? "
                    "ORDER BY name COLLATE NOCASE",
                    (owner_email,),
                ).fetchall()
            self.send_json(200, {"users": [dict(row) for row in users]})
            return
        if path.startswith("/api/"):
            self.send_json(404, {"error": "API endpoint not found."})
            return
        if path == "/":
            self.path = "/index.html"
        else:
            requested = (BASE_DIR / unquote(path.lstrip("/"))).resolve()
            if requested.parent != BASE_DIR or requested.suffix.lower() not in {".html", ".css", ".js"} or not requested.is_file():
                self.send_json(404, {"error": "Page not found."})
                return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            data = self.read_json()
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
            return

        if path == "/api/signup":
            self.signup(data)
        elif path == "/api/login":
            self.login(data)
        elif path == "/api/logout":
            self.logout()
        elif path == "/api/attendance/check-in":
            self.record_attendance("check_in")
        elif path == "/api/attendance/check-out":
            self.record_attendance("check_out")
        elif path == "/api/management/users":
            self.create_management_user(data)
        else:
            self.send_json(404, {"error": "API endpoint not found."})

    def do_DELETE(self) -> None:
        if urlparse(self.path).path != "/api/management/users":
            self.send_json(404, {"error": "API endpoint not found."})
            return
        owner = self.require_owner()
        if not owner:
            return
        user_ids = parse_qs(urlparse(self.path).query).get("id", [])
        try:
            if len(user_ids) != 1:
                raise ValueError
            user_id = int(user_ids[0])
            if user_id < 1:
                raise ValueError
        except ValueError:
            self.send_json(400, {"error": "A valid management account ID is required."})
            return
        owner_email = os.environ.get("ATTENDANCE_ADMIN_EMAIL", "").strip().lower()
        with get_db() as db:
            cursor = db.execute(
                "DELETE FROM users WHERE id=? AND role='management' AND email<>?",
                (user_id, owner_email),
            )
        if cursor.rowcount == 0:
            self.send_json(404, {"error": "Management account not found."})
            return
        self.send_json(200, {"message": "Management account removed."})

    def create_management_user(self, data: dict) -> None:
        if not self.require_owner():
            return
        name = str(data.get("name", "")).strip()
        email = str(data.get("email", "")).strip().lower()
        password = str(data.get("password", ""))
        if not name or len(name) > 120:
            self.send_json(400, {"error": "Enter a name up to 120 characters."})
            return
        if len(email) > 254 or "@" not in email or "." not in email.rsplit("@", 1)[-1]:
            self.send_json(400, {"error": "Enter a valid email address."})
            return
        if not 8 <= len(password) <= 20 or not password.isascii() or not password.isalnum():
            self.send_json(400, {"error": "Password must be 8-20 letters and numbers."})
            return
        try:
            with get_db() as db:
                db.execute(
                    "INSERT INTO users(name, email, password_hash, role, created_at) "
                    "VALUES (?, ?, ?, 'management', ?)",
                    (name, email, password_hash(password), iso_time()),
                )
        except sqlite3.IntegrityError:
            self.send_json(409, {"error": "An account with this email already exists."})
            return
        self.send_json(201, {"message": "Management account created."})

    def signup(self, data: dict) -> None:
        name = str(data.get("name", "")).strip()
        email = str(data.get("email", "")).strip().lower()
        password = str(data.get("password", ""))
        if not name or len(name) > 120:
            self.send_json(400, {"error": "Enter a name up to 120 characters."})
            return
        if len(email) > 254 or "@" not in email or "." not in email.rsplit("@", 1)[-1]:
            self.send_json(400, {"error": "Enter a valid email address."})
            return
        if not 8 <= len(password) <= 20 or not password.isascii() or not password.isalnum():
            self.send_json(400, {"error": "Password must be 8-20 letters and numbers."})
            return
        try:
            with get_db() as db:
                db.execute(
                    "INSERT INTO users(name, email, password_hash, role, created_at) VALUES (?, ?, ?, 'staff', ?)",
                    (name, email, password_hash(password), iso_time()),
                )
        except sqlite3.IntegrityError:
            self.send_json(409, {"error": "An account with this email already exists. Please sign in."})
            return
        self.send_json(201, {"message": "Account created. You can now sign in."})

    def login(self, data: dict) -> None:
        email = str(data.get("email", "")).strip().lower()
        password = str(data.get("password", ""))
        role = data.get("role")
        if role not in ("staff", "management"):
            self.send_json(400, {"error": "Choose a valid account type."})
            return
        with get_db() as db:
            user = db.execute("SELECT * FROM users WHERE email=? AND role=?", (email, role)).fetchone()
            if not user or not password_matches(password, user["password_hash"]):
                self.send_json(401, {"error": "Email or password is incorrect."})
                return
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            expires = iso_time(utc_now() + timedelta(hours=SESSION_HOURS))
            db.execute("INSERT INTO sessions(token_hash, user_id, expires_at) VALUES (?, ?, ?)", (token_hash, user["id"], expires))
        secure = "; Secure" if os.environ.get("ATTENDANCE_COOKIE_SECURE") == "1" else ""
        cookie = f"session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_HOURS * 3600}{secure}"
        self.send_json(200, {"user": {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"]}}, {"Set-Cookie": cookie})

    def logout(self) -> None:
        user = self.session_user()
        if user:
            cookie = self.headers.get("Cookie", "")
            token = next((part.strip()[8:] for part in cookie.split(";") if part.strip().startswith("session=")), None)
            if token:
                token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
                with get_db() as db:
                    db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
        self.send_json(200, {"message": "Signed out."}, {"Set-Cookie": "session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})

    def record_attendance(self, action: str) -> None:
        user = self.require_user("staff")
        if not user:
            return
        now = utc_now()
        date = now.date().isoformat()
        timestamp = iso_time(now)
        with get_db() as db:
            row = db.execute("SELECT * FROM attendance WHERE user_id=? AND work_date=?", (user["id"], date)).fetchone()
            if action == "check_in":
                if row and row["check_in"]:
                    self.send_json(409, {"error": "You have already checked in today."})
                    return
                db.execute(
                    "INSERT INTO attendance(user_id, work_date, check_in) VALUES (?, ?, ?) "
                    "ON CONFLICT(user_id, work_date) DO UPDATE SET check_in=excluded.check_in",
                    (user["id"], date, timestamp),
                )
            else:
                if not row or not row["check_in"]:
                    self.send_json(409, {"error": "Check in before checking out."})
                    return
                if row["check_out"]:
                    self.send_json(409, {"error": "You have already checked out today."})
                    return
                db.execute("UPDATE attendance SET check_out=? WHERE user_id=? AND work_date=?", (timestamp, user["id"], date))
        self.send_staff_attendance(user)

    def send_staff_attendance(self, user: sqlite3.Row) -> None:
        date = utc_now().date().isoformat()
        with get_db() as db:
            rows = db.execute(
                "SELECT work_date AS date, check_in AS checkIn, check_out AS checkOut FROM attendance "
                "WHERE user_id=? ORDER BY work_date DESC LIMIT 60", (user["id"],)
            ).fetchall()
        today = next((dict(row) for row in rows if row["date"] == date), {"date": date, "checkIn": None, "checkOut": None})
        self.send_json(200, {"today": today, "records": [dict(row) for row in rows]})

    def send_management_attendance(self) -> None:
        date = utc_now().date().isoformat()
        with get_db() as db:
            rows = db.execute(
                "SELECT users.name, users.email, attendance.check_in AS checkIn, attendance.check_out AS checkOut "
                "FROM users LEFT JOIN attendance ON attendance.user_id=users.id AND attendance.work_date=? "
                "WHERE users.role='staff' ORDER BY users.name COLLATE NOCASE", (date,)
            ).fetchall()
        staff = []
        for row in rows:
            item = dict(row)
            item["status"] = "Checked out" if item["checkOut"] else "Present" if item["checkIn"] else "Absent"
            staff.append(item)
        present = sum(1 for item in staff if item["checkIn"])
        self.send_json(200, {"date": date, "totalStaff": len(staff), "present": present, "absent": len(staff) - present, "staff": staff})


def main() -> None:
    initialize_database()
    host = os.environ.get("ATTENDANCE_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    admin_email = os.environ.get("ATTENDANCE_ADMIN_EMAIL", "").strip()
    if not admin_email or not os.environ.get("ATTENDANCE_ADMIN_PASSWORD"):
        print("Management sign-in is disabled. Set ATTENDANCE_ADMIN_EMAIL and ATTENDANCE_ADMIN_PASSWORD to enable it.")
    print(f"Attendance website running at http://{host}:{port}")
    ThreadingHTTPServer((host, port), AttendanceHandler).serve_forever()


if __name__ == "__main__":
    main()
