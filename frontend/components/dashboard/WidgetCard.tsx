import { ReactNode } from "react";
import { LucideIcon } from "lucide-react";

export default function WidgetCard({
  title,
  icon: Icon,
  children,
}: {
  title: string;
  icon: LucideIcon;
  children: ReactNode;
}) {
  return (
    <section className="widget-card glass rounded-2xl p-4 flex flex-col gap-3 min-w-0">
      <header className="flex items-center gap-2 text-sm font-medium text-muted">
        <Icon size={16} className="text-[color:var(--accent-2)]" />
        <h2>{title}</h2>
      </header>
      <div className="flex flex-col gap-2 min-w-0">{children}</div>
    </section>
  );
}
