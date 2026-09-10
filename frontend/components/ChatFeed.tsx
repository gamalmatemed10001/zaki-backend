"use client";

import { useEffect, useRef } from "react";
import MessageBubble, { Message } from "./MessageBubble";

export default function ChatFeed({ messages }: { messages: Message[] }) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6 flex flex-col gap-3">
      {messages.length === 0 && (
        <p className="text-muted text-center mt-12 text-sm">
          ابدأ محادثة مع زكي — اكتب أو تحدث
        </p>
      )}
      {messages.map((m) => (
        <MessageBubble key={m.id} message={m} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
