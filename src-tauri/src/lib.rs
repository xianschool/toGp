use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use serde::Serialize;
use std::{
    fs,
    path::{Path, PathBuf},
    process::Stdio,
    time::Duration,
};
use tauri::{AppHandle, Manager};
use tokio::process::Command;
use uuid::Uuid;

const MAX_INPUT_BYTES: u64 = 100 * 1024 * 1024;
const RECOGNITION_TIMEOUT: Duration = Duration::from_secs(20 * 60);

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RecognitionResult {
    display_name: String,
    music_xml_base64: String,
    music_xml_extension: String,
}

#[tauri::command]
async fn recognize_file(
    app: AppHandle,
    input_path: String,
) -> Result<RecognitionResult, String> {
    let input = PathBuf::from(&input_path);
    let extension = validate_input(&input)?;
    let executable = find_audiveris(&app)?;
    let tessdata = find_tessdata(&app);
    let display_name = input
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("未命名曲谱")
        .to_owned();

    let work_dir = std::env::temp_dir().join(format!("togpx-{}", Uuid::new_v4()));
    tokio::fs::create_dir_all(&work_dir)
        .await
        .map_err(|error| format!("无法创建临时目录：{error}"))?;

    let result = recognize_in_workspace(
        &executable,
        &input,
        &extension,
        &work_dir,
        display_name,
        tessdata.as_deref(),
    )
    .await;
    let _ = tokio::fs::remove_dir_all(&work_dir).await;
    result
}

async fn recognize_in_workspace(
    executable: &Path,
    input: &Path,
    extension: &str,
    work_dir: &Path,
    display_name: String,
    tessdata: Option<&Path>,
) -> Result<RecognitionResult, String> {
    let copied_input = work_dir.join(format!("input.{extension}"));
    let output_dir = work_dir.join("output");

    tokio::fs::copy(input, &copied_input)
        .await
        .map_err(|error| format!("无法读取所选曲谱：{error}"))?;
    tokio::fs::create_dir_all(&output_dir)
        .await
        .map_err(|error| format!("无法创建识别输出目录：{error}"))?;

    let mut command = Command::new(executable);
    command
        .current_dir(work_dir)
        .arg("-batch")
        .arg("-transcribe")
        .arg("-export")
        .arg("-constant")
        .arg("org.audiveris.omr.text.Language.defaultSpecification=eng+chi_sim")
        .arg("-output")
        .arg(&output_dir)
        .arg("--")
        .arg(&copied_input)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    if let Some(tessdata) = tessdata {
        command.env("TESSDATA_PREFIX", tessdata);
    }

    let child = command
        .spawn()
        .map_err(|error| format!("无法启动本地识别引擎：{error}"))?;

    let output = tokio::time::timeout(RECOGNITION_TIMEOUT, child.wait_with_output())
        .await
        .map_err(|_| "识别超时，请检查 Audiveris 是否能正常打开这份曲谱".to_owned())?
        .map_err(|error| format!("识别进程失败：{error}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let detail = stderr.trim();
        return Err(if detail.is_empty() {
            format!("识别引擎退出，状态码 {:?}", output.status.code())
        } else {
            format!("识别引擎退出：{}", truncate(detail, 1200))
        });
    }

    let music_xml = find_music_xml(&output_dir).await?;
    let bytes = tokio::fs::read(&music_xml)
        .await
        .map_err(|error| format!("无法读取识别结果：{error}"))?;
    let music_xml_extension = music_xml
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("musicxml")
        .to_ascii_lowercase();

    Ok(RecognitionResult {
        display_name,
        music_xml_base64: BASE64.encode(bytes),
        music_xml_extension,
    })
}

#[tauri::command]
async fn export_gp(destination: String, bytes_base64: String) -> Result<(), String> {
    let destination = PathBuf::from(destination);
    if !destination
        .extension()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value.eq_ignore_ascii_case("gp"))
    {
        return Err("导出文件必须使用 .gp 扩展名".to_owned());
    }

    let parent = destination
        .parent()
        .ok_or_else(|| "无法确定导出目录".to_owned())?;
    if !parent.is_dir() {
        return Err("导出目录不存在".to_owned());
    }

    let bytes = BASE64
        .decode(bytes_base64)
        .map_err(|error| format!("导出数据无效：{error}"))?;
    if bytes.is_empty() {
        return Err("导出文件为空".to_owned());
    }

    let temporary = parent.join(format!(".togpx-{}.tmp", Uuid::new_v4()));
    tokio::fs::write(&temporary, bytes)
        .await
        .map_err(|error| format!("无法写入导出文件：{error}"))?;
    tokio::fs::rename(&temporary, &destination)
        .await
        .map_err(|error| {
            let _ = std::fs::remove_file(&temporary);
            format!("无法保存导出文件：{error}")
        })?;
    Ok(())
}

#[tauri::command]
fn open_audiveris(app: AppHandle) -> Result<(), String> {
    let executable = find_audiveris(&app)?;
    let mut command = std::process::Command::new(executable);
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    if let Some(tessdata) = find_tessdata(&app) {
        command.env("TESSDATA_PREFIX", tessdata);
    }
    command
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("无法启动 Audiveris：{error}"))
}

