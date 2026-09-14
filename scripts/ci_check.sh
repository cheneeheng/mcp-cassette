#!/usr/bin/env bash
# The packaged Action's logic (action.yml), kept in a script so it can be tested
# without a runner. Lints each cassette against its merge-base counterpart (R002) and,
# with --checks diff, compares tool surfaces too.
#
# Exit codes: 0 clean, 2 usage error or unresolvable merge-base, 4 lint failed,
# 5 tool surfaces differ (diff check). The most severe code wins: 2 > 4 > 5 > 0.
set -uo pipefail

usage() {
  cat >&2 <<'EOF'
usage: ci_check.sh --baseline-ref REF [--checks lint|diff|lint,diff]
                   [--pattern-pack PATH]... [--require-redaction NAME]
                   [--fail-on error|warning] [--report-dir DIR] CASSETTE...
EOF
}

die() {
  echo "ci_check.sh: $1" >&2
  if [ "${GITHUB_ACTIONS-}" = true ]; then
    echo "::error::mcp-cassette: $1"
  fi
  exit 2
}

baseline_ref=""
checks="lint"
require_redaction=""
fail_on=""
report_dir=""
packs=()
cassettes=()
while [ $# -gt 0 ]; do
  case "$1" in
    --baseline-ref | --checks | --pattern-pack | --require-redaction | --fail-on | --report-dir)
      [ $# -ge 2 ] || { usage; exit 2; }
      case "$1" in
        --baseline-ref) baseline_ref=$2 ;;
        --checks) checks=$2 ;;
        --pattern-pack) packs+=("$2") ;;
        --require-redaction) require_redaction=$2 ;;
        --fail-on) fail_on=$2 ;;
        --report-dir) report_dir=$2 ;;
      esac
      shift 2
      ;;
    --) shift; cassettes+=("$@"); break ;;
    -*) usage; die "unknown option $1" ;;
    *) cassettes+=("$1"); shift ;;
  esac
done

do_lint=""
do_diff=""
case "$checks" in
  lint) do_lint=1 ;;
  diff) do_diff=1 ;;
  lint,diff | diff,lint) do_lint=1; do_diff=1 ;;
  *) die "--checks takes lint, diff, or lint,diff (got '$checks')" ;;
esac
# A glob that matched nothing is a misconfiguration; passing it would fail open.
[ ${#cassettes[@]} -gt 0 ] || die "no cassettes to check; set the 'cassettes' input to a glob that matches your committed cassettes"
[ -n "$baseline_ref" ] || die "no --baseline-ref; outside a pull_request event, set the 'baseline-ref' input explicitly"

# A missing merge-base fails the job and never silently skips: without a baseline R002
# stops gating, and a security gate that fails open reports success.
if ! base=$(git merge-base "$baseline_ref" HEAD 2>/dev/null); then
  die "cannot resolve a merge-base between $baseline_ref and HEAD: this checkout lacks the shared history. Set 'fetch-depth: 0' on actions/checkout."
fi

if [ -z "$report_dir" ]; then
  report_dir=$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/mcp-cassette.XXXXXX")
fi
mkdir -p "$report_dir/baseline"

status=0
findings=0
rows=()

keep_worst() {
  case "$1" in
    0) ;;
    4) [ "$status" = 2 ] || status=4 ;;
    5) [ "$status" = 0 ] && status=5 ;;
    *) status=2 ;;
  esac
}

decode() {
  local s=$1
  s=${s//%0A/ }
  s=${s//%0D/}
  s=${s//%3A/:}
  s=${s//%2C/,}
  s=${s//%25/%}
  s=${s//|/\\|}
  printf '%s' "$s"
}

for cassette in "${cassettes[@]}"; do
  safe=${cassette//[\/\\:]/_}
  baseline="$report_dir/baseline/$safe"
  if git cat-file -e "$base:./$cassette" 2>/dev/null; then
    git show "$base:./$cassette" >"$baseline"
    have_baseline=1
  else
    echo "note: $cassette is new since $baseline_ref, so there is no baseline to compare; skipped the baseline checks"
    have_baseline=""
  fi

  if [ -n "$do_lint" ]; then
    args=(lint "$cassette" --format json --annotate github)
    [ -n "$have_baseline" ] && args+=(--baseline "$baseline")
    for pack in ${packs[@]+"${packs[@]}"}; do
      args+=(--pattern-pack "$pack")
    done
    [ -n "$require_redaction" ] && args+=(--require-redaction "$require_redaction")
    [ -n "$fail_on" ] && args+=(--fail-on "$fail_on")
    out=$(mcp-cassette "${args[@]}")
    code=$?
    out=${out//$'\r'/}
    # Workflow commands render as annotations; everything else is the JSON report.
    printf '%s\n' "$out" | grep -v '^::' >"$report_dir/lint-$safe.json" || true
    while IFS= read -r line; do
      [ -n "$line" ] || continue
      echo "$line"
      findings=$((findings + 1))
      level=${line#::}
      level=${level%% *}
      props=${line#::* }
      message=${props#*::}
      props=${props%%::*}
      rule=${props##*title=}
      rows+=("| $level | $(decode "$cassette") | $(decode "$rule") | $(decode "$message") |")
    done < <(printf '%s\n' "$out" | grep '^::' || true)
    echo "lint $cassette: exit $code"
    keep_worst "$code"
  fi

  if [ -n "$do_diff" ] && [ -n "$have_baseline" ]; then
    mcp-cassette diff "$baseline" "$cassette" --tools-only >"$report_dir/diff-$safe.txt"
    code=$?
    cat "$report_dir/diff-$safe.txt"
    if [ "$code" = 5 ]; then
      echo "diff $cassette: tool surfaces differ from $baseline_ref"
      if [ "${GITHUB_ACTIONS-}" = true ]; then
        echo "::error file=$cassette,title=diff::tool surfaces differ from $baseline_ref"
      fi
      rows+=("| error | $cassette | diff | tool surfaces differ from $baseline_ref |")
    fi
    keep_worst "$code"
  fi
done

if [ -n "${GITHUB_STEP_SUMMARY-}" ]; then
  {
    echo "### mcp-cassette: ${#cassettes[@]} cassette(s), exit $status"
    echo
    if [ ${#rows[@]} -gt 0 ]; then
      echo "| Severity | Cassette | Rule | Finding |"
      echo "|---|---|---|---|"
      printf '%s\n' "${rows[@]}"
    else
      echo "No findings."
    fi
  } >>"$GITHUB_STEP_SUMMARY"
fi
if [ -n "${GITHUB_OUTPUT-}" ]; then
  {
    echo "findings=$findings"
    echo "report-path=$report_dir"
    echo "exit-code=$status"
  } >>"$GITHUB_OUTPUT"
fi

echo "mcp-cassette: ${#cassettes[@]} cassette(s), $findings finding(s), exit $status; reports in $report_dir"
exit "$status"
