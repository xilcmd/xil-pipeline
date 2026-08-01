"""
Generate MkDocs documentation from Python source files using mkdocstrings.

This script scans the xil-pipeline directory and creates markdown files for
automatic API documentation generation.

By default, clears existing generated documentation before building to prevent
stale references. Use --no-clear to preserve existing files.
"""
from pathlib import Path
import logging
import argparse
import os
import shutil

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Subdirectory of docs/ that holds the generated API pages, and the only
# directory clean_generated_docs() removes.
#
# This is a FIXED NAME, deliberately not derived from the checkout directory.
# The generated tree is committed at docs/xil-pipeline/, so deriving the name
# from code_root.name meant any checkout not literally named "xil-pipeline"
# generated a SECOND tree beside the committed one — every mkdocstrings anchor
# then existed twice and --strict aborted on ~86 "Multiple primary URLs"
# warnings.  Read the Docs checks out into "latest"/"stable", so its builds
# failed while GitHub Actions (which checks out into .../xil-pipeline/) passed.
GENERATED_DOCS_DIRNAME = "xil-pipeline"

# Repo-root markdown -> the docs/ subfolder its symlink is placed in.
#
# Without this, every root-level .md lands flat in docs/ and mkdocs-awesome-pages
# orders the nav by ASCII filename, which puts CLAUDE.md and DIRECTION_TYPES ahead
# of "About" for no reason a reader can see.  Only the SYMLINK moves — the source
# files stay at the repo root, so README/CLAUDE cross-references and anything that
# refers to them by path keep working.
#
# To categorize a new root-level doc, add one entry here.  Files not listed keep
# mirroring the repo structure exactly as before.  docs/.pages controls the
# top-level order of the resulting sections.
DOC_CATEGORIES = {
    "cast-config-reference.md": "configuration",
    "sfx-config-reference.md": "configuration",
    "claude-scriptwriter-reference.md": "guides",
    "CLAUDE.md": "internals",
}


def convert_path_to_namespace(path: Path, root: Path) -> str:
    """
    Convert a file path to a Python namespace (e.g., xil-pipeline.eth.loader).

    Args:
        path: Absolute path to Python file or directory
        root: Project root directory

    Returns:
        Dotted namespace string (e.g., "xil-pipeline.eth.loader")
    """
    # Get relative path from root
    relative = path.relative_to(root)

    # Remove .py extension if present
    if relative.suffix == '.py':
        relative = relative.with_suffix('')

    # Convert path to dotted namespace
    namespace = '.'.join(relative.parts)

    return namespace


def should_document_file(file: Path, code_root: Path) -> bool:
    """
    Check if a Python file should be documented.

    Args:
        file: Path to Python file
        code_root: Root directory of the source code (used to compute relative
                   path so the root dir name itself is not subject to filtering)

    Returns:
        True if file should be documented, False otherwise
    """
    name = file.name

    # Skip dunder files (__init__.py, __main__.py)
    if name.startswith('__') and name.endswith('__.py'):
        return False

    # Skip test files
    if name.startswith('test_') or name.endswith('_test.py'):
        return False

    # Skip private modules
    if name.startswith('_'):
        return False

    # Use relative path parts (from code_root) so the root dir name is not
    # subject to filtering — only subdirectory names are checked.
    relative_parts = file.relative_to(code_root).parts

    # Skip archived/legacy directories (matches archive_*, legacy_*, etc.)
    if any(part.startswith('archive') or part.startswith('legacy') for part in relative_parts):
        return False

    # Skip subdirectories with hyphens (not valid Python package names).
    # relative_parts[:-1] = subdirectory parts only (excludes the filename).
    if any('-' in part for part in relative_parts[:-1]):
        return False

    # Skip files with spaces (not valid Python modules)
    if ' ' in name:
        return False

    # Skip specific problematic directories
    skip_dirs = {'data', 'output', '.ruff_cache', '.venv', 'venv', '.git', 'tests', 'docs',
                 'site'}
    if any(part in skip_dirs or part.endswith('_files') or part.startswith('venv')
           for part in relative_parts):
        return False

    return True


