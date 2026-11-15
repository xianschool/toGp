use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use serde::{Deserialize, Serialize};
use std::{
    cmp::Ordering,
    fs,
    path::{Path, PathBuf},
    process::{ExitStatus, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering as AtomicOrdering},
        Arc, Mutex,
    },
    time::{Duration, Instant},
};
use tauri::{
    menu::{Menu, MenuItem, HELP_SUBMENU_ID},
    AppHandle, Emitter, Manager, State,
};
use tokio::io::{AsyncRead, AsyncReadExt};
use tokio::process::{Child, Command};
use uuid::Uuid;

const MAX_INPUT_BYTES: u64 = 100 * 1024 * 1024;
const MAX_TOTAL_BYTES: u64 = 300 * 1024 * 1024;
const MAX_FILES: usize = 30;
const RECOGNITION_TIMEOUT: Duration = Duration::from_secs(20 * 60);
const ABOUT_MENU_ID: &str = "about";

#[derive(Default)]
struct RecognitionState {
    active: Mutex<Option<Arc<AtomicBool>>>,
}

impl RecognitionState {
    fn begin(&self) -> Result<Arc<AtomicBool>, String> {
        let mut active = self
            .active
            .lock()
            .map_err(|_| "无法启动识别任务".to_owned())?;
        if active.is_some() {
            return Err("已有识别任务正在进行".to_owned());
        }
        let cancel = Arc::new(AtomicBool::new(false));
        *active = Some(Arc::clone(&cancel));
        Ok(cancel)
    }

    fn cancel(&self) -> bool {
        let cancel = self
            .active
            .lock()
            .ok()
            .and_then(|active| active.as_ref().cloned());
        if let Some(cancel) = cancel {
            cancel.store(true, AtomicOrdering::SeqCst);
            true
        } else {
            false
        }
    }

