from pathlib import Path
from zipfile import ZipFile

import main
from scripts.build_release_artifacts import SOURCE_BUNDLE_FILES, build_source_bundle


def test_get_resource_path_uses_project_root_when_not_frozen():
    expected_path = Path(main.__file__).resolve().parent / "query.sql"

    assert main.get_resource_path("query.sql") == expected_path


def test_get_runtime_output_path_uses_executable_directory_when_frozen(monkeypatch, tmp_path):
    executable_path = tmp_path / "dist" / "AutoResearch_SQLServer.exe"
    executable_path.parent.mkdir(parents=True)
    executable_path.write_text("", encoding="utf-8")
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()

    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main.sys, "_MEIPASS", str(bundle_root), raising=False)
    monkeypatch.setattr(main.sys, "executable", str(executable_path), raising=False)

    assert main.get_resource_path("query.sql") == bundle_root / "query.sql"
    assert main.get_runtime_output_path("results.json") == executable_path.parent / "results.json"


def test_build_source_bundle_includes_runtime_files_and_excludes_local_env(tmp_path):
    source_root = Path(__file__).resolve().parents[1]
    project_root = tmp_path / "project-root"
    project_root.mkdir()

    for relative_path in SOURCE_BUNDLE_FILES:
        source_path = source_root / relative_path
        destination_path = project_root / relative_path
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        destination_path.write_bytes(source_path.read_bytes())

    (project_root / ".env").write_text("DB_SERVER=local\n", encoding="utf-8")

    archive_path = build_source_bundle(output_dir=tmp_path, version="0.1.0", project_root=project_root)

    with ZipFile(archive_path) as archive:
        names = set(archive.namelist())

    prefix = "AutoResearch_SQLServer-0.1.0/"
    expected_entries = {
        f"{prefix}README.md",
        f"{prefix}requirements.txt",
        f"{prefix}.env.example",
        f"{prefix}query.sql",
        f"{prefix}main.py",
        f"{prefix}runner.py",
        f"{prefix}variants.py",
        f"{prefix}validator.py",
    }

    assert expected_entries.issubset(names)
    assert f"{prefix}.env" not in names


def test_release_workflow_uses_shared_next_version_request_adapter():
    workflow_text = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")

    assert "reusable-version-consistency.yml@main" in workflow_text
    assert "reusable-next-version-request.yml@main" in workflow_text
    assert "source-repository: ${{ github.repository }}" in workflow_text
    assert "repository-ref: ${{ github.ref }}" in workflow_text
    assert "needs: [version-consistency, next-version-request]" in workflow_text
    assert "expected-release-version: ${{ github.ref_name }}" in workflow_text
    assert "Validate next version request" not in workflow_text