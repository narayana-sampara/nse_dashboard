const TOKEN_KEY = "nse_token";

export type MenuKey =
  | "signals"
  | "weekly"
  | "monthly"
  | "radar"
  | "future"
  | "analysis"
  | "five_percent_strategy";

export type CurrentUser = {
  username: string;
  role: "admin" | "user";
  permissions: MenuKey[];
};

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY);
}

export function logout(): void {
  clearToken();
  window.location.href = "/login";
}

export async function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const token = getToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(input, { ...init, headers });
  if (response.status === 401 && typeof window !== "undefined") {
    clearToken();
    window.location.href = "/login";
  }
  return response;
}

export async function login(username: string, password: string): Promise<CurrentUser> {
  const response = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "Invalid username or password");
  }
  const data = await response.json();
  setToken(data.access_token);
  return { username: data.username, role: data.role, permissions: data.permissions };
}

export async function fetchCurrentUser(): Promise<CurrentUser | null> {
  if (!getToken()) return null;
  const response = await apiFetch("/api/v1/auth/me");
  if (!response.ok) return null;
  return response.json();
}
