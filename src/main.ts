import * as alphaTab from "@coderline/alphatab";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow, LogicalSize } from "@tauri-apps/api/window";
import { open, save } from "@tauri-apps/plugin-dialog";
import "./style.css";

interface RecognitionResult {
  displayName: string;
  gp5Base64: string;
  report: unknown;
}

const chooseFileButton = requiredButton("choose-file");
const chooseAnotherButton = requiredButton("choose-another");
const exportButton = requiredButton("export-gp");
const resultActions = requiredElement("result-actions");
const start = requiredElement("start");
const progressPanel = requiredElement("progress-panel");
const errorPanel = requiredElement("error-panel");
const errorMessage = requiredElement("error-message");
const workspace = requiredElement("workspace");
const aboutDialog = requiredElement<HTMLDialogElement>("about-dialog");
const aboutCloseButton = requiredButton("about-close");

let currentResult: RecognitionResult | null = null;
let busy = false;

const baseUrl = import.meta.env.BASE_URL;
const api = new alphaTab.AlphaTabApi(requiredElement("alphaTab"), {
  core: {
    fontDirectory: `${baseUrl}font/`,
    useWorkers: false,
  },
  display: {
    layoutMode: alphaTab.LayoutMode.Page,
    staveProfile: alphaTab.StaveProfile.ScoreTab,
  },
  player: {
    enablePlayer: false,
  },
});

api.scoreLoaded.on(() => {
  document.body.classList.add("has-result");
  workspace.hidden = false;
  resultActions.hidden = false;
  exportButton.disabled = false;
});

api.error.on((error) => {
  showError(error.message || "曲谱预览失败");
});

chooseFileButton.addEventListener("click", () => void chooseAndRecognize());
chooseAnotherButton.addEventListener("click", () => void chooseAndRecognize());
exportButton.addEventListener("click", () => void exportGp());
aboutCloseButton.addEventListener("click", () => aboutDialog.close());
aboutDialog.addEventListener("click", (event) => {
  if (event.target === aboutDialog) {
    aboutDialog.close();
  }
});
void listen("open-about", () => {
  if (!aboutDialog.open) {
    aboutDialog.showModal();
  }
});

async function chooseAndRecognize(): Promise<void> {
  if (busy) {
    return;
  }

  const selected = await open({
    multiple: true,
    directory: false,
    filters: [
      {
        name: "曲谱",
        extensions: ["pdf", "png", "jpg", "jpeg", "tif", "tiff"],
      },
    ],
  });
  const inputPaths = typeof selected === "string" ? [selected] : selected;
  if (!inputPaths || inputPaths.length === 0) {
    return;
  }

  busy = true;
  chooseFileButton.disabled = true;
  chooseAnotherButton.disabled = true;
  exportButton.disabled = true;
  start.hidden = true;
  errorPanel.hidden = true;
  workspace.hidden = true;
  resultActions.hidden = true;
  document.body.classList.remove("has-result");
  progressPanel.hidden = false;

  try {
    const result = await invoke<RecognitionResult>("recognize_tab_files", {
      inputPaths,
    });
    currentResult = result;
    await getCurrentWindow().setSize(new LogicalSize(1080, 760));
    api.load(base64ToBytes(result.gp5Base64), [0]);
    progressPanel.hidden = true;
  } catch (error) {
    progressPanel.hidden = true;
    start.hidden = false;
    showError(error instanceof Error ? error.message : String(error));
  } finally {
    busy = false;
    chooseFileButton.disabled = false;
    chooseAnotherButton.disabled = false;
  }
}

async function exportGp(): Promise<void> {
  if (!api.score) {
    showError("没有可导出的曲谱");
    return;
  }

  const sourceName = currentResult?.displayName || "未命名曲谱";
  const destination = await save({
    defaultPath: `${sanitizeFileName(api.score.title || sourceName)}.gp5`,
    filters: [{ name: "Guitar Pro 5", extensions: ["gp5"] }],
  });
  if (!destination) {
    return;
  }

  exportButton.disabled = true;
  try {
    if (!currentResult) {
      throw new Error("没有可导出的 GP5");
    }
    await invoke("export_gp5", {
      destination,
      bytesBase64: currentResult.gp5Base64,
    });
  } catch (error) {
    showError(error instanceof Error ? error.message : String(error));
  } finally {
    exportButton.disabled = false;
  }
}

function showError(message: string): void {
  errorMessage.textContent = message.split("\n")[0] ?? message;
  errorPanel.hidden = false;
}

function requiredElement<T extends HTMLElement = HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) {
    throw new Error(`Missing element #${id}`);
  }
  return element as T;
}

function requiredButton(id: string): HTMLButtonElement {
  return requiredElement<HTMLButtonElement>(id);
}

function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function sanitizeFileName(value: string): string {
  const cleaned = value
    .replace(/[/:\\?%*"<>|]/g, "-")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 80);
  return cleaned || "未命名曲谱";
}
