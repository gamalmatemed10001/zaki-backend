import { Mail } from "lucide-react";
import WidgetCard from "./WidgetCard";
import { EmailItem } from "@/lib/dashboard";

export default function EmailWidget({
  emails,
  googleConnected,
}: {
  emails: EmailItem[] | null;
  googleConnected: boolean;
}) {
  return (
    <WidgetCard title="البريد الإلكتروني" icon={Mail}>
      {!googleConnected && (
        <p className="text-xs text-muted">لم يتم ربط حساب جوجل بعد.</p>
      )}
      {googleConnected && (!emails || emails.length === 0) && (
        <p className="text-xs text-muted">لا توجد رسائل حديثة.</p>
      )}
      {emails?.map((e) => (
        <div key={e.id} className="flex flex-col gap-0.5 text-sm min-w-0">
          <div className="flex items-baseline justify-between gap-2">
            <span className="truncate font-medium">{e.subject}</span>
          </div>
          <span className="text-xs text-muted truncate">{e.from}</span>
        </div>
      ))}
    </WidgetCard>
  );
}
