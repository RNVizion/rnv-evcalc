cd "$(git rev-parse --show-toplevel)"
sed -i 's|python3 intake\.py|python3 scripts/intake.py|g' scripts/intake.py
sed -i 's|intake\.py writes|scripts/intake.py writes|g' flag.json overlay.html
sed -i 's|between intake\.py|between scripts/intake.py|g' flag.json
sed -i 's|scripts/scripts/|scripts/|g' scripts/*.py flag.json overlay.html
python3 -c "import json; json.load(open('flag.json')); print('flag.json parses')"
echo "== USAGE LINES =="
git grep -nE "python3 (scripts/)?(evcalc|intake)\.py"
echo "== DOC REFS =="
git grep -nE "(evcalc|intake|shipping)\.py" -- flag.json overlay.html README.md
