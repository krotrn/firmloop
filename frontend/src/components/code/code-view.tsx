"use client";

import dynamic from "next/dynamic";

import { useEditorTheme } from "@/components/code/use-editor-theme";
import { Skeleton } from "@/components/ui/skeleton";

export const EDITOR_HEIGHT = "min(60vh, 28rem)";

export function EditorFallback({ height }: { height: string }) {
  return <Skeleton className="w-full" style={{ height }} />;
}

// Monaco touches `window` on import, so it is only ever loaded in the browser.
const Editor = dynamic(
  () => import("@/components/code/monaco-client").then((m) => m.Editor),
  { ssr: false, loading: () => <EditorFallback height={EDITOR_HEIGHT} /> }
);

export interface CodeViewProps {
  value: string;
  /** Accessible name of the editor. */
  label: string;
  /** Makes the editor editable; the new text arrives here. */
  onChange?: (value: string) => void;
  /** CSS height; the default fits a 360 px wide screen. */
  height?: string;
}

export function CodeView({
  value,
  label,
  onChange,
  height = EDITOR_HEIGHT,
}: CodeViewProps) {
  const theme = useEditorTheme();
  const editable = onChange !== undefined;
  return (
    <div className="border-input overflow-hidden rounded-lg border">
      <Editor
        height={height}
        language="cpp"
        theme={theme}
        value={value}
        onChange={(next) => onChange?.(next ?? "")}
        options={{
          readOnly: !editable,
          ariaLabel: label,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          wordWrap: "on",
          automaticLayout: true,
          fontSize: 13,
          tabSize: 2,
        }}
      />
    </div>
  );
}
