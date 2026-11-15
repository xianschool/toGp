use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use serde::Serialize;
use std::{
    cmp::Ordering,
    ffi::OsString,
    fs,
    path::{Path, PathBuf},
    process::Stdio,
    time::Duration,
};
use tauri::{AppHandle, Manager};
use tokio::process::Command;
use uuid::Uuid;

const MAX_INPUT_BYTES: u64 = 100 * 1024 * 1024;
const MAX_TOTAL_BYTES: u64 = 300 * 1024 * 1024;
const MAX_FILES: usize = 30;
const RECOGNITION_TIMEOUT: Duration = Duration::from_secs(20 * 60);
const ABOUT_MENU_ID: &str = "about";
const PDF_LOW_RES_ATTEMPTS: &[(u32, u32)] =
    &[(50, 60_000_000), (30, 25_000_000)];

struct RecognitionEngineError {
    summary: String,
    stdout: String,
    stderr: String,
}

impl RecognitionEngineError {
    fn message(summary: String) -> Self {
        Self {
            summary,
            stdout: String::new(),
            stderr: String::new(),
        }
    }

    fn is_image_too_large(&self) -> bool {
        self.combined_logs().contains("Too large image:")
    }

    fn user_message(&self) -> String {
        if self.is_image_too_large() {
            return "PDF 页面分辨率过高，识别引擎无法读取。已尝试自动降低分辨率，仍然失败。"
                .to_owned();
        }

        let logs = self.combined_logs();
        let detail = logs
            .lines()
            .rev()
            .find(|line| line.contains("ERROR") || line.contains("WARN"))
            .map(str::trim)
            .filter(|line| !line.is_empty());
        match detail {
            Some(detail) => format!("{}：{}", self.summary, truncate(detail, 600)),
            None => self.summary.clone(),
        }
    }

