"use client";

import { useState, KeyboardEvent } from "react";
import { NotebookText, Search } from "lucide-react";
import WidgetCard from "./WidgetCard";
import { NoteItem } from "@/lib/dashboard";

export default function NotesSearchWidget({
  notes,
  onSearch,
}: {
  notes: NoteItem[];
  onSearch: (query: string) => void;
}) {
  const [query, setQuery] = useState("");

  const submit = () => {
    const q = query.trim();
    if (!q) return;
    onSearch(q);
    setQuery("");
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") submit();
  };

  return (
    <WidgetCard title="الملاحظات والبحث" icon={NotebookText}>
      <div className="flex items-center gap-2 rounded-lg bg-white/5 px-2.5 py-1.5">
        <Search size={14} className="text-muted shrink-0" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="ابحث في الإنترنت..."
          dir="rtl"
          className="flex-1 bg-transparent outline-none text-xs placeholder:text-muted min-w-0"
        />
      </div>

      {notes.length === 0 && <p className="text-xs text-muted">لا توجد ملاحظات بعد.</p>}
      {notes.map((n) => (
        <p key={n.id} className="text-sm line-clamp-2">
          {n.content}
        </p>
      ))}
    </WidgetCard>
  );
}
