from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_readme_documents_service_start_stop_and_showcase_image():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "![KnoNote 页面展示](figure/frame.png)" in readme
    assert "### 4. 启动服务" in readme
    assert "### 5. 停止服务" in readme
    assert "打开前端页面后，点击同步按钮同步收藏内容。" in readme
    assert "### 4. 同步收藏夹" not in readme
    assert "### 6. 启动后端" not in readme
    assert "### 7. 启动前端" not in readme
    assert "start_server.sh   # 启动前后端服务" in readme
    assert "stop_server.sh    # 停止前后端服务" in readme


def test_stop_script_covers_backend_and_frontend_processes():
    stop_script = (PROJECT_ROOT / "stop_server.sh").read_text(encoding="utf-8")

    assert "FRONTEND_PORT" in stop_script
    assert "FRONTEND_PID_FILE" in stop_script
    assert 'stop_by_pidfile "后端"' in stop_script
    assert 'stop_by_pidfile "前端"' in stop_script
