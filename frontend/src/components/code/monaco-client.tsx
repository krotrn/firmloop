"use client";

import Editor, { DiffEditor } from "@monaco-editor/react";

import { setupMonaco } from "@/components/code/monaco-setup";

setupMonaco();

export { DiffEditor, Editor };
