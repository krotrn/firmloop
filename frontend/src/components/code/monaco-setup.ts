import { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor/editor/editor.api";
import "monaco-editor/languages/definitions/cpp/register.js";

let configured = false;

/**
 * Point the editor loader at the bundled package (no CDN) and give it the
 * base editor worker. Safe to call more than once.
 */
export function setupMonaco(): void {
  if (configured) return;
  configured = true;
  self.MonacoEnvironment = {
    getWorker: () =>
      new Worker(
        new URL("monaco-editor/editor/editor.worker.js", import.meta.url),
        { type: "module" }
      ),
  };
  loader.config({ monaco });
}
