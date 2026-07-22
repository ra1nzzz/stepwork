/**
 * 脚本编辑器（W5 Batch1）
 * - TipTap（StarterKit）+ 防抖自动保存（SaveScript → 新建版本并串 parent 链）
 * - 生成脚本后由 store.seedBody 注入正文，编辑器转为 TipTap doc
 */

import { useEffect, useRef } from "react";
import { useEditor, EditorContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import type { JSONContent } from "@tiptap/core";
import { useScriptStore } from "@/stores/useScriptStore";

/** 纯文本 → TipTap doc（按空行/换行分段） */
function textToDoc(text: string): JSONContent {
  const paras = text
    .split(/\n+/)
    .map((t) => t.trim())
    .filter((t) => t.length > 0);
  const content: JSONContent[] = paras.length
    ? paras.map((t) => ({
        type: "paragraph",
        content: [{ type: "text", text: t }],
      }))
    : [{ type: "paragraph" }];
  return { type: "doc", content };
}

export function ScriptEditor() {
  const seedBody = useScriptStore((s) => s.seedBody);
  const scriptTitle = useScriptStore((s) => s.scriptTitle);
  const setScriptTitle = useScriptStore((s) => s.setScriptTitle);
  const saveScript = useScriptStore((s) => s.saveScript);

  const appliedSeed = useRef<string | null>(null);
  const skipNextUpdate = useRef(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const editor = useEditor({
    extensions: [StarterKit],
    content: textToDoc(""),
    editorProps: { attributes: { class: "script-editor" } },
    onUpdate: ({ editor }) => {
      if (skipNextUpdate.current) {
        skipNextUpdate.current = false;
        return;
      }
      if (saveTimer.current) clearTimeout(saveTimer.current);
      const doc = editor.getJSON();
      const title = useScriptStore.getState().scriptTitle;
      const payload: Record<string, unknown> = { title, doc };
      saveTimer.current = setTimeout(() => {
        void saveScript(payload);
      }, 800);
    },
  });

  useEffect(() => {
    if (!editor || seedBody == null) return;
    if (seedBody === appliedSeed.current) return;
    skipNextUpdate.current = true;
    editor.commands.setContent(textToDoc(seedBody));
    appliedSeed.current = seedBody;
  }, [editor, seedBody]);

  return (
    <section className="feature-view" data-od-id="script-editor-view">
      <header className="feature-head">
        <h2>脚本编辑器</h2>
        <p className="feature-sub">自动保存（每 0.8s 防抖 → 新版本入链）</p>
      </header>

      <label className="script-title-input">
        标题
        <input
          value={scriptTitle}
          onChange={(e) => setScriptTitle(e.target.value)}
          placeholder="脚本标题"
        />
      </label>

      <EditorContent editor={editor} />
    </section>
  );
}