def should_copy_markdown_file(file: Path, code_root: Path | None = None) -> bool:
    """
    Check if a markdown file should be copied to docs.

    Args:
        file: Path to markdown file
        code_root: Source code root.  Filtering is done on the path RELATIVE to
            this, so directory names above the checkout are never matched.
            Optional only for backwards compatibility; always pass it.

    Returns:
        True if file should be copied, False otherwise
    """
    name = file.name

    # Skip test files
    if name.startswith('test_') or name.endswith('_test.md'):
        return False

    # Filter on the path relative to code_root — matching absolute path parts
    # rejected everything on Read the Docs, whose checkout lives under
    # /home/docs/checkouts/... where the literal "docs" component hits skip_dirs.
    # "Markdown files: 0" in every RTD build; the site only worked because the
    # symlinks happened to be committed.  should_document_file() carries the same
    # fix for .py files, which is why RTD documented 59 modules but 0 pages.
    if code_root is not None:
        try:
            relative_parts = file.relative_to(code_root).parts
        except ValueError:                      # not under code_root
            relative_parts = file.parts
    else:
        relative_parts = file.parts

    # Skip archived/legacy directories (matches archive_*, legacy_*, etc.)
    if any(part.startswith('archive') or part.startswith('legacy') for part in relative_parts):
        return False

    # Skip specific problematic directories
    # 'docs' is excluded to prevent re-copying generated output back into docs/docs/
    # '.pytest_cache' excluded to prevent copying pytest internals
    skip_dirs = {'data', 'output', '.ruff_cache', '.venv', 'venv', '.git', 'site',
                 'docs', '.pytest_cache', 'scripts'}
    parent_parts = relative_parts[:-1]          # directories only, not the filename
    if any(part in skip_dirs or part.endswith('_files') or part.startswith('venv')
           for part in parent_parts):
        return False

    # Skip files with spaces (can cause issues)
    if ' ' in name:
        return False

    return True


def clean_generated_docs(docs_base: Path, code_root: Path) -> None:
    """
    Clean generated documentation files, preserving hand-written docs.

    Removes:
    - The generated xil-pipeline/ tree
    - Symlinks inside the DOC_CATEGORIES folders (stale links from a source that
      was renamed, deleted, or re-filed under a different category)

    Preserves:
    - docs/.pages — the committed top-level navigation order
    - Real files anywhere, including inside category folders, which hold both
      generated symlinks and hand-written pages side by side
    - Root-level .md files in docs/ (hand-written guides)
    - mkdocs.yml and other config files

    Args:
        docs_base: Base docs directory (e.g., docs/)
        code_root: Source code root (e.g., xil-pipeline/)
    """
    logger.info("Cleaning generated documentation...")

    # Calculate the docs subdirectory that mirrors the code structure
    generated_dir = docs_base / GENERATED_DOCS_DIRNAME

    if generated_dir.exists():
        try:
            shutil.rmtree(generated_dir)
            logger.info(f"  Removed: {generated_dir.relative_to(docs_base.parent)}")
        except OSError as e:
            logger.error(f"  Failed to remove {generated_dir}: {e}")

    # A file newly added to DOC_CATEGORIES leaves its old symlink sitting in
    # docs/ root.  MkDocs then resolves the page from there instead of the
    # category folder, and every relative link inside it points at the wrong
    # neighbours — which surfaces as "target is not found among documentation
    # files" under --strict.
    for name in sorted(DOC_CATEGORIES):
        stale = docs_base / name
        if stale.is_symlink():
            try:
                stale.unlink()
                logger.info(f"  Removed stale link: {stale.relative_to(docs_base.parent)}")
            except OSError as e:
                logger.error(f"  Failed to remove {stale}: {e}")

    # Category folders mix generated symlinks with committed .md files and a
    # committed .pages, so remove ONLY the symlinks.  rmtree here would silently
    # delete hand-written documentation.
    for category in sorted(set(DOC_CATEGORIES.values())):
        category_dir = docs_base / category
        if not category_dir.is_dir():
            continue
        for entry in sorted(category_dir.iterdir()):
            # is_symlink() is true even when the target is gone, which is exactly
            # the stale-link case worth clearing.
            if not entry.is_symlink():
                continue
            try:
                entry.unlink()
                logger.info(f"  Removed link: {entry.relative_to(docs_base.parent)}")
            except OSError as e:
                logger.error(f"  Failed to remove {entry}: {e}")

    logger.info("✅ Cleanup complete!")


