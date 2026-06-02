import { SendHorizonal } from "lucide-react";
import { FormEvent, useState } from "react";

type Props = {
  onSubmit: (text: string) => void;
  disabled?: boolean;
  placeholder?: string;
};

export function ChatInput({ onSubmit, disabled, placeholder }: Props) {
  const [value, setValue] = useState("");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const text = value.trim();
    if (!text || disabled) return;
    onSubmit(text);
    setValue("");
  }

  return (
    <form className="chat-input" onSubmit={handleSubmit}>
      <input
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder={placeholder ?? "说说你的周末安排..."}
        disabled={disabled}
      />
      <button type="submit" disabled={disabled || !value.trim()} aria-label="发送">
        <SendHorizonal size={20} />
      </button>
    </form>
  );
}
