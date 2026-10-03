"""``xil setup chatterbox`` — build the local-model venv the Chatterbox Turbo
worker runs under.

Creates ``venv-chatterbox`` where :func:`~xil_pipeline.models.resolve_venv_python`
looks for it (``--dir``, else ``$XIL_CODEROOT``, else the workspace root), installs
PyTorch (CUDA wheels when ``nvidia-smi`` works, CPU wheels otherwise),
``chatterbox-tts`` and ``pydub``, then checks that the worker's imports load.
Uses ``uv`` when it is on ``PATH``, otherwise ``python -m venv`` and pip.

Output matches the Rust ``xil setup`` line for line (parity).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import get_code_root, get_workspace_root

logger = get_logger(__name__)

VENV = "venv-chatterbox"
TORCH = ("torch==2.6.0", "torchaudio==2.6.0")
PACKAGES = ("chatterbox-tts", "pydub")
VERIFY = (
    "import torch, torchaudio, pydub; "
    "from chatterbox.tts_turbo import ChatterboxTurboTTS; "
    "print(torch.cuda.is_available())"
)


@dataclass(frozen=True)
class Installer:
    """``kind`` is ``"uv"`` or ``"pip"``; for pip, ``program`` is the base
    interpreter used for ``-m venv``."""

    kind: str
    program: str


@dataclass
class Step:
    label: str
    program: str
    args: list[str] = field(default_factory=list)

    def display(self) -> str:
        return " ".join([self.program, *self.args])


@dataclass
class Opts:
    venv: str
    python_ver: str
    cuda: bool
    cuda_index: str
    force: bool
    dry_run: bool
    coderoot_set: bool


def venv_python(venv: str) -> str:
    return os.path.join(venv, "bin", "python3")


def _find_on_path(name: str) -> str | None:
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def find_installer(python_ver: str) -> Installer | None:
    uv = _find_on_path("uv")
    if uv:
        return Installer("uv", uv)
    base = _find_on_path(f"python{python_ver}") or _find_on_path("python3")
    return Installer("pip", base) if base else None


def detect_cuda() -> bool:
    try:
        r = subprocess.run(["nvidia-smi"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return r.returncode == 0


def use_cuda(device: str, detected: bool) -> bool:
    return {"auto": detected, "cuda": True, "cpu": False}[device]


def target_dir(explicit: str | None, code_root: Path | None, workspace: Path) -> str:
    """``--dir``, else ``$XIL_CODEROOT``, else the workspace: the places
    ``resolve_venv_python`` searches."""
    if explicit:
        return explicit
    return str(code_root if code_root is not None else workspace)


def index_url(cuda: bool, cuda_index: str) -> str:
    return f"https://download.pytorch.org/whl/{cuda_index if cuda else 'cpu'}"


def plan(installer: Installer, venv: str, python_ver: str, cuda: bool, cuda_index: str) -> list[Step]:
    py = venv_python(venv)
    index = index_url(cuda, cuda_index)
    if installer.kind == "uv":
        uv = installer.program
        return [
            Step("create venv", uv, ["venv", venv, "--python", python_ver]),
            Step("install PyTorch", uv, ["pip", "install", "--python", py, *TORCH, "--index-url", index]),
            Step("install chatterbox-tts", uv, ["pip", "install", "--python", py, *PACKAGES]),
        ]
    return [
        Step("create venv", installer.program, ["-m", "venv", venv]),
        Step("upgrade pip", py, ["-m", "pip", "install", "--upgrade", "pip"]),
        Step("install PyTorch", py, ["-m", "pip", "install", *TORCH, "--index-url", index]),
        Step("install chatterbox-tts", py, ["-m", "pip", "install", *PACKAGES]),
    ]


def verify(python: str) -> bool | None:
    """CUDA availability when the venv imports everything the worker needs,
    else ``None``."""
    if not os.path.exists(python):
        return None
    try:
        r = subprocess.run([python, "-c", VERIFY], capture_output=True, text=True)
    except OSError:
        return None
    if r.returncode != 0:
        return None
    lines = r.stdout.splitlines()
    last = lines[-1].strip() if lines else ""
    return {"True": True, "False": False}.get(last)


def _device_name(cuda: bool) -> str:
    return "CUDA" if cuda else "CPU"


def execute(opts: Opts, installer: Installer | None) -> int:
    python = venv_python(opts.venv)

    if not opts.force and not opts.dry_run:
        cuda = verify(python)
        if cuda is not None:
            print(f"{opts.venv} is already set up ({_device_name(cuda)}).")
            print("Use --force to rebuild it.")
            return 0

    if installer is None:
        logger.error(
            "Neither uv nor python%s / python3 was found on PATH. Install uv "
            "(https://docs.astral.sh/uv/) or Python 3.10-3.13, then retry.",
            opts.python_ver,
        )
        return 1

    steps = plan(installer, opts.venv, opts.python_ver, opts.cuda, opts.cuda_index)
    print(f"Setting up {opts.venv} with {_device_name(opts.cuda)} PyTorch wheels.")

    if opts.dry_run:
        if opts.force and os.path.exists(opts.venv):
            print(f"rm -rf {opts.venv}")
        for s in steps:
            print(s.display())
        return 0

    if os.path.exists(opts.venv):
        if opts.force:
            print(f"Removing {opts.venv}")
            shutil.rmtree(opts.venv)
        else:
            logger.error(
                "%s exists but does not import chatterbox. Rerun with --force to rebuild it.",
                opts.venv,
            )
            return 1

    for i, s in enumerate(steps, 1):
        print(f"[{i}/{len(steps)}] {s.label}: {s.display()}", flush=True)
        rc = subprocess.run([s.program, *s.args]).returncode
        if rc != 0:
            logger.error("Step '%s' failed (exit status: %d).", s.label, rc)
            return 1

    cuda = verify(python)
    if cuda is None:
        logger.error("%s was built but does not import chatterbox. Check the output above.", opts.venv)
        return 1
    print(f"Verified: chatterbox imports; device {_device_name(cuda)}.")
    if opts.cuda and not cuda:
        logger.warning(
            "CUDA wheels were installed but torch sees no GPU; Chatterbox Turbo "
            "will fall back to the CPU. Check your NVIDIA driver, or try another "
            "--cuda-index."
        )

    print("\nNext:")
    if not opts.coderoot_set:
        print(f"  export XIL_CODEROOT={os.path.dirname(opts.venv)}")
    print("  Save one clip per speaker as voice_refs/<speaker_key>.wav (over 5 seconds).")
    print(
        "  If the model is gated for your account: export HF_TOKEN=hf_... "
        "(weights download on first render)."
    )
    print("  xil produce --episode S01E01 --backend chatterbox-turbo")
    return 0


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-setup",
        description=(
            "Create and verify the virtual environment a local ML worker runs "
            "under. 'chatterbox' builds venv-chatterbox with PyTorch (CUDA "
            "wheels when an NVIDIA GPU is found, CPU wheels otherwise) and "
            "chatterbox-tts, then checks that it imports."
        ),
        epilog=(
            "The venv is created in --dir, else $XIL_CODEROOT, else the "
            "workspace root: the places 'xil produce' looks for it. Uses uv "
            "when it is on PATH, otherwise python -m venv and pip. Running it "
            "again on a working venv does nothing."
        ),
    )
    parser.add_argument("target", choices=["chatterbox"], help="what to set up")
    parser.add_argument(
        "--device",
        choices=["auto", "cuda", "cpu"],
        default="auto",
        help="PyTorch build to install (auto: CUDA if nvidia-smi works, else CPU)",
    )
    parser.add_argument("--cuda-index", default="cu124", metavar="TAG", help="PyTorch CUDA wheel index tag")
    parser.add_argument("--dir", metavar="PATH", help="directory to create venv-chatterbox in")
    parser.add_argument(
        "--python",
        default="3.13",
        metavar="VER",
        help="Python version for the venv (chatterbox-tts needs 3.10-3.13 for torch 2.6)",
    )
    parser.add_argument("--force", action="store_true", help="delete and rebuild an existing venv")
    parser.add_argument("--dry-run", action="store_true", help="print the commands without running them")
    return parser


def main() -> int:
    """CLI entry point for ``xil setup``."""
    configure_logging()
    args = get_parser().parse_args()
    detected = args.device == "auto" and detect_cuda()
    code_root = get_code_root()
    opts = Opts(
        venv=os.path.join(target_dir(args.dir, code_root, get_workspace_root()), VENV),
        python_ver=args.python,
        cuda=use_cuda(args.device, detected),
        cuda_index=args.cuda_index,
        force=args.force,
        dry_run=args.dry_run,
        coderoot_set=code_root is not None,
    )
    return execute(opts, find_installer(args.python))


if __name__ == "__main__":
    sys.exit(main())