def create_pages_file(docs_dir: Path, title: str = None) -> None:
    """
    Create a .pages file for mkdocs-awesome-pages plugin.

    Args:
        docs_dir: Directory to create .pages file in
        title: Optional custom title for the section
    """
    if title is None:
        title = docs_dir.name.replace('_', ' ').title()

    pages_file = docs_dir / '.pages'

    try:
        with open(pages_file, 'w', encoding='utf-8') as f:
            f.write(f"title: {title}\n")
        logger.debug(f"Created {pages_file}")
    except OSError as e:
        logger.error(f"Failed to create {pages_file}: {e}")


def link_sample_audio(project_root: Path, docs_base: Path) -> int:
    """Symlink MP3 files from samples/ into docs/samples/.

    Uses the same relative-symlink approach as link_markdown_files() so the
    links work on ReadTheDocs and other CI environments.  MkDocs copies any
    non-markdown file in the docs/ tree to the site verbatim, making the
    audio files directly downloadable from the published docs site.

    Args:
        project_root: Project root directory.
        docs_base: Base docs directory (e.g., docs/).

    Returns:
        Number of symlinks created.
    """
    samples_dir = project_root / "samples"
    if not samples_dir.exists():
        return 0

    mp3_files = sorted(samples_dir.glob("*.mp3"))
    if not mp3_files:
        return 0

    logger.info("Linking sample audio files from samples/...")
    dest_dir = docs_base / "samples"
    dest_dir.mkdir(parents=True, exist_ok=True)

    linked_count = 0
    for mp3_file in mp3_files:
        try:
            dest_file = dest_dir / mp3_file.name
            if dest_file.exists() or dest_file.is_symlink():
                dest_file.unlink()
            try:
                target = os.path.relpath(str(mp3_file.resolve()), str(dest_file.parent.resolve()))
            except ValueError:
                target = str(mp3_file.resolve())
            dest_file.symlink_to(target)
            logger.info(f"  Linked: samples/{mp3_file.name}")
            linked_count += 1
        except Exception as e:
            logger.error(f"  Failed to link {mp3_file.name}: {e}")

    logger.info(f"✅ Linked {linked_count} sample audio file(s)")
    return linked_count


def link_markdown_files(code_root: Path, docs_base: Path, project_root: Path) -> int:
    """
    Symlink markdown files from source code into the docs directory.

    Preserves directory structure and creates symbolic links so that edits to
    the source .md files are immediately visible in the generated docs site
    without needing to re-run the build script.

    Args:
        code_root: Source code root directory (e.g., xil-pipeline/)
        docs_base: Base docs directory (e.g., docs/)
        project_root: Project root directory

    Returns:
        Number of symlinks created
    """
    logger.info(f"Linking markdown files from {code_root.name}...")

    # Find all markdown files in source code
    md_files = [f for f in code_root.rglob("*.md") if should_copy_markdown_file(f, code_root)]

    linked_count = 0
    for md_file in md_files:
        try:
            # Calculate relative path from project root
            relative_dir = md_file.parent.relative_to(project_root)

            # Root-level docs may be filed under a category instead of landing
            # flat in docs/ (see DOC_CATEGORIES).  Anything else keeps mirroring
            # the repo structure.
            if relative_dir == Path(".") and md_file.name in DOC_CATEGORIES:
                relative_dir = Path(DOC_CATEGORIES[md_file.name])

            docs_dir = docs_base / relative_dir

            # Create destination directory
            docs_dir.mkdir(parents=True, exist_ok=True)

            # Remove stale copy/symlink before creating fresh symlink
            dest_file = docs_dir / md_file.name
            if dest_file.exists() or dest_file.is_symlink():
                dest_file.unlink()

            # Symlink using a relative path so the link works on any machine
            # (absolute paths break on ReadTheDocs and other CI environments)
            try:
                target = os.path.relpath(str(md_file.resolve()), str(dest_file.parent.resolve()))
            except ValueError:
                target = str(md_file.resolve())
            dest_file.symlink_to(target)

            logger.info(f"  Linked: {md_file.relative_to(code_root)}")
            linked_count += 1

        except Exception as e:
            logger.error(f"  Failed to link {md_file}: {e}")

    logger.info(f"✅ Linked {linked_count} markdown file(s)")
    return linked_count