#[tauri::command]
fn open_license(app: AppHandle) -> Result<(), String> {
    let mut candidates = Vec::new();
    if let Ok(resource_dir) = app.path().resource_dir() {
        candidates.push(resource_dir.join("legal/LICENSE"));
        candidates.push(resource_dir.join("resources/legal/LICENSE"));
        candidates.push(resource_dir.join("LICENSE"));
    }
    candidates.push(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join("LICENSE"),
    );

    let license = candidates
        .into_iter()
        .find(|path| path.is_file())
        .ok_or_else(|| "安装包中没有找到许可证文件".to_owned())?;

    std::process::Command::new("open")
        .arg("-e")
        .arg(license)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("无法打开许可证：{error}"))
}

fn validate_input(input: &Path) -> Result<String, String> {
    if !input.is_file() {
        return Err("所选文件不存在".to_owned());
    }

    let metadata = std::fs::metadata(input).map_err(|error| format!("无法读取文件信息：{error}"))?;
    if metadata.len() == 0 {
        return Err("所选文件为空".to_owned());
    }
    if metadata.len() > MAX_INPUT_BYTES {
        return Err("文件超过 100 MB 限制".to_owned());
    }

    let extension = input
        .extension()
        .and_then(|value| value.to_str())
        .map(str::to_ascii_lowercase)
        .ok_or_else(|| "所选文件没有扩展名".to_owned())?;
    const ALLOWED: &[&str] = &["pdf", "png", "jpg", "jpeg", "tif", "tiff"];
    if !ALLOWED.contains(&extension.as_str()) {
        return Err("仅支持 PDF、PNG、JPEG、TIFF 曲谱".to_owned());
    }

    Ok(extension)
}

fn find_audiveris(app: &AppHandle) -> Result<PathBuf, String> {
    let mut candidates = Vec::new();

    if let Ok(value) = std::env::var("TOGPX_AUDIVERIS_EXECUTABLE") {
        candidates.push(PathBuf::from(value));
    }

    if let Ok(resource_dir) = app.path().resource_dir() {
        candidates.push(
            resource_dir
                .join("audiveris/Audiveris.app/Contents/MacOS/Audiveris"),
        );
        candidates.push(
            resource_dir
                .join("resources/audiveris/Audiveris.app/Contents/MacOS/Audiveris"),
        );
        if let Some(extracted) = extract_bundled_audiveris(app, &resource_dir) {
            candidates.push(extracted);
        }
    }

    candidates.push(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("resources/audiveris/Audiveris.app/Contents/MacOS/Audiveris"),
    );
    candidates.push(PathBuf::from(
        "/Applications/Audiveris.app/Contents/MacOS/Audiveris",
    ));

    candidates
        .into_iter()
        .find(|path| path.is_file())
        .ok_or_else(|| {
            "未找到 Audiveris 识别组件。请重新安装 ToGpx，或运行 npm run prepare:audiveris"
                .to_owned()
        })
}

fn find_tessdata(app: &AppHandle) -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if let Ok(resource_dir) = app.path().resource_dir() {
        candidates.push(resource_dir.join("tessdata"));
        candidates.push(resource_dir.join("resources/tessdata"));
    }
    candidates.push(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources/tessdata"));
    candidates.into_iter().find(|path| path.is_dir())
}

fn extract_bundled_audiveris(app: &AppHandle, resource_dir: &Path) -> Option<PathBuf> {
    let archive = [
        resource_dir.join("audiveris-runtime.zip"),
        resource_dir.join("resources/audiveris-runtime.zip"),
    ]
    .into_iter()
    .find(|path| path.is_file())?;

    let cache_root = app.path().app_cache_dir().ok()?.join("audiveris-5.11.0");
    let executable = cache_root.join("Audiveris.app/Contents/MacOS/Audiveris");
    if executable.is_file() {
        return Some(executable);
    }

    if cache_root.exists() {
        let _ = fs::remove_dir_all(&cache_root);
    }
    fs::create_dir_all(&cache_root).ok()?;

    let status = std::process::Command::new("ditto")
        .arg("-x")
        .arg("-k")
        .arg(&archive)
        .arg(&cache_root)
        .status()
        .ok()?;

    (status.success() && executable.is_file()).then_some(executable)
}

async fn find_music_xml(output_dir: &Path) -> Result<PathBuf, String> {
    let mut entries = tokio::fs::read_dir(output_dir)
        .await
        .map_err(|error| format!("无法检查识别输出：{error}"))?;

    while let Some(entry) = entries
        .next_entry()
        .await
        .map_err(|error| format!("无法检查识别输出：{error}"))?
    {
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        let extension = path
            .extension()
            .and_then(|value| value.to_str())
            .unwrap_or_default()
            .to_ascii_lowercase();
        if matches!(extension.as_str(), "mxl" | "musicxml" | "xml") {
            return Ok(path);
        }
    }

    Err("Audiveris 没有生成 MusicXML，请打开高级校对检查曲谱".to_owned())
}

fn truncate(value: &str, max_chars: usize) -> String {
    value.chars().take(max_chars).collect()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            recognize_file,
            export_gp,
            open_audiveris,
            open_license
        ])
        .run(tauri::generate_context!())
        .expect("error while running ToGpx");
}
