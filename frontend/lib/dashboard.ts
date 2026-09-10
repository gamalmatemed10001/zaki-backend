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

export async function fetchDashboard(): Promise<DashboardData | null> {
  try {
    const res = await fetch("/api/dashboard", { cache: "no-store" });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}
