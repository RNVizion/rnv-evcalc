cd "$(git rev-parse --show-toplevel)"
git status --short
echo "== TREE =="; git ls-files
echo "== PY PATH READS =="
git grep -nE "open\(|__file__|Path\(|json\.load|\.json|cards/" -- "*.py"
echo "== REFS ELSEWHERE =="
git grep -nE "\.py|overlay\.html|flag\.json" -- "*.md" "*.html" "*.yml" "*.yaml" "*.json" "*.toml"
