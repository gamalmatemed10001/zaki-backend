const STORAGE_KEY = "zaki-session-id";

export function getSessionId(): string {
  if (typeof window === "undefined") return "default";

  let id = window.localStorage.getItem(STORAGE_KEY);
  if (!id) {
    id = crypto.randomUUID();
    window.localStorage.setItem(STORAGE_KEY, id);
  }
  return id;
}
