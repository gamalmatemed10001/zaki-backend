import { CalendarDays } from "lucide-react";
import WidgetCard from "./WidgetCard";
import { CalendarEvent } from "@/lib/dashboard";

function formatWhen(iso: string) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ar", { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

export default function CalendarWidget({
  events,
  googleConnected,
}: {
  events: CalendarEvent[] | null;
  googleConnected: boolean;
}) {
  return (
    <WidgetCard title="المواعيد" icon={CalendarDays}>
      {!googleConnected && (
        <p className="text-xs text-muted">لم يتم ربط حساب جوجل بعد.</p>
      )}
      {googleConnected && (!events || events.length === 0) && (
        <p className="text-xs text-muted">لا توجد مواعيد قادمة.</p>
      )}
      {events?.map((e) => (
        <div key={e.id} className="flex flex-col gap-0.5 text-sm">
          <span className="truncate">{e.summary}</span>
          <span className="text-xs text-muted">{formatWhen(e.start)}</span>
        </div>
      ))}
    </WidgetCard>
  );
}
