cd "$(git rev-parse --show-toplevel)"
mkdir -p scripts
git mv evcalc.py intake.py shipping.py scripts/
sed -i 's|python3 evcalc\.py|python3 scripts/evcalc.py|g' scripts/evcalc.py
echo "== REMAINING REFS =="
git grep -n "evcalc\.py\|intake\.py\|shipping\.py"
echo "== USAGE HEADERS =="
head -20 scripts/intake.py
