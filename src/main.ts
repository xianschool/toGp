import * as alphaTab from "@coderline/alphatab";
import { invoke } from "@tauri-apps/api/core";
import { open, save } from "@tauri-apps/plugin-dialog";
import "./style.css";

interface RecognitionResult {
  displayName: string;
  musicXmlBase64: string;
  musicXmlExtension: string;
}

const chooseFileButton = requiredButton("choose-file");
const chooseAnotherButton = requiredButton("choose-another");
const openAudiverisButton = requiredButton("open-audiveris");
const retryAudiverisButton = requiredButton("retry-audiveris");
const openLicenseButton = requiredButton("open-license");
const exportButton = requiredButton("export-gp");
const playPauseButton = requiredButton("play-pause");
const playIcon = requiredElement("play-icon");
const progressPanel = requiredElement("progress-panel");
const progressTitle = requiredElement("progress-title");
const progressDetail = requiredElement("progress-detail");
const errorPanel = requiredElement("error-panel");
const errorMessage = requiredElement("error-message");
const workspace = requiredElement("workspace");
const scoreMeta = requiredElement("score-meta");
const speedInput = requiredElement<HTMLInputElement>("speed");
const speedLabel = requiredElement<HTMLOutputElement>("speed-label");
const playbackTime = requiredElement("playback-time");
const scoreScroll = requiredElement("score-scroll");

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
    enablePlayer: true,
    soundFont: `${baseUrl}soundfont/sonivox.sf2`,
    outputMode: alphaTab.PlayerOutputMode.WebAudioScriptProcessor,
    scrollElement: scoreScroll,
  },
});

api.scoreLoaded.on((score) => {
  document.body.classList.add("has-result");
  workspace.hidden = false;
  exportButton.disabled = false;

  const title = score.title.trim() || currentResult?.displayName || "未命名曲谱";
  const artist = score.artist.trim();
  const trackCount = score.tracks.length;
  const barCount = score.masterBars.length;
  scoreMeta.textContent = [
    title,
    artist || null,
    `${trackCount} 条音轨`,
    `${barCount} 小节`,
  ]
    .filter(Boolean)
    .join(" · ");
});

api.playerStateChanged.on(({ state }) => {
  const playing = state === alphaTab.synth.PlayerState.Playing;
  playIcon.textContent = playing ? "Ⅱ" : "▶";
  playPauseButton.setAttribute("aria-label", playing ? "暂停" : "播放");
});

api.playerPositionChanged.on(({ currentTime }) => {
  playbackTime.textContent = formatDuration(currentTime);
});

api.error.on((error) => {
  showError(`曲谱预览失败：${error.message || error.stack || String(error)}`);
});

chooseFileButton.addEventListener("click", () => void chooseAndRecognize());
chooseAnotherButton.addEventListener("click", () => void chooseAndRecognize());
openAudiverisButton.addEventListener("click", () => void openAudiveris());
retryAudiverisButton.addEventListener("click", () => void openAudiveris());
openLicenseButton.addEventListener("click", () => void openLicense());
exportButton.addEventListener("click", () => void exportGp());
playPauseButton.addEventListener("click", () => api.playPause());

speedInput.addEventListener("input", () => {
  const speed = Number(speedInput.value);
  speedLabel.value = `${speed}%`;
  api.playbackSpeed = speed / 100;
});

async function chooseAndRecognize(): Promise<void> {
  if (busy) {
    return;
  }

  const selected = await open({
    multiple: false,
    directory: false,
    filters: [
      {
        name: "曲谱",
        extensions: ["pdf", "png", "jpg", "jpeg", "tif", "tiff"],
      },
    ],
  });

  if (typeof selected !== "string") {
    return;
  }

  busy = true;
  document.body.classList.remove("has-result");
  setButtonsDisabled(true);
  errorPanel.hidden = true;
  progressPanel.hidden = false;
  progressTitle.textContent = "正在识别曲谱";
  progressDetail.textContent = "文件只在本机处理。识别完成后会自动生成预览。";

  try {
    const result = await invoke<RecognitionResult>("recognize_file", {
      inputPath: selected,
    });
    currentResult = result;
    progressTitle.textContent = "正在载入识别结果";
    progressDetail.textContent = `${result.displayName} · MusicXML`;

    const bytes = base64ToBytes(result.musicXmlBase64);
    api.load(bytes, [0]);
    progressPanel.hidden = true;
  } catch (error) {
    progressPanel.hidden = true;
    showError(errorMessageFrom(error));
  } finally {
    busy = false;
    setButtonsDisabled(false);
  }
}

async function exportGp(): Promise<void> {
  if (!api.score) {
    showError("还没有可以导出的曲谱。");
    return;
  }

  const sourceName = currentResult?.displayName.replace(/\.[^.]+$/, "") || "未命名曲谱";
  const baseName = sanitizeFileName(api.score.title || sourceName);
  const destination = await save({
    defaultPath: `${baseName}.gp`,
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
    const savedName = destination.split(/[\\/]/).pop() || `${baseName}.gp`;
    scoreMeta.textContent = `${scoreMeta.textContent ?? ""} · 已导出 ${savedName}`;
  } catch (error) {
    showError(`导出失败：${errorMessageFrom(error)}`);
  } finally {
    exportButton.disabled = false;
  }
}

async function openAudiveris(): Promise<void> {
  try {
    await invoke("open_audiveris");
  } catch (error) {
    showError(`无法启动 Audiveris：${errorMessageFrom(error)}`);
  }
}

async function openLicense(): Promise<void> {
  try {
    await invoke("open_license");
  } catch (error) {
    showError(`无法打开许可证：${errorMessageFrom(error)}`);
  }
}

function showError(message: string): void {
  errorMessage.textContent = message;
  errorPanel.hidden = false;
}

function setButtonsDisabled(disabled: boolean): void {
  chooseFileButton.disabled = disabled;
  chooseAnotherButton.disabled = disabled;
  openAudiverisButton.disabled = disabled;
  retryAudiverisButton.disabled = disabled;
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

function errorMessageFrom(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function formatDuration(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}
