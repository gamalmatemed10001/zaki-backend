export interface TaskItem {
  id: string;
  title: string;
  done: boolean;
  due_at: string | null;
}

export interface NoteItem {
  id: string;
  content: string;
  created_at: string;
}

export interface CalendarEvent {
  id: string;
  summary: string;
  start: string;
  link: string | null;
}

export interface EmailItem {
  id: string;
  from: string;
  subject: string;
  snippet: string;
}

export interface DashboardData {
  tasks: TaskItem[];
  notes: NoteItem[];
  calendar: CalendarEvent[] | null;
  emails: EmailItem[] | null;
  google_connected: boolean;
}

export interface DashboardResult {
  // null on failure -- the caller should leave any existing (possibly
  // stale-but-good) widget data alone rather than wiping it out because
  // of one failed refresh.
  data: DashboardData | null;
  // Non-null only on failure -- a short, user-facing Arabic message the
  // caller can show as a toast/inline warning instead of failing silently.
  error: string | null;
}

export async function fetchDashboard(): Promise<DashboardResult> {
  try {
    const res = await fetch("/api/dashboard", { cache: "no-store" });
    if (!res.ok) {
      let detail = "تعذر تحميل لوحة المعلومات.";
      try {
        const body = await res.json();
        if (typeof body?.detail === "string") detail = body.detail;
      } catch {
        // not JSON -- keep the generic fallback above.
      }
      return { data: null, error: detail };
    }
    return { data: await res.json(), error: null };
  } catch {
    return { data: null, error: "تعذر الاتصال بالخادم لتحميل لوحة المعلومات." };
  }
}
