"use client";

import dynamic from "next/dynamic";

import { EDITOR_HEIGHT, EditorFallback } from "@/components/code/code-view";
import { useEditorTheme } from "@/components/code/use-editor-theme";
import { useNarrowScreen } from "@/components/code/use-narrow-screen";

const DiffEditor = dynamic(
  () => import("@/components/code/monaco-client").then((m) => m.DiffEditor),
  { ssr: false, loading: () => <EditorFallback height={EDITOR_HEIGHT} /> }
);

export interface CodeDiffProps {
  original: string;
  modified: string;
  /** Heading over the left (original) side, for example "v4". */
  originalLabel: string;
  /** Heading over the right (modified) side, for example "Attempt 2". */
  modifiedLabel: string;
  height?: string;
}

export function CodeDiff({
  original,
  modified,
  originalLabel,
  modifiedLabel,
  height = EDITOR_HEIGHT,
}: CodeDiffProps) {
  const theme = useEditorTheme();
  const narrow = useNarrowScreen();
  return (
    <div className="flex flex-col gap-1.5">
      <div className="text-muted-foreground flex justify-between gap-4 text-xs font-medium">
        <span>{originalLabel}</span>
        <span>{modifiedLabel}</span>
      </div>
      <div className="border-input overflow-hidden rounded-lg border">
        <DiffEditor
          height={height}
          language="cpp"
          theme={theme}
          original={original}
          modified={modified}
          options={{
            readOnly: true,
            originalAriaLabel: originalLabel,
            modifiedAriaLabel: modifiedLabel,
            originalEditable: false,
            renderSideBySide: !narrow,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            wordWrap: "on",
            automaticLayout: true,
            fontSize: 13,
          }}
        />
      </div>
    </div>
  );
}
