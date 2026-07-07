# jump to the real repo root (the folder with .git), wherever you are
cd "$(git rev-parse --show-toplevel)"

# confirm the nesting before touching anything
ls -la          # expect: .git  and a  rnv-evcalc/  folder
ls rnv-evcalc   # expect: evcalc.py  fees.json  cards/

# move the contents up one level, then drop the empty shell
mv rnv-evcalc/* .
rmdir rnv-evcalc

# verify
ls -la          # evcalc.py, fees.json, cards/ now sit at root
