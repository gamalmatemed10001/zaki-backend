import { ListTodo } from "lucide-react";
import WidgetCard from "./WidgetCard";
import { TaskItem } from "@/lib/dashboard";

export default function TasksWidget({ tasks }: { tasks: TaskItem[] }) {
  const pending = tasks.filter((t) => !t.done);
  const done = tasks.filter((t) => t.done);

  return (
    <WidgetCard title="المهام" icon={ListTodo}>
      {tasks.length === 0 && <p className="text-xs text-muted">لا توجد مهام بعد.</p>}

      {pending.map((task) => (
        <div key={task.id} className="task-row flex items-center gap-2 text-sm">
          <span className="task-checkbox" aria-hidden />
          <span className="truncate">{task.title}</span>
        </div>
      ))}

      {done.length > 0 && (
        <div className="pt-1 mt-1 border-t border-white/10 flex flex-col gap-2">
          {done.slice(0, 3).map((task) => (
            <div key={task.id} className="task-row flex items-center gap-2 text-sm opacity-50">
              <span className="task-checkbox is-done" aria-hidden />
              <span className="truncate line-through">{task.title}</span>
            </div>
          ))}
        </div>
      )}
    </WidgetCard>
  );
}
