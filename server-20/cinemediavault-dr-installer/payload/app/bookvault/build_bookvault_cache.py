#!/usr/bin/env python3
from pathlib import Path
import os
import sys

sys.path.insert(0, "/home/jnicolas/bookvault")
from bookvault_server import BookIndex

root = Path(os.environ.get("BOOKVAULT_ROOT", "/media/jnicolas/Expansion/Books"))
index = BookIndex(root)
index.scan(force=True)
print(f"Indexed {len(index.books)} books from {root}")
