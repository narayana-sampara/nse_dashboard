"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, fetchCurrentUser, getToken, type CurrentUser, type MenuKey } from "../../lib/auth";

const MENU_ITEMS: { key: MenuKey; label: string }[] = [
  { key: "signals", label: "Signals" },
  { key: "weekly", label: "Weekly Predictions" },
  { key: "monthly", label: "Monthly Predictions" },
  { key: "radar", label: "Early Growth Radar" },
  { key: "future", label: "Future Stocks" },
  { key: "analysis", label: "Deep Dive" },
  { key: "five_percent_strategy", label: "AI 5% Growth Strategy" },
];

type AdminUser = { id: number; username: string; role: "admin" | "user"; permissions: MenuKey[] };

export default function AdminPage() {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [newUsername, setNewUsername] = useState("");
  const [newPassword, setNewPassword] = useState("");

  const loadUsers = useCallback(async () => {
    const response = await apiFetch("/api/v1/admin/users", { cache: "no-store" });
    if (!response.ok) { setError(`Unable to load users (${response.status})`); return; }
    setUsers(await response.json());
  }, []);

  useEffect(() => {
    if (!getToken()) { router.replace("/login"); return; }
    void fetchCurrentUser().then((user: CurrentUser | null) => {
      if (!user || user.role !== "admin") { router.replace("/"); return; }
      setReady(true);
      void loadUsers();
    });
  }, [router, loadUsers]);

  const toggleMenu = (userId: number, key: MenuKey) => {
    setUsers((previous) => previous.map((user) => user.id === userId
      ? { ...user, permissions: user.permissions.includes(key) ? user.permissions.filter((k) => k !== key) : [...user.permissions, key] }
      : user));
  };

  const savePermissions = async (user: AdminUser) => {
    setStatus(""); setError("");
    const response = await apiFetch(`/api/v1/admin/users/${user.id}/permissions`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ menu_keys: user.permissions }),
    });
    if (!response.ok) { setError(`Unable to save permissions for ${user.username}`); return; }
    setStatus(`Saved permissions for ${user.username}`);
  };

  const createUser = async (event: React.FormEvent) => {
    event.preventDefault();
    setStatus(""); setError("");
    const response = await apiFetch("/api/v1/admin/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: newUsername, password: newPassword }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      setError(payload.detail ?? `Unable to create user (${response.status})`);
      return;
    }
    setNewUsername(""); setNewPassword("");
    await loadUsers();
  };

  if (!ready) return <main className="auth-loading">Loading…</main>;

  return (
    <main className="admin-page">
      <header className="topbar">
        <span className="logo">N</span><strong>Admin: menu access</strong>
        <a href="/">Back to dashboard</a>
      </header>
      {error && <div className="error" role="alert">{error}</div>}
      {status && <div className="notice" role="status">{status}</div>}

      <section className="admin-create">
        <h2>Create user</h2>
        <form onSubmit={createUser}>
          <input placeholder="Username" value={newUsername} onChange={(event) => setNewUsername(event.target.value)} required />
          <input type="password" placeholder="Password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required />
          <button type="submit">Create</button>
        </form>
      </section>

      <section className="admin-users">
        <h2>Menu access</h2>
        <table>
          <thead>
            <tr>
              <th>User</th>
              <th>Role</th>
              {MENU_ITEMS.map((item) => <th key={item.key}>{item.label}</th>)}
              <th></th>
            </tr>
          </thead>
          <tbody>
            {users.map((user) => (
              <tr key={user.id}>
                <td>{user.username}</td>
                <td>{user.role}</td>
                {MENU_ITEMS.map((item) => (
                  <td key={item.key}>
                    <input
                      type="checkbox"
                      disabled={user.role === "admin"}
                      checked={user.role === "admin" || user.permissions.includes(item.key)}
                      onChange={() => toggleMenu(user.id, item.key)}
                    />
                  </td>
                ))}
                <td>{user.role !== "admin" && <button onClick={() => void savePermissions(user)}>Save</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </main>
  );
}
