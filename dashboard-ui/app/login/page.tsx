"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { login } from "../../lib/auth";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const onSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      await login(username, password);
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="login-page">
      <form className="login-card" onSubmit={onSubmit}>
        <h1>NSE Operations Desk</h1>
        <p className="eyebrow">Sign in to continue</p>
        {error && <div className="error" role="alert">{error}</div>}
        <label>
          Username
          <input value={username} onChange={(event) => setUsername(event.target.value)} required autoFocus />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} required />
        </label>
        <button type="submit" disabled={submitting}>{submitting ? "Signing in…" : "Sign in"}</button>
      </form>
    </main>
  );
}
