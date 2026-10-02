#!/usr/bin/env bash
# md_to_docx.sh: markdown brief to branded Word (spec 010 US4).
#
# Runs Pandoc as a separate program with templates/reference.docx (Hawthorn
# styles, built by scripts/build_report_templates.py). Pandoc is GPL and is
# never imported or linked: running it as a command-line tool keeps its
# license off repo code (configs/integrations.json, entry pandoc).
#
# Refuses input that contains an em-dash (U+2014). When DOCX_VALIDATE names the
# docx skill's validate.py, the output must pass it with --original set to the
# reference document.
#
# Reads   <input.md>, templates/reference.docx
# Writes  outputs/briefs/<name>.docx (or the path given)
#
# Usage   scripts/md_to_docx.sh deliverables/<path>.md [outputs/briefs/<name>.docx]
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
reference="$root/templates/reference.docx"

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
  echo "usage: scripts/md_to_docx.sh INPUT.md [OUTPUT.docx]" >&2
  exit 2
fi
input="$1"
name="$(basename "${input%.md}")"
output="${2:-$root/outputs/briefs/$name.docx}"

if ! command -v pandoc >/dev/null 2>&1; then
  echo "pandoc is not installed. Install it as a command-line program:" >&2
  echo "  brew install pandoc            # macOS" >&2
  echo "  sudo apt-get install -y pandoc # Debian, Ubuntu" >&2
  exit 1
fi
if [ ! -f "$input" ]; then
  echo "no such file: $input" >&2
  exit 2
fi
if grep -n $'\xe2\x80\x94' "$input" >&2; then
  echo "em-dash in $input (lines above); use a comma, colon or parentheses" >&2
  exit 1
fi

mkdir -p "$(dirname "$output")"
pandoc "$input" --from gfm --to docx --reference-doc "$reference" --output "$output"

if [ -n "${DOCX_VALIDATE:-}" ]; then
  python "$DOCX_VALIDATE" "$output" --original "$reference"
fi
echo "wrote $output"
