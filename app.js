async function apiRequest(path, options = {}) {
    const response = await fetch(path, {
        ...options,
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        credentials: "same-origin"
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "The request could not be completed.");
    return result;
}

function validateInput(email, password) {
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return "Please enter a valid email address.";
    if (!/^[a-zA-Z0-9]{8,20}$/.test(password)) return "Password must be 8-20 characters long and contain only letters and numbers.";
    return null;
}

function setMessage(element, text, success = false) {
    if (!element) return;
    element.textContent = text;
    element.classList.toggle("text-danger", !success);
    element.classList.toggle("text-success", success);
}

function togglePasswordVisibility() {
    const passwordInput = document.getElementById("password");
    const toggleButton = document.getElementById("togglePassword");
    if (!passwordInput || !toggleButton) return;
    passwordInput.type = passwordInput.type === "password" ? "text" : "password";
    toggleButton.textContent = passwordInput.type === "password" ? "Show Password" : "Hide Password";
}

const loginForm = document.getElementById("loginForm");
if (loginForm) {
    loginForm.addEventListener("submit", async function (event) {
        event.preventDefault();
        const nameInput = document.getElementById("name");
        const name = nameInput ? nameInput.value.trim() : "";
        const email = document.getElementById("email").value.trim().toLowerCase();
        const password = document.getElementById("password").value;
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || !password || (nameInput && !name)) {
            alert(nameInput && !name ? "Please enter your full name." : "Enter a valid email and password.");
            return;
        }
        try {
            const result = await apiRequest("/api/login", {
                method: "POST",
                body: JSON.stringify({ email, password, role: nameInput ? "staff" : "management" })
            });
            if (nameInput && result.user.name.toLowerCase() !== name.toLowerCase()) {
                await apiRequest("/api/logout", { method: "POST", body: "{}" });
                throw new Error("The name does not match this staff account.");
            }
            window.location.href = nameInput ? "staff_dashboard.html" : "management_dashboard.html";
        } catch (error) {
            alert(error.message);
        }
    });
}

const signupForm = document.getElementById("signupForm");
if (signupForm) {
    signupForm.addEventListener("submit", async function (event) {
        event.preventDefault();
        const name = document.getElementById("signupName").value.trim();
        const email = document.getElementById("signupEmail").value.trim().toLowerCase();
        const password = document.getElementById("signupPassword").value;
        const message = document.getElementById("signupMessage");
        const validationError = validateInput(email, password);
        if (!name) return setMessage(message, "Please enter your full name.");
        if (validationError) return setMessage(message, validationError);
        if (password !== document.getElementById("confirmPassword").value) return setMessage(message, "The passwords do not match.");
        const button = signupForm.querySelector('button[type="submit"]');
        button.disabled = true;
        try {
            const result = await apiRequest("/api/signup", {
                method: "POST",
                body: JSON.stringify({ name, email, password })
            });
            signupForm.reset();
            setMessage(message, result.message + " ", true);
            const link = document.createElement("a");
            link.href = "staff.html";
            link.textContent = "Continue to staff sign in";
            message.appendChild(link);
        } catch (error) {
            setMessage(message, error.message);
        } finally {
            button.disabled = false;
        }
    });
}

function formatTime(value) {
    return value ? new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—";
}

function formatDate(value) {
    return new Date(`${value}T00:00:00`).toLocaleDateString();
}

function addCell(row, value) {
    const cell = document.createElement("td");
    cell.textContent = value;
    row.appendChild(cell);
}

async function loadStaffDashboard(user) {
    const userName = document.getElementById("staffName");
    if (userName) userName.textContent = user.name;
    const recordsBody = document.getElementById("staffAttendanceRows");
    const status = document.getElementById("staffAttendanceStatus");
    const time = document.getElementById("staffAttendanceTime");
    const message = document.getElementById("staffAttendanceMessage");
    const checkInButton = document.getElementById("checkInButton");
    const checkOutButton = document.getElementById("checkOutButton");

    async function refresh() {
        const data = await apiRequest("/api/attendance/today");
        const today = data.today;
        const checkedIn = Boolean(today.checkIn);
        if (status) status.textContent = today.checkOut ? "Checked out" : checkedIn ? "Present" : "Not checked in";
        if (time) time.textContent = checkedIn ? `Checked in at ${formatTime(today.checkIn)}` : "Use the buttons below to record attendance";
        if (message) message.textContent = today.checkOut ? `Checked out at ${formatTime(today.checkOut)}.` : checkedIn ? "You are currently checked in." : "You have not checked in yet today.";
        if (checkInButton) checkInButton.disabled = checkedIn;
        if (checkOutButton) checkOutButton.disabled = !checkedIn || Boolean(today.checkOut);
        if (recordsBody) {
            recordsBody.replaceChildren();
            data.records.forEach(function (record) {
                const row = document.createElement("tr");
                addCell(row, formatDate(record.date));
                addCell(row, user.name);
                addCell(row, record.checkOut ? "Checked out" : record.checkIn ? "Present" : "Absent");
                recordsBody.appendChild(row);
            });
        }

        const monthlySummary = document.getElementById("staffMonthlySummary");
        if (monthlySummary) {
            const month = new Date().toISOString().slice(0, 7);
            const monthRecords = data.records.filter((record) => record.date.startsWith(month));
            const checkedOutDays = monthRecords.filter((record) => record.checkOut).length;
            monthlySummary.textContent = `${monthRecords.length} attendance day(s) recorded this month; ${checkedOutDays} complete check-out(s).`;
        }
    }

    async function record(action, button) {
        button.disabled = true;
        try {
            await apiRequest(`/api/attendance/${action}`, { method: "POST", body: "{}" });
            await refresh();
        } catch (error) {
            if (message) message.textContent = error.message;
            button.disabled = false;
        }
    }
    if (checkInButton) checkInButton.addEventListener("click", () => record("check-in", checkInButton));
    if (checkOutButton) checkOutButton.addEventListener("click", () => record("check-out", checkOutButton));
    await refresh();
}

