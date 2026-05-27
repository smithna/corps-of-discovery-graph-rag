"use client";

interface Props {
  agent: boolean;
  onChange: (val: boolean) => void;
  disabled?: boolean;
}

export default function GraphToggle({ agent, onChange, disabled }: Props) {
  return (
    <div className="flex items-center gap-3">
      <span className={`text-sm font-medium transition-colors ${!agent ? "text-white" : "text-gray-500"}`}>
        Vector RAG
      </span>

      <button
        onClick={() => !disabled && onChange(!agent)}
        disabled={disabled}
        aria-pressed={agent}
        className={`
          relative inline-flex h-7 w-14 items-center rounded-full transition-colors duration-200
          focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-neo-dark
          ${disabled ? "opacity-40 cursor-not-allowed" : "cursor-pointer"}
          ${agent ? "bg-neo-green focus-visible:ring-neo-green" : "bg-gray-600 focus-visible:ring-gray-400"}
        `}
      >
        <span
          className={`
            inline-block h-5 w-5 rounded-full bg-white shadow-md transform transition-transform duration-200
            ${agent ? "translate-x-8" : "translate-x-1"}
          `}
        />
      </button>

      <span className={`text-sm font-medium transition-colors ${agent ? "text-neo-green" : "text-gray-500"}`}>
        Graph Agent
      </span>
    </div>
  );
}