    fn combined_logs(&self) -> String {
        format!("{}\n{}", self.stdout, self.stderr)
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RecognitionResult {
    display_name: String,
    music_xml_files: Vec<MusicXmlFile>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct MusicXmlFile {
    data_base64: String,
    extension: String,
}

#[tauri::command]
async fn recognize_files(
    app: AppHandle,
    input_paths: Vec<String>,
) -> Result<RecognitionResult, String> {
    if input_paths.is_empty() {
        return Err("没有选择图片".to_owned());
    }
    if input_paths.len() > MAX_FILES {
        return Err(format!("一次最多选择 {MAX_FILES} 个文件"));
    }

    let mut inputs = input_paths
        .into_iter()
        .map(PathBuf::from)
        .collect::<Vec<_>>();
    inputs.sort_by(|left, right| natural_path_cmp(left, right));

    let mut total_bytes = 0_u64;
    let mut validated = Vec::<(PathBuf, String)>::with_capacity(inputs.len());
    for input in inputs {
        let extension = validate_input(&input)?;
        total_bytes += fs::metadata(&input)
            .map_err(|error| format!("无法读取文件信息：{error}"))?
            .len();
        validated.push((input, extension));
    }
    if total_bytes > MAX_TOTAL_BYTES {
        return Err("所选文件合计超过 300 MB 限制".to_owned());
    }

    let first_name = validated
        .first()
        .and_then(|(path, _)| path.file_stem())
        .and_then(|name| name.to_str())
        .unwrap_or("未命名曲谱");
    let display_name = if validated.len() == 1 {
        first_name.to_owned()
    } else {
        format!("{first_name} 等 {} 页", validated.len())
    };

    let executable = find_audiveris(&app)?;
    let tessdata = find_tessdata(&app);
    let work_dir = std::env::temp_dir().join(format!("togp-{}", Uuid::new_v4()));
    tokio::fs::create_dir_all(&work_dir)
        .await
        .map_err(|error| format!("无法创建临时目录：{error}"))?;

    let result = if validated.len() == 1 {
        let (input, extension) = &validated[0];
        recognize_single(
            &executable,
            input,
            extension,
            &work_dir,
            display_name,
            tessdata.as_deref(),
        )
        .await
    } else {
        recognize_compound(
            &executable,
            &validated,
            &work_dir,
            display_name,
            tessdata.as_deref(),
        )
        .await
    };

    let _ = tokio::fs::remove_dir_all(&work_dir).await;
    result
}

async fn recognize_single(
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

    transcribe_book(
        executable,
        &copied_input,
        &output_dir,
        work_dir,
        extension == "pdf",
        tessdata,
    )
    .await?;

    read_result(&output_dir, work_dir, display_name).await
}

async fn recognize_compound(
    executable: &Path,
    inputs: &[(PathBuf, String)],
    work_dir: &Path,
    display_name: String,
    tessdata: Option<&Path>,
) -> Result<RecognitionResult, String> {
    let playlist_path = work_dir.join("playlist.xml");
    let output_dir = work_dir.join("output");
    tokio::fs::create_dir_all(&output_dir)
        .await
        .map_err(|error| format!("无法创建识别输出目录：{error}"))?;

    let mut playlist = String::from("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<play-list>");
    for (index, (input, extension)) in inputs.iter().enumerate() {
        let copied = work_dir.join(format!("input-{:03}.{}", index + 1, extension));
        tokio::fs::copy(input, &copied)
            .await
            .map_err(|error| format!("无法读取所选曲谱：{error}"))?;
        playlist.push_str("<excerpt><path>");
        playlist.push_str(&xml_escape(&copied.to_string_lossy()));
        playlist.push_str("</path>");
        if matches!(extension.as_str(), "png" | "jpg" | "jpeg" | "tif" | "tiff") {
            playlist.push_str("<sheets-selection>1</sheets-selection>");
        }
        playlist.push_str("</excerpt>");
    }
    playlist.push_str("</play-list>");
    tokio::fs::write(&playlist_path, playlist)
        .await
        .map_err(|error| format!("无法创建多页曲谱：{error}"))?;

    run_audiveris(
        executable,
        vec![
            "-batch".into(),
            "-playlist".into(),
            playlist_path.into_os_string(),
        ],
        work_dir,
        tessdata,
    )
    .await
    .map_err(|error| error.user_message())?;

    let compound_book = work_dir.join("playlist.omr");
    if !compound_book.is_file() {
        return Err("Audiveris 未能合并这些图片".to_owned());
    }

    transcribe_book(
        executable,
        &compound_book,
        &output_dir,
        work_dir,
        inputs.iter().any(|(_, extension)| extension == "pdf"),
        tessdata,
    )
    .await?;

    read_result(&output_dir, work_dir, display_name).await
}

async fn run_audiveris(
    executable: &Path,
    args: Vec<OsString>,
    work_dir: &Path,
    tessdata: Option<&Path>,
) -> Result<(), RecognitionEngineError> {
    let mut command = Command::new(executable);
    command
        .current_dir(work_dir)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    if let Some(tessdata) = tessdata {
        command.env("TESSDATA_PREFIX", tessdata);
    }

    let child = command.spawn().map_err(|error| {
        RecognitionEngineError::message(format!("无法启动本地识别引擎：{error}"))
    })?;
    let output = tokio::time::timeout(RECOGNITION_TIMEOUT, child.wait_with_output())
        .await
        .map_err(|_| RecognitionEngineError::message("识别超时".to_owned()))?
        .map_err(|error| RecognitionEngineError::message(format!("识别进程失败：{error}")))?;

    if !output.status.success() {
        return Err(RecognitionEngineError {
            summary: format!("识别引擎退出，状态码 {:?}", output.status.code()),
            stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        });
    }
    Ok(())
}

async fn transcribe_book(
    executable: &Path,
    book_path: &Path,
    output_dir: &Path,
    work_dir: &Path,
    allow_pdf_retry: bool,
    tessdata: Option<&Path>,
) -> Result<(), String> {
    let mut last_failure = None;

    for attempt_index in 0..=PDF_LOW_RES_ATTEMPTS.len() {
        if attempt_index > 0 {
            tokio::fs::remove_dir_all(output_dir)
                .await
                .map_err(|error| format!("无法重置识别输出目录：{error}"))?;
            tokio::fs::create_dir_all(output_dir)
                .await
                .map_err(|error| format!("无法创建识别输出目录：{error}"))?;
        }

        let extra_constants = attempt_index
            .checked_sub(1)
            .and_then(|index| PDF_LOW_RES_ATTEMPTS.get(index))
            .map(|(resolution, max_pixels)| {
                vec![
                    format!("org.audiveris.omr.image.ImageLoading.pdfResolution={resolution}"),
                    format!("org.audiveris.omr.step.LoadStep.maxPixelCount={max_pixels}"),
                ]
            })
            .unwrap_or_default();

        match run_audiveris(
            executable,
            recognition_arguments(output_dir, book_path, &extra_constants),
            work_dir,
            tessdata,
        )
        .await
        {
            Ok(()) => return Ok(()),
            Err(error) => {
                let can_retry = allow_pdf_retry
                    && error.is_image_too_large()
                    && attempt_index < PDF_LOW_RES_ATTEMPTS.len();
                last_failure = Some(error);
                if !can_retry {
                    break;
                }
            }
        }
    }

    Err(last_failure
        .map(|error| error.user_message())
        .unwrap_or_else(|| "识别引擎没有完成处理".to_owned()))
}

fn recognition_arguments(
    output_dir: &Path,
    book_path: &Path,
    extra_constants: &[String],
) -> Vec<OsString> {
    let mut arguments = vec![
        "-batch".into(),
        "-transcribe".into(),
        "-export".into(),
        "-constant".into(),
        "org.audiveris.omr.text.Language.defaultSpecification=eng+chi_sim".into(),
    ];
    for constant in extra_constants {
        arguments.push("-constant".into());
        arguments.push(constant.into());
    }
    arguments.extend([
        "-output".into(),
        output_dir.to_path_buf().into_os_string(),
        "--".into(),
        book_path.to_path_buf().into_os_string(),
    ]);
    arguments
}

async fn read_result(
    output_dir: &Path,
    work_dir: &Path,
    display_name: String,
) -> Result<RecognitionResult, String> {
    let music_xml_files = find_music_xml_files(&[output_dir, work_dir]).await;
    if music_xml_files.is_empty() {
        return Err("没有生成 MusicXML".to_owned());
    }
    let mut files = Vec::with_capacity(music_xml_files.len());
    for music_xml in music_xml_files {
        let bytes = tokio::fs::read(&music_xml)
            .await
            .map_err(|error| format!("无法读取识别结果：{error}"))?;
        files.push(MusicXmlFile {
            data_base64: BASE64.encode(bytes),
            extension: music_xml
                .extension()
                .and_then(|value| value.to_str())
                .unwrap_or("musicxml")
                .to_ascii_lowercase(),
        });
    }

    Ok(RecognitionResult {
        display_name,
        music_xml_files: files,
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

    let temporary = parent.join(format!(".togp-{}.tmp", Uuid::new_v4()));
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

fn validate_input(input: &Path) -> Result<String, String> {
    if !input.is_file() {
        return Err("所选文件不存在".to_owned());
    }
    let metadata = fs::metadata(input).map_err(|error| format!("无法读取文件信息：{error}"))?;
    if metadata.len() == 0 {
        return Err("所选文件为空".to_owned());
    }
    if metadata.len() > MAX_INPUT_BYTES {
        return Err("单个文件超过 100 MB 限制".to_owned());
    }

    let extension = input
        .extension()
        .and_then(|value| value.to_str())
        .map(str::to_ascii_lowercase)
        .ok_or_else(|| "所选文件没有扩展名".to_owned())?;
    const ALLOWED: &[&str] = &["pdf", "png", "jpg", "jpeg", "tif", "tiff"];
    if !ALLOWED.contains(&extension.as_str()) {
        return Err("仅支持 PDF、PNG、JPEG、TIFF".to_owned());
    }
    Ok(extension)
}

fn natural_path_cmp(left: &Path, right: &Path) -> Ordering {
    let left = left
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or_default();
    let right = right
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or_default();
    natural_cmp(left, right)
}

fn natural_cmp(left: &str, right: &str) -> Ordering {
    let left = left.to_lowercase().chars().collect::<Vec<_>>();
    let right = right.to_lowercase().chars().collect::<Vec<_>>();
    let (mut li, mut ri) = (0_usize, 0_usize);

    while li < left.len() && ri < right.len() {
        if left[li].is_ascii_digit() && right[ri].is_ascii_digit() {
            let left_start = li;
            while li < left.len() && left[li].is_ascii_digit() {
                li += 1;
            }
            let right_start = ri;
            while ri < right.len() && right[ri].is_ascii_digit() {
                ri += 1;
            }

            let left_sig = left[left_start..li]
                .iter()
                .skip_while(|ch| **ch == '0')
                .collect::<Vec<_>>();
            let right_sig = right[right_start..ri]
                .iter()
                .skip_while(|ch| **ch == '0')
                .collect::<Vec<_>>();
            let order = left_sig
                .len()
                .cmp(&right_sig.len())
                .then_with(|| left_sig.cmp(&right_sig));
            if order != Ordering::Equal {
                return order;
            }
            continue;
        }

        let order = left[li].cmp(&right[ri]);
        if order != Ordering::Equal {
            return order;
        }
        li += 1;
        ri += 1;
    }

    left.len().cmp(&right.len())
}

fn xml_escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

fn find_audiveris(app: &AppHandle) -> Result<PathBuf, String> {
    let mut candidates = Vec::new();
    if let Ok(value) = std::env::var("TOGP_AUDIVERIS_EXECUTABLE") {
        candidates.push(PathBuf::from(value));
    }
    if let Ok(resource_dir) = app.path().resource_dir() {
        candidates.push(resource_dir.join("audiveris/Audiveris.app/Contents/MacOS/Audiveris"));
        candidates
            .push(resource_dir.join("resources/audiveris/Audiveris.app/Contents/MacOS/Audiveris"));
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
        .ok_or_else(|| "未找到 Audiveris 识别组件，请重新安装 toGp".to_owned())
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

async fn find_music_xml_files(directories: &[&Path]) -> Vec<PathBuf> {
    let mut files = Vec::new();
    for directory in directories {
        let Ok(mut entries) = tokio::fs::read_dir(directory).await else {
            continue;
        };
        while let Ok(Some(entry)) = entries.next_entry().await {
            let path = entry.path();
            if !path.is_file() {
                continue;
            }
            let extension = path
                .extension()
                .and_then(|value| value.to_str())
                .unwrap_or_default()
                .to_ascii_lowercase();
            let is_music_xml = match extension.as_str() {
                "mxl" | "musicxml" => true,
                "xml" => tokio::fs::read(&path)
                    .await
                    .is_ok_and(|bytes| appears_to_be_music_xml(&bytes)),
                _ => false,
            };
            if is_music_xml {
                files.push(path);
            }
        }
    }
    files.sort_by(|left, right| natural_path_cmp(left, right));
    files.dedup();
    files
}

fn appears_to_be_music_xml(bytes: &[u8]) -> bool {
    let prefix = &bytes[..bytes.len().min(64 * 1024)];
    let text = String::from_utf8_lossy(prefix);
    text.contains("<score-partwise") || text.contains("<score-timewise")
}

fn truncate(value: &str, max_chars: usize) -> String {
    value.chars().take(max_chars).collect()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![recognize_files, export_gp])
        .run(tauri::generate_context!())
        .expect("error while running toGp");
}

#[cfg(test)]
mod tests {
    use super::{appears_to_be_music_xml, natural_cmp, RecognitionEngineError};
    use std::cmp::Ordering;

    #[test]
    fn sorts_numeric_file_names_naturally() {
        assert_eq!(natural_cmp("page2.png", "page10.png"), Ordering::Less);
        assert_eq!(natural_cmp("scan_002.jpg", "scan_10.jpg"), Ordering::Less);
        assert_eq!(natural_cmp("002-page.png", "10-page.png"), Ordering::Less);
    }

    #[test]
    fn distinguishes_music_xml_from_playlist_xml() {
        assert!(appears_to_be_music_xml(
            br#"<?xml version="1.0"?><score-partwise version="4.0"></score-partwise>"#
        ));
        assert!(!appears_to_be_music_xml(
            br#"<?xml version="1.0"?><play-list><excerpt></excerpt></play-list>"#
        ));
    }

    #[test]
    fn recognizes_oversized_pdf_failure() {
        let error = RecognitionEngineError {
            summary: "识别引擎退出，状态码 Some(1)".to_owned(),
            stdout: "WARN Too large image: 604,196,208 pixels (vs 20,000,000 max)".to_owned(),
            stderr: String::new(),
        };

        assert!(error.is_image_too_large());
        assert!(error.user_message().contains("PDF"));
    }
}