async function loadManagementDashboard(user) {
    const data = await apiRequest("/api/management/attendance");
    document.getElementById("totalStaffCount").textContent = data.totalStaff;
    document.getElementById("presentStaffCount").textContent = data.present;
    document.getElementById("absentStaffCount").textContent = data.absent;
    const summary = document.getElementById("managementReportSummary");
    summary.replaceChildren();
    [
        `${data.totalStaff} staff member(s) registered.`,
        `${data.present} staff member(s) checked in today.`,
        `${data.absent} staff member(s) have not checked in today.`
    ].forEach(function (text) {
        const item = document.createElement("li");
        item.textContent = text;
        summary.appendChild(item);
    });
    const rows = document.getElementById("managementAttendanceRows");
    rows.replaceChildren();
    data.staff.forEach(function (staff) {
        const row = document.createElement("tr");
        addCell(row, staff.name);
        addCell(row, staff.email);
        addCell(row, staff.status);
        addCell(row, formatTime(staff.checkIn));
        addCell(row, formatTime(staff.checkOut));
        rows.appendChild(row);
    });

    const accountsPanel = document.getElementById("managementAccountsPanel");
    const accountsLink = document.getElementById("managementAccountsLink");
    if (!user.isOwner || !accountsPanel) return;
    accountsPanel.hidden = false;
    if (accountsLink) accountsLink.hidden = false;

    const accountForm = document.getElementById("managementAccountForm");
    const accountMessage = document.getElementById("managementAccountMessage");
    const accountRows = document.getElementById("managementAccountRows");

    async function refreshManagementAccounts() {
        const result = await apiRequest("/api/management/users");
        accountRows.replaceChildren();
        result.users.forEach(function (manager) {
            const row = document.createElement("tr");
            addCell(row, manager.name);
            addCell(row, manager.email);
            const actionCell = document.createElement("td");
            const removeButton = document.createElement("button");
            removeButton.type = "button";
            removeButton.className = "management-account-button danger";
            removeButton.textContent = "Revoke";
            removeButton.addEventListener("click", async function () {
                if (!window.confirm(`Remove management access for ${manager.name}?`)) return;
                removeButton.disabled = true;
                try {
                    await apiRequest(`/api/management/users?id=${manager.id}`, { method: "DELETE" });
                    setMessage(accountMessage, "Management access removed.", true);
                    await refreshManagementAccounts();
                } catch (error) {
                    setMessage(accountMessage, error.message);
                    removeButton.disabled = false;
                }
            });
            actionCell.appendChild(removeButton);
            row.appendChild(actionCell);
            accountRows.appendChild(row);
        });
    }

    accountForm.addEventListener("submit", async function (event) {
        event.preventDefault();
        const name = document.getElementById("managerName").value.trim();
        const email = document.getElementById("managerEmail").value.trim().toLowerCase();
        const password = document.getElementById("managerPassword").value;
        const validationError = validateInput(email, password);
        if (!name) return setMessage(accountMessage, "Please enter the manager's name.");
        if (validationError) return setMessage(accountMessage, validationError);
        const button = accountForm.querySelector('button[type="submit"]');
        button.disabled = true;
        try {
            const result = await apiRequest("/api/management/users", {
                method: "POST",
                body: JSON.stringify({ name, email, password })
            });
            accountForm.reset();
            await refreshManagementAccounts();
            setMessage(accountMessage, result.message, true);
        } catch (error) {
            setMessage(accountMessage, error.message);
        } finally {
            button.disabled = false;
        }
    });

    try {
        await refreshManagementAccounts();
    } catch (error) {
        setMessage(accountMessage, error.message);
    }
}

async function initializeDashboard() {
    const staffPage = document.body.classList.contains("staff-dashboard-page");
    const managementPage = document.body.classList.contains("management-dashboard-page");
    if (!staffPage && !managementPage) return;
    try {
        const result = await apiRequest("/api/me");
        if (staffPage && result.user.role !== "staff") return window.location.replace("staff.html");
        if (managementPage && result.user.role !== "management") return window.location.replace("management.html");
        if (staffPage) await loadStaffDashboard(result.user);
        if (managementPage) await loadManagementDashboard(result.user);
    } catch (error) {
        window.location.replace(staffPage ? "staff.html" : "management.html");
    }
}

const logoutLinks = document.querySelectorAll("[data-logout]");
logoutLinks.forEach(function (link) {
    link.addEventListener("click", async function (event) {
        event.preventDefault();
        try {
            await apiRequest("/api/logout", { method: "POST", body: "{}" });
        } finally {
            window.location.href = "index.html";
        }
    });
});

initializeDashboard();
