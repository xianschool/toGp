import * as alphaTab from "@coderline/alphatab";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow, LogicalSize } from "@tauri-apps/api/window";
import { open, save } from "@tauri-apps/plugin-dialog";
import "./style.css";

interface RecognitionResult {
  displayName: string;
  gp5Base64: string;
  report: {
    title?: string;
    artist?: string;
    tempo?: number;
    capo?: number;
    measures?: number;
    sourceProfile?: string;
    sourceAdapter?: {
      id?: string;
      displayName?: string;
      reason?: string;
      sourceFamily?: string;
      sourceConfidence?: number;
      renderType?: string;
    };
    sourceDiagnosis?: {
      renderType?: string;
      sourceCandidates?: Array<{
        family?: string;
        confidence?: number;
      }>;
    };
    chordAnalysis?: {
      labels?: number;
      assignedLabels?: number;
      unmatchedLabels?: number;
      convertedNotes?: number;
    };
    tabScore?: {
      source?: {
        renderType?: string;
      };
      totals?: {
        pages?: number;
        systems?: number;
        measures?: number;
        events?: number;
      };
      validation?: {
        status?: string;
        issues?: Array<{
          message?: string;
        }>;
      };
    };
    tracks?: Array<{
      strings?: number;
      notes?: number;
    }>;
    warnings?: string[];
  };
}

const chooseFileButton = requiredButton("choose-file");
const chooseAnotherButton = requiredButton("choose-another");
const exportButton = requiredButton("export-gp");
const resultActions = requiredElement("result-actions");
const start = requiredElement("start");
const progressPanel = requiredElement("progress-panel");
const progressLabel = requiredElement("progress-label");
const cancelRecognitionButton = requiredButton("cancel-recognition");
const errorPanel = requiredElement("error-panel");
const errorMessage = requiredElement("error-message");
const workspace = requiredElement("workspace");
const dropOverlay = requiredElement("drop-overlay");
const resultSummary = requiredElement("result-summary");
const resultStats = requiredElement("result-stats");
const resultWarnings = requiredElement<HTMLDetailsElement>("result-warnings");
const resultWarningSummary = requiredElement("result-warning-summary");
const resultWarningList = requiredElement("result-warning-list");
const aboutDialog = requiredElement<HTMLDialogElement>("about-dialog");
const aboutCloseButton = requiredButton("about-close");

let currentResult: RecognitionResult | null = null;
let busy = false;
let cancelling = false;
const appWindow = getCurrentWindow();

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

api.scoreLoaded.on((score) => {
  let metadataChanged = false;
  if (currentResult?.report.title) {
    score.title = currentResult.report.title;
    metadataChanged = true;
  }
  if (currentResult?.report.artist) {
    score.artist = currentResult.report.artist;
    metadataChanged = true;
  }
  document.body.classList.add("has-result");
  renderRecognitionSummary(currentResult?.report);
  workspace.hidden = false;
  resultActions.hidden = false;
  exportButton.disabled = false;
  if (metadataChanged) {
    api.render();
  }
});

api.error.on((error) => {
  showError(error.message || "曲谱预览失败");
});

