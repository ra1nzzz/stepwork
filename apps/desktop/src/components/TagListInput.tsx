/**
 * 标签式列表输入（Tranche 2 复用组件）
 * 用于 BrandProfile 的内容支柱 / 禁用表达，以及发布变体的 tags：
 * 输入后回车（或逗号）追加为 chip，点击 chip 上的 × 移除。
 * 沿用 global.css 既有 .chip / .field 视觉。
 */

import { useState } from "react";

export function TagListInput({
  id,
  value,
  onChange,
  placeholder,
  disabled,
}: {
  id?: string;
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  const [draft, setDraft] = useState("");

  function commit(raw: string) {
    const items = raw
      .split(/[,，]/)
      .map((s) => s.trim())
      .filter((s) => s.length > 0 && !value.includes(s));
    if (items.length > 0) onChange([...value, ...items]);
    setDraft("");
  }

  return (
    <div>
      {value.length > 0 && (
        <div className="filters" style={{ marginBottom: 8, flexWrap: "wrap" }}>
          {value.map((tag) => (
            <span key={tag} className="chip active">
              {tag}
              <button
                type="button"
                aria-label={`移除 ${tag}`}
                onClick={() => onChange(value.filter((t) => t !== tag))}
                disabled={disabled}
                style={{
                  background: "none",
                  border: "none",
                  color: "inherit",
                  cursor: "pointer",
                  marginLeft: 6,
                  padding: 0,
                  font: "inherit",
                }}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <input
        id={id}
        className="field"
        style={{ width: "100%" }}
        value={draft}
        placeholder={placeholder ?? "输入后回车添加"}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit(draft);
          }
        }}
        onBlur={() => {
          if (draft.trim()) commit(draft);
        }}
      />
    </div>
  );
}
