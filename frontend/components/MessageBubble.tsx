"use client";

import { motion } from "motion/react";

export interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
}

export default function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      className={`flex ${isUser ? "justify-start" : "justify-end"}`}
    >
      <div
        className={`max-w-[80%] rounded-2xl px-4 py-2.5 text-[15px] leading-relaxed glass ${
          isUser ? "rounded-ss-sm" : "rounded-se-sm"
        }`}
        style={{
          background: isUser
            ? "rgba(124, 92, 255, 0.16)"
            : "rgba(255, 255, 255, 0.06)",
        }}
      >
        {message.text}
      </div>
    </motion.div>
  );
}
