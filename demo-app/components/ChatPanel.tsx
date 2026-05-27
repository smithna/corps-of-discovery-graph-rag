"use client";

import { useEffect, useRef } from "react";

export interface Message {
  role: "user" | "assistant";
  content: string;
  useGraph?: boolean;
}

interface Props {
  messages: Message[];
  streaming: string;
}

export default function ChatPanel({ messages, streaming }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streaming]);

  return (
    <div className="flex flex-col gap-4 h-full overflow-y-auto px-4 py-4">
      {messages.map((msg, i) => (
        <div
          key={i}
          className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
        >
          <div
            className={`
              max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed
              ${msg.role === "user"
                ? "bg-neo-blue text-white rounded-br-sm"
                : "bg-neo-panel border border-neo-border text-gray-200 rounded-bl-sm"
              }
            `}
          >
            {msg.role === "assistant" && msg.useGraph && (
              <div className="text-xs text-neo-green mb-1.5 font-medium">
                Vector + Graph
              </div>
            )}
            {msg.role === "assistant" && msg.useGraph === false && (
              <div className="text-xs text-gray-500 mb-1.5 font-medium">
                Vector only
              </div>
            )}
            <p className="whitespace-pre-wrap">{msg.content}</p>
          </div>
        </div>
      ))}

      {/* Streaming bubble */}
      {streaming && (
        <div className="flex justify-start">
          <div className="max-w-[85%] rounded-2xl rounded-bl-sm px-4 py-3 text-sm leading-relaxed bg-neo-panel border border-neo-border text-gray-200">
            <p className="whitespace-pre-wrap">{streaming}<span className="inline-block w-0.5 h-4 bg-neo-blue ml-0.5 animate-pulse align-middle" /></p>
          </div>
        </div>
      )}

      {!messages.length && !streaming && (
        <div className="flex-1 flex items-center justify-center text-gray-600 text-sm">
          Ask a question about the Lewis &amp; Clark Expedition.
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