def create_module_doc(file: Path, docs_dir: Path, namespace: str) -> None:
    """
    Create a markdown file for a Python module.

    Args:
        file: Path to Python source file
        docs_dir: Directory to create markdown file in
        namespace: Python namespace (e.g., "xil-pipeline.eth.loader")
    """
    md_file = docs_dir / f"{file.stem}.md"

    try:
        with open(md_file, 'w', encoding='utf-8') as f:
            # Write module title
            title = file.stem.replace('_', ' ').title()
            f.write(f"# {title}\n\n")

            # Write mkdocstrings directive
            f.write(f"::: {namespace}\n")
            f.write("    options:\n")
            f.write("      show_root_heading: true\n")
            f.write("      show_source: true\n")
            f.write("      members_order: source\n")

        logger.info(f"Created {md_file.relative_to(docs_dir.parent.parent)}")
    except OSError as e:
        logger.error(f"Failed to create {md_file}: {e}")


def main() -> None:
    """Generate MkDocs documentation from xil-pipeline Python files."""
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='Generate MkDocs documentation from Python source files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default behavior - clean and rebuild all docs
  python build_docs.py

  # Skip cleaning (preserve existing generated docs)
  python build_docs.py --no-clear

  # Verbose output
  python build_docs.py --verbose
        """
    )
    parser.add_argument(
        '--no-clear',
        action='store_true',
        help='Skip cleaning generated docs (default: clean before build)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging (DEBUG level)'
    )
    args = parser.parse_args()

    # Set logging level
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # Setup paths — script lives at <project_root>/docs/, so parent.parent = project_root
    project_root = Path(__file__).parent.parent
    code_root = project_root          # Bug fix: source files live in project_root, not a subdir
    docs_base = project_root / "docs"

    if not code_root.exists():
        logger.error(f"Code directory not found: {code_root}")
        return

    # Clean generated docs (unless --no-clear specified)
    if not args.no_clear:
        clean_generated_docs(docs_base, code_root)
    else:
        logger.info("Skipping cleanup (--no-clear specified)")

    logger.info(f"Scanning {code_root} for Python files...")

    # Find all Python files
    py_files = [f for f in code_root.rglob("*.py") if should_document_file(f, code_root)]
    logger.info(f"Found {len(py_files)} files to document")

    # Find all directories containing Python files
    directories = set()
    for py_file in py_files:
        directories.add(py_file.parent)

    logger.info(f"Creating documentation structure in {docs_base}...")

    # Create docs directory structure
    # Generated docs go into docs/<GENERATED_DOCS_DIRNAME>/ so clean_generated_docs
    # can remove them cleanly without touching hand-written docs — and so the path
    # does not depend on what the checkout directory happens to be called.
    for directory in sorted(directories):
        relative_dir = Path(GENERATED_DOCS_DIRNAME) / directory.relative_to(code_root)
        docs_dir = docs_base / relative_dir

        # Create directory
        docs_dir.mkdir(parents=True, exist_ok=True)

        # Create .pages file
        if directory == code_root:
            create_pages_file(docs_dir, title="xil-pipeline Code Reference")
        else:
            create_pages_file(docs_dir)

        # Get Python files in this directory (non-recursive)
        files_in_dir = [f for f in directory.glob("*.py") if should_document_file(f, code_root)]

        # Create markdown files for each module
        for py_file in sorted(files_in_dir):
            namespace = convert_path_to_namespace(py_file, project_root)
            create_module_doc(py_file, docs_dir, namespace)

    # Symlink markdown files from source to docs
    logger.info("")  # Blank line for readability
    md_count = link_markdown_files(code_root, docs_base, project_root)

    # Symlink sample audio files into docs/samples/
    logger.info("")  # Blank line for readability
    audio_count = link_sample_audio(project_root, docs_base)

    logger.info("")  # Blank line for readability
    logger.info("✅ Documentation generation complete!")
    logger.info(f"   Python modules: {len(py_files)}")
    logger.info(f"   Markdown files: {md_count}")
    logger.info(f"   Sample audio  : {audio_count}")


if __name__ == "__main__":
    main()
