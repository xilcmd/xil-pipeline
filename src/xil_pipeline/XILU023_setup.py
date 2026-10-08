"""``xil setup`` — build the venv a local ML worker runs under.

Targets:

* ``chatterbox`` — ``venv-chatterbox`` for the Chatterbox Turbo TTS worker:
  PyTorch, ``chatterbox-tts`` and ``pydub``.
* ``whisper`` — ``venv-whisper`` for ``xil stem-verify``: ``faster-whisper``
  (CTranslate2, no PyTorch).
* ``mmaudio`` — ``venv-mmaudio`` for ``--sfx-backend mmaudio``: a pinned clone
  of hkchengrex/MMAudio installed editable, then PyTorch re-pinned afterwards
  (MMAudio's unbounded ``torch`` pulls a CUDA 13 build).

Each venv goes where :func:`~xil_pipeline.models.resolve_venv_python` looks
for it (``--dir``, else ``$XIL_CODEROOT``, else the workspace root). PyTorch
comes from the CUDA wheel index when ``nvidia-smi`` works, the CPU index
otherwise. Uses ``uv`` when it is on ``PATH``, otherwise ``python -m venv``
and pip. The venv is then checked by importing what its worker needs.

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


@dataclass(frozen=True)
class Target:
    """One ``xil setup`` target. ``repo`` (URL, ref) is cloned next to the
    venv as ``MMAudio`` and installed editable before ``packages``; ``torch``
    is installed last so it wins over whatever ``packages`` pulled in."""

    name: str
    venv: str
    python: str
    torch: tuple[str, ...]
    packages: tuple[str, ...]
    label: str
    import_name: str
    verify: str
    repo: tuple[str, str] | None = None


CHATTERBOX = Target(
    name="chatterbox",
    venv="venv-chatterbox",
    python="3.13",
    torch=("torch==2.6.0", "torchaudio==2.6.0"),
    packages=("chatterbox-tts", "pydub"),
    label="install chatterbox-tts",
    import_name="chatterbox",
    verify=(
        "import torch, torchaudio, pydub; "
        "from chatterbox.tts_turbo import ChatterboxTurboTTS; "
        "print(torch.cuda.is_available())"
    ),
)
WHISPER = Target(
    name="whisper",
    venv="venv-whisper",
    python="3.13",
    torch=(),
    packages=("faster-whisper",),
    label="install faster-whisper",
    import_name="faster_whisper",
    verify=(
        "import ctranslate2; from faster_whisper import WhisperModel; "
        "print(ctranslate2.get_cuda_device_count() > 0)"
    ),
)
# MMAudio pins numpy<2.1, which has no Python 3.13 wheels: default to 3.12.
MMAUDIO = Target(
    name="mmaudio",
    venv="venv-mmaudio",
    python="3.12",
    torch=("torch==2.6.0", "torchaudio==2.6.0", "torchvision==0.21.0"),
    packages=("pydub",),
    label="install MMAudio",
    import_name="mmaudio",
    verify=(
        "import torch, torchaudio, pydub; "
        "from mmaudio.eval_utils import all_model_cfg; "
        "print(torch.cuda.is_available())"
    ),
    repo=("https://github.com/hkchengrex/MMAudio", "974010a"),
)
TARGETS = {t.name: t for t in (CHATTERBOX, WHISPER, MMAUDIO)}

# The checkpoint of the worker's model (mmaudio_worker._DEFAULT_VARIANT).
MMAUDIO_WEIGHTS = os.path.join("weights", "mmaudio_large_44k_v2.pth")

VENV = CHATTERBOX.venv


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
    target: Target = CHATTERBOX
    git: str | None = None


def venv_python(venv: str) -> str:
    return os.path.join(venv, "bin", "python3")


def repo_dir(venv: str) -> str:
    """The MMAudio clone sits beside its venv."""
    return os.path.join(os.path.dirname(venv), "MMAudio")


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


def needs_clone(target: Target, venv: str) -> bool:
    return target.repo is not None and not os.path.isdir(repo_dir(venv))


def plan(
    installer: Installer,
    venv: str,
    python_ver: str,
    cuda: bool,
    cuda_index: str,
    target: Target = CHATTERBOX,
    git: str = "git",
) -> list[Step]:
    py = venv_python(venv)
    index = index_url(cuda, cuda_index)
    if installer.kind == "uv":
        uv = installer.program
        steps = [Step("create venv", uv, ["venv", venv, "--python", python_ver])]
        install = [uv, "pip", "install", "--python", py]
    else:
        steps = [
            Step("create venv", installer.program, ["-m", "venv", venv]),
            Step("upgrade pip", py, ["-m", "pip", "install", "--upgrade", "pip"]),
        ]
        install = [py, "-m", "pip", "install"]

    def install_step(label: str, args: list[str]) -> Step:
        return Step(label, install[0], [*install[1:], *args])

    torch = install_step("install PyTorch", [*target.torch, "--index-url", index])
    if target.repo is None:
        if target.torch:
            steps.append(torch)
        steps.append(install_step(target.label, list(target.packages)))
        return steps

    url, ref = target.repo
    repo = repo_dir(venv)
    if needs_clone(target, venv):
        steps += [
            Step("clone MMAudio", git, ["clone", url, repo]),
            Step(f"check out MMAudio {ref}", git, ["-C", repo, "checkout", ref]),
        ]
    steps.append(install_step(target.label, ["-e", repo, *target.packages]))
    steps.append(torch)
    return steps


def verify(python: str, target: Target = CHATTERBOX) -> bool | None:
    """GPU availability when the venv imports everything the worker needs,
    else ``None``."""
    if not os.path.exists(python):
        return None
    try:
        r = subprocess.run([python, "-c", target.verify], capture_output=True, text=True)
    except OSError:
        return None
    if r.returncode != 0:
        return None
    lines = r.stdout.splitlines()
    last = lines[-1].strip() if lines else ""
    return {"True": True, "False": False}.get(last)


def _device_name(cuda: bool) -> str:
    return "CUDA" if cuda else "CPU"


def _header(opts: Opts) -> str:
    if not opts.target.torch:
        return f"Setting up {opts.venv}."
    return f"Setting up {opts.venv} with {_device_name(opts.cuda)} PyTorch wheels."


def _no_gpu_warning(target: Target) -> str:
    if target is WHISPER:
        return (
            "nvidia-smi works but CTranslate2 sees no GPU; Whisper will run on "
            "the CPU (int8). Check your NVIDIA driver."
        )
    worker = "MMAudio" if target is MMAUDIO else "Chatterbox Turbo"
    return (
        f"CUDA wheels were installed but torch sees no GPU; {worker} "
        "will fall back to the CPU. Check your NVIDIA driver, or try another "
        "--cuda-index."
    )


def _next_steps(opts: Opts) -> list[str]:
    target = opts.target
    if target is WHISPER:
        return ["  xil stem-verify --episode S01E01"]
    if target is MMAUDIO:
        weights = os.path.join(os.path.dirname(opts.venv), MMAUDIO_WEIGHTS)
        found = (
            f"  Weights found: {weights}"
            if os.path.isfile(weights)
            else f"  Weights not found: {weights} (about 6 GB downloads on the first run)."
        )
        return [
            found,
            "  MMAudio weights are CC BY-NC 4.0: non-commercial use only.",
            "  xil sfx --episode S01E01 --gen-sfx --sfx-backend mmaudio --mmaudio-accept-noncommercial",
        ]
    return [
        "  Save one clip per speaker as voice_refs/<speaker_key>.wav (over 5 seconds).",
        "  If the model is gated for your account: export HF_TOKEN=hf_... "
        "(weights download on first render).",
        "  xil produce --episode S01E01 --backend chatterbox-turbo",
    ]


def execute(opts: Opts, installer: Installer | None) -> int:
    target = opts.target
    python = venv_python(opts.venv)

    if not opts.force and not opts.dry_run:
        cuda = verify(python, target)
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

    if installer.kind == "pip" and os.path.basename(installer.program) != f"python{opts.python_ver}":
        logger.warning(
            "python%s was not found on PATH; building with %s instead. Install uv "
            "(it fetches the right Python) if the build fails.",
            opts.python_ver,
            installer.program,
        )

    if needs_clone(target, opts.venv) and opts.git is None:
        logger.error(
            "git was not found on PATH; it is needed to clone %s into %s. "
            "Install git, or clone it there yourself, then retry.",
            target.repo[0],
            repo_dir(opts.venv),
        )
        return 1

    steps = plan(
        installer, opts.venv, opts.python_ver, opts.cuda, opts.cuda_index, target, opts.git or "git"
    )
    print(_header(opts))

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
                "%s exists but does not import %s. Rerun with --force to rebuild it.",
                opts.venv,
                target.import_name,
            )
            return 1

    for i, s in enumerate(steps, 1):
        print(f"[{i}/{len(steps)}] {s.label}: {s.display()}", flush=True)
        rc = subprocess.run([s.program, *s.args]).returncode
        if rc != 0:
            logger.error("Step '%s' failed (exit status: %d).", s.label, rc)
            return 1

    cuda = verify(python, target)
    if cuda is None:
        logger.error(
            "%s was built but does not import %s. Check the output above.",
            opts.venv,
            target.import_name,
        )
        return 1
    print(f"Verified: {target.import_name} imports; device {_device_name(cuda)}.")
    if opts.cuda and not cuda:
        logger.warning(_no_gpu_warning(target))

    print("\nNext:")
    if not opts.coderoot_set:
        print(f"  export XIL_CODEROOT={os.path.dirname(opts.venv)}")
    for line in _next_steps(opts):
        print(line)
    return 0


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-setup",
        description=(
            "Create and verify the virtual environment a local ML worker runs "
            "under. 'chatterbox' builds venv-chatterbox (PyTorch + "
            "chatterbox-tts) for local TTS; 'whisper' builds venv-whisper "
            "(faster-whisper) for xil stem-verify; 'mmaudio' clones MMAudio "
            "and builds venv-mmaudio for local SFX. PyTorch comes as CUDA "
            "wheels when an NVIDIA GPU is found, CPU wheels otherwise."
        ),
        epilog=(
            "The venv is created in --dir, else $XIL_CODEROOT, else the "
            "workspace root: the places the xil commands look for it. Uses uv "
            "when it is on PATH, otherwise python -m venv and pip. Running it "
            "again on a working venv does nothing."
        ),
    )
    parser.add_argument("target", choices=list(TARGETS), help="what to set up")
    parser.add_argument(
        "--device",
        choices=["auto", "cuda", "cpu"],
        default="auto",
        help="PyTorch build to install (auto: CUDA if nvidia-smi works, else CPU)",
    )
    parser.add_argument("--cuda-index", default="cu124", metavar="TAG", help="PyTorch CUDA wheel index tag")
    parser.add_argument("--dir", metavar="PATH", help="directory to create the venv in")
    parser.add_argument(
        "--python",
        metavar="VER",
        help="Python version for the venv (default: 3.13; 3.12 for mmaudio)",
    )
    parser.add_argument("--force", action="store_true", help="delete and rebuild an existing venv")
    parser.add_argument("--dry-run", action="store_true", help="print the commands without running them")
    return parser


def main() -> int:
    """CLI entry point for ``xil setup``."""
    configure_logging()
    args = get_parser().parse_args()
    target = TARGETS[args.target]
    python_ver = args.python or target.python
    detected = args.device == "auto" and detect_cuda()
    code_root = get_code_root()
    opts = Opts(
        venv=os.path.join(target_dir(args.dir, code_root, get_workspace_root()), target.venv),
        python_ver=python_ver,
        cuda=use_cuda(args.device, detected),
        cuda_index=args.cuda_index,
        force=args.force,
        dry_run=args.dry_run,
        coderoot_set=code_root is not None,
        target=target,
        git=_find_on_path("git"),
    )
    return execute(opts, find_installer(python_ver))


if __name__ == "__main__":
    sys.exit(main())