    fn finish(&self) {
        if let Ok(mut active) = self.active.lock() {
            *active = None;
        }
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct TabRecognitionResult {
    display_name: String,
    gp5_base64: String,
    report: serde_json::Value,
}

#[derive(Deserialize)]
struct TabConverterReply {
    ok: bool,
    error: Option<String>,
    report: Option<serde_json::Value>,
}

async fn read_pipe<R>(mut pipe: R) -> Result<Vec<u8>, String>
where
    R: AsyncRead + Unpin,
{
    let mut bytes = Vec::new();
    pipe.read_to_end(&mut bytes)
        .await
        .map_err(|error| format!("无法读取转换器输出：{error}"))?;
    Ok(bytes)
}

enum ChildOutcome {
    Exited(ExitStatus),
    Cancelled,
    TimedOut,
}

async fn wait_for_child(child: &mut Child, cancel: &AtomicBool) -> Result<ChildOutcome, String> {
    let started = Instant::now();
    loop {
        if let Some(status) = child
            .try_wait()
            .map_err(|error| format!("六线谱转换器失败：{error}"))?
        {
            return Ok(ChildOutcome::Exited(status));
        }
        if cancel.load(AtomicOrdering::SeqCst) {
            let _ = child.kill().await;
            return Ok(ChildOutcome::Cancelled);
        }
        if started.elapsed() >= RECOGNITION_TIMEOUT {
            let _ = child.kill().await;
            return Ok(ChildOutcome::TimedOut);
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
}

#[tauri::command]
async fn recognize_tab_files(
    app: AppHandle,
    state: State<'_, RecognitionState>,
    input_paths: Vec<String>,
) -> Result<TabRecognitionResult, String> {
    if input_paths.is_empty() {
        return Err("没有选择曲谱".to_owned());
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
    for input in &inputs {
        validate_input(input)?;
        total_bytes += fs::metadata(input)
            .map_err(|error| format!("无法读取文件信息：{error}"))?
            .len();
    }
    if total_bytes > MAX_TOTAL_BYTES {
        return Err("所选文件合计超过 300 MB 限制".to_owned());
    }

    let first_name = inputs
        .first()
        .and_then(|path| path.file_stem())
        .and_then(|name| name.to_str())
        .unwrap_or("未命名曲谱");
    let display_name = if inputs.len() == 1 {
        first_name.to_owned()
    } else {
        format!("{first_name} 等 {} 个文件", inputs.len())
    };

    let cancel = state.begin()?;
    let result = run_tab_recognition(&app, inputs, display_name, cancel).await;
    state.finish();
    result
}

async fn run_tab_recognition(
    app: &AppHandle,
    inputs: Vec<PathBuf>,
    display_name: String,
    cancel: Arc<AtomicBool>,
) -> Result<TabRecognitionResult, String> {
    let (python, runner, site_packages) = find_tab_converter(app)?;
    let work_dir = std::env::temp_dir().join(format!("togp-tab-{}", Uuid::new_v4()));
    let output_path = work_dir.join("output.gp5");
    let job_path = work_dir.join("job.json");
    let job = serde_json::json!({
        "inputs": inputs
            .iter()
            .map(|path| path.to_string_lossy().to_string())
            .collect::<Vec<_>>()
    });

    let result = async {
        tokio::fs::create_dir_all(&work_dir)
            .await
            .map_err(|error| format!("无法创建临时目录：{error}"))?;
        tokio::fs::write(
            &job_path,
            serde_json::to_vec(&job).map_err(|error| format!("无法创建转换任务：{error}"))?,
        )
        .await
        .map_err(|error| format!("无法创建转换任务：{error}"))?;

        let mut command = Command::new(python);
        command
            .arg(runner)
            .arg(&job_path)
            .arg(&output_path)
            .current_dir(&work_dir)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .env_clear()
            .env("HOME", &work_dir)
            .env("TMPDIR", &work_dir)
            .env("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
            .env("LANG", "C.UTF-8")
            .env("LC_ALL", "C.UTF-8")
            .env("PYTHONPATH", site_packages)
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .env("PYTHONNOUSERSITE", "1")
            .env("PYTHONUNBUFFERED", "1")
            .env("ORT_DISABLE_TELEMETRY", "1");

        let mut child = command
            .spawn()
            .map_err(|error| format!("无法启动六线谱转换器：{error}"))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "无法读取转换器输出".to_owned())?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| "无法读取转换器错误信息".to_owned())?;
        let stdout_task = tokio::spawn(read_pipe(stdout));
        let stderr_task = tokio::spawn(read_pipe(stderr));

        let status = match wait_for_child(&mut child, &cancel).await? {
            ChildOutcome::Exited(status) => status,
            ChildOutcome::Cancelled => {
                let _ = stdout_task.await;
                let _ = stderr_task.await;
                return Err("识别已取消".to_owned());
            }
            ChildOutcome::TimedOut => {
                let _ = stdout_task.await;
                let _ = stderr_task.await;
                return Err("六线谱识别超时".to_owned());
            }
        };
        let stdout = stdout_task
            .await
            .map_err(|error| format!("无法等待转换器输出：{error}"))??;
        let stderr = stderr_task
            .await
            .map_err(|error| format!("无法等待转换器错误信息：{error}"))??;

        let reply = serde_json::from_slice::<TabConverterReply>(&stdout).ok();
        if !status.success() || !reply.as_ref().is_some_and(|reply| reply.ok) {
            let detail = reply
                .and_then(|reply| reply.error)
                .or_else(|| {
                    let stderr = String::from_utf8_lossy(&stderr);
                    let detail = stderr.trim();
                    (!detail.is_empty()).then(|| truncate(detail, 1200))
                })
                .unwrap_or_else(|| format!("转换器退出，状态码 {:?}", status.code()));
            return Err(format!("六线谱识别失败：{detail}"));
        }

        let bytes = tokio::fs::read(&output_path)
            .await
            .map_err(|error| format!("无法读取 GP5 结果：{error}"))?;
        if bytes.is_empty() {
            return Err("转换器生成了空的 GP5 文件".to_owned());
        }

        Ok(TabRecognitionResult {
            display_name,
            gp5_base64: BASE64.encode(bytes),
            report: reply
                .and_then(|reply| reply.report)
                .unwrap_or(serde_json::Value::Null),
        })
    }
    .await;
    let _ = tokio::fs::remove_dir_all(&work_dir).await;
    result
}

#[tauri::command]
fn cancel_tab_recognition(state: State<'_, RecognitionState>) -> bool {
    state.cancel()
}

#[tauri::command]
async fn export_gp5(destination: String, bytes_base64: String) -> Result<(), String> {
    let destination = PathBuf::from(destination);
    if !destination
        .extension()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value.eq_ignore_ascii_case("gp5"))
    {
        return Err("导出文件必须使用 .gp5 扩展名".to_owned());
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

fn find_tab_converter(app: &AppHandle) -> Result<(PathBuf, PathBuf, PathBuf), String> {
    let mut roots = Vec::new();
    if let Ok(resource_dir) = app.path().resource_dir() {
        roots.push(resource_dir.join("resources/tab-converter"));
        roots.push(resource_dir.join("tab-converter"));
    }
    roots.push(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources/tab-converter"));

    for root in roots {
        let runner = root.join("runner.py");
        let site_packages = root.join("python/lib/python3.12/site-packages");
        let python = [
            root.join("python/bin/python3.12"),
            root.join("python/bin/python3"),
        ]
        .into_iter()
        .find(|path| path.is_file());
        if runner.is_file() && site_packages.is_dir() {
            if let Some(python) = python {
                return Ok((python, runner, site_packages));
            }
        }
    }

    Err("未找到六线谱转换组件，请重新安装 toGp".to_owned())
}

fn truncate(value: &str, max_chars: usize) -> String {
    value.chars().take(max_chars).collect()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(RecognitionState::default())
        .setup(|app| {
            let handle = app.handle().clone();
            let menu = Menu::default(&handle)?;
            if let Some(help_item) = menu.get(HELP_SUBMENU_ID) {
                if let Some(help) = help_item.as_submenu() {
                    help.set_text("帮助")?;
                    let about =
                        MenuItem::with_id(&handle, ABOUT_MENU_ID, "关于我们", true, None::<&str>)?;
                    help.append(&about)?;
                }
            }
            handle.set_menu(menu)?;
            handle.on_menu_event(|app, event| {
                if event.id().as_ref() == ABOUT_MENU_ID {
                    let _ = app.emit("open-about", ());
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            recognize_tab_files,
            cancel_tab_recognition,
            export_gp5
        ])
        .run(tauri::generate_context!())
        .expect("error while running toGp");
}

#[cfg(test)]
mod tests {
    use super::{natural_cmp, wait_for_child, ChildOutcome, RecognitionState};
    use std::{
        cmp::Ordering,
        process::Stdio,
        sync::atomic::AtomicBool,
        time::{Duration, Instant},
    };
    use tokio::process::Command;

    #[test]
    fn sorts_numeric_file_names_naturally() {
        assert_eq!(natural_cmp("page2.png", "page10.png"), Ordering::Less);
        assert_eq!(natural_cmp("scan_002.jpg", "scan_10.jpg"), Ordering::Less);
        assert_eq!(natural_cmp("002-page.png", "10-page.png"), Ordering::Less);
    }

    #[test]
    fn allows_only_one_active_recognition_and_can_cancel_it() {
        let state = RecognitionState::default();
        let _cancel = state.begin().expect("first task should start");

        assert!(state.begin().is_err());
        assert!(state.cancel());

        state.finish();
        assert!(state.begin().is_ok());
        assert!(state.cancel());
    }

    #[test]
    fn cancelling_a_recognition_kills_its_child_process() {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("test runtime");
        runtime.block_on(async {
            let mut child = Command::new("/bin/sleep")
                .arg("10")
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .kill_on_drop(true)
                .spawn()
                .expect("child process");
            let cancel = AtomicBool::new(true);
            let started = Instant::now();

            let outcome = wait_for_child(&mut child, &cancel)
                .await
                .expect("wait result");

            assert!(matches!(outcome, ChildOutcome::Cancelled));
            assert!(started.elapsed() < Duration::from_secs(2));
        });
    }
}