chooseFileButton.addEventListener("click", () => void chooseAndRecognize());
chooseAnotherButton.addEventListener("click", () => void chooseAndRecognize());
cancelRecognitionButton.addEventListener("click", () => void cancelRecognition());
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
void appWindow.onDragDropEvent((event) => {
  const payload = event.payload;
  if (payload.type === "enter") {
    dropOverlay.hidden = false;
    return;
  }
  if (payload.type === "leave") {
    dropOverlay.hidden = true;
    return;
  }
  if (payload.type === "drop") {
    dropOverlay.hidden = true;
    if (payload.paths.length > 0) {
      void recognizePaths(payload.paths);
    }
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
  await recognizePaths(inputPaths);
}

async function recognizePaths(inputPaths: string[]): Promise<void> {
  if (busy) {
    return;
  }
  busy = true;
  cancelling = false;
  chooseFileButton.disabled = true;
  chooseAnotherButton.disabled = true;
  exportButton.disabled = true;
  cancelRecognitionButton.disabled = false;
  progressLabel.textContent = "识别中…";
  start.hidden = true;
  errorPanel.hidden = true;
  workspace.hidden = true;
  resultSummary.hidden = true;
  resultActions.hidden = true;
  document.body.classList.remove("has-result");
  progressPanel.hidden = false;

  try {
    const result = await invoke<RecognitionResult>("recognize_tab_files", {
      inputPaths,
    });
    currentResult = result;
    await appWindow.setSize(new LogicalSize(1080, 760));
    api.load(base64ToBytes(result.gp5Base64), [0]);
    progressPanel.hidden = true;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    progressPanel.hidden = true;
    resultSummary.hidden = true;
    start.hidden = false;
    if (!message.includes("识别已取消")) {
      showError(message);
    }
  } finally {
    busy = false;
    cancelling = false;
    cancelRecognitionButton.disabled = false;
    progressLabel.textContent = "识别中…";
    chooseFileButton.disabled = false;
    chooseAnotherButton.disabled = false;
  }
}

async function cancelRecognition(): Promise<void> {
  if (!busy || cancelling) {
    return;
  }
  cancelling = true;
  cancelRecognitionButton.disabled = true;
  progressLabel.textContent = "正在取消…";
  try {
    const requested = await invoke<boolean>("cancel_tab_recognition");
    if (!requested && busy) {
      cancelling = false;
      cancelRecognitionButton.disabled = false;
      progressLabel.textContent = "识别中…";
    }
  } catch (error) {
    cancelling = false;
    cancelRecognitionButton.disabled = false;
    progressLabel.textContent = "识别中…";
    showError(`无法取消识别：${error instanceof Error ? error.message : String(error)}`);
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

function renderRecognitionSummary(report: RecognitionResult["report"] | undefined): void {
  if (!report) {
    resultSummary.hidden = true;
    return;
  }

  const track = report.tracks?.[0];
  const stats = [
    track?.strings ? `${track.strings} 弦` : "",
    report.measures ? `${report.measures} 小节` : "",
    report.tabScore?.totals?.systems
      ? `${report.tabScore.totals.systems} 谱表`
      : "",
    report.tempo ? `${report.tempo} BPM` : "",
    report.capo ? `Capo ${report.capo}` : "",
    report.chordAnalysis?.labels ? `${report.chordAnalysis.labels} 个和弦` : "",
    report.chordAnalysis?.convertedNotes
      ? `${report.chordAnalysis.convertedNotes} 个 X 已补品位`
      : "",
    adapterLabel(report),
    sourceFamilyLabel(report),
  ].filter(Boolean);

  resultStats.replaceChildren(
    ...stats.map((text) => {
      const item = document.createElement("span");
      item.className = "result-chip";
      item.textContent = text;
      return item;
    }),
  );

  const validationWarnings = (report.tabScore?.validation?.issues ?? [])
    .map((issue) => issue.message ?? "")
    .filter((warning) => warning.trim());
  const warnings = Array.from(
    new Set(
      [...(report.warnings ?? []), ...validationWarnings].filter((warning) =>
        warning.trim(),
      ),
    ),
  );
  resultWarningList.replaceChildren(
    ...warnings.map((warning) => {
      const item = document.createElement("li");
      item.textContent = formatWarning(warning);
      return item;
    }),
  );
  resultWarningSummary.textContent = `识别提示 · ${warnings.length} 条`;
  resultWarnings.hidden = warnings.length === 0;
  resultWarnings.open = false;
  resultSummary.hidden = false;
}

function adapterLabel(report: RecognitionResult["report"]): string {
  return report.sourceAdapter?.displayName || profileLabel(report.sourceProfile);
}

function sourceFamilyLabel(report: RecognitionResult["report"]): string {
  const adapter = report.sourceAdapter;
  const candidate = report.sourceDiagnosis?.sourceCandidates?.find(
    (item) => (item.confidence ?? 0) >= 0.7,
  );
  const family = adapter?.sourceFamily || candidate?.family;
  const confidence = adapter?.sourceConfidence ?? candidate?.confidence ?? 0;
  if (!family || confidence < 0.7) {
    return "";
  }
  const labels: Record<string, string> = {
    musescore: "MuseScore 来源",
    "guitar-pro": "Guitar Pro 来源",
    dorico: "Dorico 来源",
    sibelius: "Sibelius 来源",
    finale: "Finale 来源",
    lilypond: "LilyPond 来源",
    "digital-tab-screenshot": "数字六线谱截图",
    "scanned-publication": "扫描出版物",
  };
  return labels[family] ?? "";
}

function profileLabel(profile: string | undefined): string {
  if (profile === "monochrome-watermark-tab") {
    return "黑白水印六线谱";
  }
  if (profile === "red-chord-tab") {
    return "红色和弦数字谱";
  }
  if (profile === "musescore-vector-tab") {
    return "MuseScore 矢量六线谱";
  }
  if (profile === "generic") {
    return "通用识别";
  }
  return "";
}

function formatWarning(warning: string): string {
  if (warning.startsWith("Tablature read from an image (OCR)")) {
    return "这是从图片识别的六线谱，请核对音符、节奏和演奏技巧。";
  }
  const rhythm = warning.match(
    /^Rhythm read from the score in (\d+) bar\(s\); estimated from the spacing of the notes in bars (.+): check them before trusting the playback\.$/,
  );
  if (rhythm) {
    return `只有 ${rhythm[1]} 小节读取到明确节奏；第 ${rhythm[2]} 小节按音符间距估算时值，播放前请核对。`;
  }
  const missing = warning.match(
    /^Bars not found in the PDF, filled with rests: (.+)\.$/,
  );
  if (missing) {
    return `第 ${missing[1]} 小节没有从谱面中找到，已用休止符填充。`;
  }
  const strings = warning.match(
    /^(\d+) tab line\(s\) with a number of strings other than (\d+) were ignored\.$/,
  );
  if (strings) {
    return `忽略了 ${strings[1]} 条弦数与主谱表（${strings[2]} 弦）不一致的谱线。`;
  }
  return warning;
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
