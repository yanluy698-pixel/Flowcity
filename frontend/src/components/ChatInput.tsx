import { FormEvent, useEffect, useState } from "react";

type Props = {
  onSubmit: (text: string) => void;
  disabled?: boolean;
  placeholder?: string;
  draftText?: string;
};

export function ChatInput({ onSubmit, disabled, placeholder, draftText }: Props) {
  const [value, setValue] = useState("");

  useEffect(() => {
    if (draftText !== undefined) {
      setValue(draftText);
    }
  }, [draftText]);

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
        发送
      </button>
    </form>
  );
}
