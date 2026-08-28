#!/usr/bin/env python3
"""Build reproducible ELM source, skill, Linux, and Windows release assets."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPOSITORY_ROOT / "release"


def run(command: list[str], *, cwd: Path = REPOSITORY_ROOT) -> None:
    completed = subprocess.run(command, cwd=cwd, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed ({completed.returncode}): {command[0]}")


def project_version() -> str:
    configuration = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(configuration["project"]["version"])


def write_zip(archive: Path, source: Path, root_name: str) -> None:
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            relative = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(f"{root_name}/{relative}", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if path.suffix == ".sh" else 0o644) << 16
            output.writestr(info, path.read_bytes())


def write_tar_gz(archive: Path, source: Path, root_name: str) -> None:
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as output:
                for path in sorted(item for item in source.rglob("*") if item.is_file()):
                    relative = path.relative_to(source).as_posix()
                    info = output.gettarinfo(str(path), arcname=f"{root_name}/{relative}")
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    info.mode = 0o755 if path.suffix == ".sh" else 0o644
                    with path.open("rb") as handle:
                        output.addfile(info, handle)


def build_python_assets(python: Path, output: Path, version: str) -> Path:
    run([str(python), "-m", "build", "--wheel", "--sdist", "--outdir", str(output)])
    wheels = sorted(output.glob(f"elm_memory-{version.replace('-', '_')}-py3-none-any.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected one release wheel, found {len(wheels)}")
    return wheels[0]


def build_skill_asset(output: Path, version: str) -> None:
    write_zip(
        output / f"elm-memory-operator-{version}.zip",
        REPOSITORY_ROOT / "skills" / "elm-memory-operator",
        "elm-memory-operator",
    )


def build_linux_asset(output: Path, wheel: Path, version: str) -> None:
    with tempfile.TemporaryDirectory(prefix="elm-linux-release-") as temporary:
        payload = Path(temporary)
        shutil.copy2(wheel, payload / wheel.name)
        shutil.copy2(REPOSITORY_ROOT / "packaging" / "linux" / "install.sh", payload / "install.sh")
        shutil.copy2(REPOSITORY_ROOT / "LICENSE", payload / "LICENSE")
        shutil.copy2(REPOSITORY_ROOT / "README.md", payload / "README.md")
        write_tar_gz(
            output / f"elm-memory-{version}-linux-any.tar.gz",
            payload,
            f"elm-memory-{version}-linux-any",
        )


def locate_iscc() -> Path:
    candidates = [
        shutil.which("iscc"),
        os.environ.get("ISCC_PATH"),
        str(Path.home() / "AppData" / "Local" / "Programs" / "Inno Setup 6" / "ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise FileNotFoundError("Inno Setup 6 compiler not found. Install JRSoftware.InnoSetup or set ISCC_PATH.")


def smoke_frozen_payload(payload: Path, version: str) -> None:
    elm = payload / "elm.exe"
    mcp = payload / "elm-mcp.exe"
    run([str(elm), "--version"], cwd=payload)
    run([str(mcp), "--help"], cwd=payload)
    with tempfile.TemporaryDirectory(prefix="elm-frozen-smoke-") as temporary:
        temporary_path = Path(temporary)
        root = temporary_path / "memory"
        shutil.copytree(REPOSITORY_ROOT / "tests" / "fixtures" / "sample_elm", root)
        before = {path.relative_to(root): hashlib.sha256(path.read_bytes()).digest() for path in root.rglob("*.md")}
        run([str(elm), "rebuild", "--root", str(root), "--json"], cwd=payload)
        run([str(elm), "doctor", "--root", str(root), "--json", "--no-sync"], cwd=payload)
        after = {path.relative_to(root): hashlib.sha256(path.read_bytes()).digest() for path in root.rglob("*.md")}
        if before != after:
            raise RuntimeError("Frozen smoke test modified canonical Markdown")
        fresh_root = temporary_path / "fresh-memory"
        run([
            str(elm),
            "init",
            "--root",
            str(fresh_root),
            "--project",
            "release-smoke",
            "--json",
        ], cwd=payload)
        if not (fresh_root / "00_registry" / "ELM_ROOT_ID.json").is_file():
            raise RuntimeError("Frozen init did not create a portable root identity")
        run([str(elm), "doctor", "--root", str(fresh_root), "--json", "--no-sync"], cwd=payload)
    completed = subprocess.run([str(elm), "--version"], check=True, capture_output=True, text=True)
    if completed.stdout.strip() != f"elm {version}":
        raise RuntimeError("Frozen executable version mismatch")


def smoke_windows_installer(installer: Path, version: str) -> None:
    with tempfile.TemporaryDirectory(prefix="elm-installer-smoke-") as temporary:
        install_dir = Path(temporary) / "ELM"
        run([
            str(installer),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/CURRENTUSER",
            f"/DIR={install_dir}",
            "/MERGETASKS=!addtopath",
        ])
        run([str(install_dir / "elm.exe"), "--version"], cwd=install_dir)
        run([str(install_dir / "elm-mcp.exe"), "--help"], cwd=install_dir)
        uninstallers = sorted(install_dir.glob("unins*.exe"))
        if len(uninstallers) != 1:
            raise RuntimeError("Windows uninstaller was not created")
        run([
            str(uninstallers[0]),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
        ], cwd=install_dir)
        deadline = time.monotonic() + 15.0
        while install_dir.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        if install_dir.exists() and any(install_dir.iterdir()):
            raise RuntimeError(f"Windows uninstaller left files for ELM {version}")


def build_windows_assets(python: Path, output: Path, version: str) -> None:
    if os.name != "nt":
        raise RuntimeError("Windows assets must be built on Windows")
    with tempfile.TemporaryDirectory(prefix="elm-windows-release-") as temporary:
        work = Path(temporary)
        dist = work / "dist"
        run([
            str(python),
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--console",
            "--name",
            "elm",
            "--distpath",
            str(dist),
            "--workpath",
            str(work / "build"),
            "--specpath",
            str(work / "spec"),
            "--paths",
            str(REPOSITORY_ROOT / "src"),
            "--copy-metadata",
            "mcp",
            "--copy-metadata",
            "mcp-types",
            str(REPOSITORY_ROOT / "packaging" / "launcher.py"),
        ])
        payload = dist / "elm"
        shutil.copy2(payload / "elm.exe", payload / "elm-mcp.exe")
        for name in ("LICENSE", "NOTICE", "README.md"):
            shutil.copy2(REPOSITORY_ROOT / name, payload / name)
        smoke_frozen_payload(payload, version)
        write_zip(
            output / f"elm-memory-{version}-windows-x64-portable.zip",
            payload,
            f"elm-memory-{version}-windows-x64",
        )
        iscc = locate_iscc()
        run([
            str(iscc),
            f"/DAppVersion={version}",
            f"/DPayloadDir={payload}",
            f"/DOutputDir={output}",
            str(REPOSITORY_ROOT / "packaging" / "windows" / "installer.iss"),
        ])
        installer = output / f"ELM-Memory-{version}-windows-x64-setup.exe"
        if not installer.is_file():
            raise RuntimeError("Inno Setup did not create the expected installer")
        smoke_windows_installer(installer, version)


def write_checksums(output: Path) -> None:
    lines = []
    for path in sorted(item for item in output.iterdir() if item.is_file() and item.name != "SHA256SUMS.txt"):
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (output / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--windows", action="store_true", help="Also build and smoke-test Windows assets")
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    output = args.output.resolve()
    if output == REPOSITORY_ROOT or REPOSITORY_ROOT not in output.parents:
        raise SystemExit("Release output must be a child of the repository")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    version = project_version()
    wheel = build_python_assets(args.python.resolve(), output, version)
    build_skill_asset(output, version)
    build_linux_asset(output, wheel, version)
    if args.windows:
        build_windows_assets(args.python.resolve(), output, version)
    write_checksums(output)
    print(f"Built ELM {version} release assets in {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
