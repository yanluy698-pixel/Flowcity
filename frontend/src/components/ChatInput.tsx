import { FormEvent, useEffect, useState } from "react";
import type { ModifyDraft } from "../types";

type Props = {
  onSubmit: (text: string) => void;
  disabled?: boolean;
  placeholder?: string;
  draft?: ModifyDraft;
  onClearDraft?: () => void;
  showDraftPreview?: boolean;
};

export function ChatInput({ onSubmit, disabled, placeholder, draft, onClearDraft, showDraftPreview = true }: Props) {
  const [value, setValue] = useState("");

  useEffect(() => {
    if (draft) {
      setValue(draft.prefillInput || !draft.systemPrompt ? draft.suggestion : "");
    }
  }, [draft]);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const text = value.trim();
    if (!text || disabled) return;
    onSubmit(text);
    onClearDraft?.();
    setValue("");
  }

  return (
    <div className="chat-input-wrap">
      {draft && showDraftPreview && (
        <div className="draft-chip">
          <span className="draft-pill">{draft.label}</span>
          <button type="button" onClick={onClearDraft} aria-label="取消修改">
            ×
          </button>
        </div>
      )}
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
    </div>
  );
}
