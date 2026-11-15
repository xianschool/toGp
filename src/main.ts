import * as alphaTab from "@coderline/alphatab";
import { invoke } from "@tauri-apps/api/core";
import { open, save } from "@tauri-apps/plugin-dialog";
import { mergeScores } from "./score-merge";
import "./style.css";

interface MusicXmlFile {
  dataBase64: string;
  extension: string;
}

interface RecognitionResult {
  displayName: string;
  musicXmlFiles: MusicXmlFile[];
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
    const result = await invoke<RecognitionResult>("recognize_files", {
      inputPaths,
    });
    currentResult = result;
    const settings = new alphaTab.Settings();
    const scores = result.musicXmlFiles.map((file) =>
      alphaTab.importer.ScoreLoader.loadScoreFromBytes(
        base64ToBytes(file.dataBase64),
        settings,
      ),
    );
    api.load(mergeScores(scores, settings), [0]);
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
    defaultPath: `${sanitizeFileName(api.score.title || sourceName)}.gp`,
    filters: [{ name: "Guitar Pro 7/8", extensions: ["gp"] }],
  });
  if (!destination) {
    return;
  }

  exportButton.disabled = true;
  try {
    const bytes = new alphaTab.exporter.Gp7Exporter().export(
      api.score,
      new alphaTab.Settings(),
    );
    await invoke("export_gp", {
      destination,
      bytesBase64: bytesToBase64(bytes),
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

function bytesToBase64(bytes: Uint8Array): string {
  const chunkSize = 0x8000;
  let binary = "";
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  return btoa(binary);
}

function sanitizeFileName(value: string): string {
  const cleaned = value
    .replace(/[/:\\?%*"<>|]/g, "-")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 80);
  return cleaned || "未命名曲谱";
}
