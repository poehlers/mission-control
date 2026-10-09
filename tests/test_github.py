"""Remote URL -> owner/repo, the key that matches local clones to GitHub repos."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from collectors.github import full_name

assert full_name("https://github.com/poehlers/kadd.git\n") == "poehlers/kadd"  # real KADD remote
assert full_name("https://github.com/poehlers/MIMS.git") == "poehlers/mims"
assert full_name("git@github.com:poehlers/DenPIE.git") == "poehlers/denpie"
assert full_name("ssh://git@github.com/poehlers/GW-Thesis") == "poehlers/gw-thesis"
assert full_name("https://github.com/poehlers/pythondaq/") == "poehlers/pythondaq"
assert full_name("") is None and full_name("https://gitlab.com/x/y.git") is None
print("ok test_github")
