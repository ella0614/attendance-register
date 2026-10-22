# Attendance Tracker

A small attendance app using Python's standard library and SQLite. No package installation is needed.

## Run locally (PowerShell)

Open PowerShell in this folder and start the server:

```powershell
$env:ATTENDANCE_ADMIN_EMAIL = "manager@example.com"
$env:ATTENDANCE_ADMIN_PASSWORD = "ChooseAUniquePassword123"
& "C:\Python314\python.exe" server.py
```

Open http://127.0.0.1:8000 in your browser. Keep the terminal running while using the website. Stop the server with Ctrl+C. The management email/password are configured through environment variables and are not created by public signup. Staff members can register through the signup page.

The SQLite database is created automatically in `.attendance-data/attendance.sqlite3`. Back it up to preserve user accounts and attendance records. Change the management password by stopping the server, setting the new `ATTENDANCE_ADMIN_PASSWORD`, and starting it again.

## API overview

- `POST /api/signup` — create a staff account
- `POST /api/login` — sign in as staff or management; session cookie is HttpOnly
- `POST /api/logout` — end the session
- `GET /api/me` — current signed-in account
- `GET /api/attendance/today` — staff member's attendance and recent records
- `POST /api/attendance/check-in` and `/api/attendance/check-out` — record attendance
- `GET /api/management/attendance` — management-only attendance overview

This backend is intended as a local/demo starting point. Passwords are stored as salted PBKDF2 hashes, not plaintext. Management signup is intentionally disabled.

## Free demo deployment from GitHub with Render

GitHub Pages only serves static files; it cannot run this Python backend. The included `render.yaml` deploys both the website and API as one Render web service on its free plan. This is suitable for a project demo, but the free service can sleep when idle and the SQLite database is temporary: accounts and attendance can be erased when Render restarts, redeploys, or replaces the service. Do not use it for important records. Keeping data reliably requires a paid persistent disk or an external database with its own free-tier limits.

1. Create a new **private** GitHub repository.
2. In PowerShell, open this project folder and run `git init`, `git add .`, then `git commit -m "Prepare attendance app for deployment"`.
3. Add the repository URL as the `origin` remote, then push the `main` branch to GitHub.
4. In Render, choose **New → Blueprint**, connect the GitHub repository, and deploy the detected `render.yaml`. Confirm the service plan is **Free**; do not add a paid disk.
5. When prompted for `ATTENDANCE_ADMIN_EMAIL` and `ATTENDANCE_ADMIN_PASSWORD`, enter the management login you want. Set a long, unique password; never put credentials in source files or commit them.
6. Open the Render service URL after deployment. Use `/` for the site and `/api/health` to check that the service and database are responding.

The backend and static website are served from the same origin, so login cookies and API calls work without extra CORS configuration. For a production attendance system, use persistent managed storage, backups, rate limiting, and stronger operational monitoring.
